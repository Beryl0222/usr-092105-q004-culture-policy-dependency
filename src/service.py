"""文化政策依赖协调服务。

接收各部门按公共信封约定发来的后继事件，把政策条款的版本、发布与
生效时间、地区与主体范围、前置材料、引用依赖、冲突意见、受理事项
及最终决定回放成可回溯的关系网络。迟到事件按真实发生时间归位后
整体重放，复核标记只传播到真实依赖项；本服务不作法律解释。
"""
from __future__ import annotations

from src.models import (
    CaseState,
    ClauseChange,
    ClauseState,
    Decision,
    ReviewMark,
    parse_ts,
    scope_matches,
)
from src.validator import validate_event

_POLICY_ACTIONS = {"publish", "amend", "postpone", "repeal", "transition"}
_EDGE_ACTIONS = {"declare", "retract"}
_EDGE_KINDS = {"cites", "requires", "preempts"}
_CASE_ACTIONS = {"accept", "decide", "close"}
_VISIBILITIES = {"public", "internal"}


class CoordinationService:
    """事件溯源的协调服务：任何时刻的状态都可由事件日志完整回放。"""

    def __init__(self) -> None:
        self._events: dict[str, dict] = {}
        self._reset()

    def _reset(self) -> None:
        self.clauses: dict[str, ClauseState] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}
        self.cases: dict[str, CaseState] = {}
        self.opinions: dict[str, dict] = {}
        self.conflicts: dict[str, dict] = {}
        self._changes: list[ClauseChange] = []
        self._postponements: list[dict] = []

    # ---- 接入 ----

    def ingest(self, event: dict) -> list[str]:
        """接收一条部门事件；返回可直接展示给接入方的中文错误，空列表表示成功。

        来源系统重试时沿用原 event_id，重复投递幂等。
        """
        errors = validate_event(event) + self._validate_payload(event)
        if errors:
            return errors
        event_id = event["event_id"]
        if event_id in self._events:
            return []
        self._events[event_id] = event
        self._rebuild()
        return []

    def clone(self) -> "CoordinationService":
        """复制当前事件日志并重放，用于发布前模拟，不影响正式状态。"""
        other = CoordinationService()
        other._events = dict(self._events)
        other._rebuild()
        return other

    def _validate_payload(self, event: dict) -> list[str]:
        errors: list[str] = []
        try:
            parse_ts(event["occurred_at"])
        except (ValueError, TypeError):
            errors.append("occurred_at 不是合法时间")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return errors + ["缺少字段：payload"]
        event_type = event["event_type"]
        if event_type == "POLICY_PUBLISHED":
            action = payload.get("action")
            if action not in _POLICY_ACTIONS:
                errors.append("payload.action 必须是 publish/amend/postpone/repeal/transition")
            elif action in {"publish", "amend"}:
                for key in ("title", "owning_agency", "effective_from"):
                    if key not in payload:
                        errors.append(f"payload 缺少字段：{key}")
            elif action == "postpone" and "new_effective_from" not in payload:
                errors.append("payload 缺少字段：new_effective_from")
            elif action == "repeal" and "repealed_at" not in payload:
                errors.append("payload 缺少字段：repealed_at")
            elif action == "transition":
                transition = payload.get("transition")
                if not isinstance(transition, dict) or "cutoff" not in transition:
                    errors.append("payload.transition 缺少字段：cutoff")
        elif event_type == "DEPENDENCY_DECLARED":
            if payload.get("action") not in _EDGE_ACTIONS:
                errors.append("payload.action 必须是 declare/retract")
            for key in ("from_clause", "to_clause"):
                if key not in payload:
                    errors.append(f"payload 缺少字段：{key}")
            if payload.get("action") == "declare" and payload.get("kind") not in _EDGE_KINDS:
                errors.append("payload.kind 必须是 cites/requires/preempts")
        elif event_type == "OPINION_ISSUED":
            for key in ("agency", "clause_id", "text"):
                if key not in payload:
                    errors.append(f"payload 缺少字段：{key}")
            if payload.get("visibility", "public") not in _VISIBILITIES:
                errors.append("payload.visibility 必须是 public/internal")
        elif event_type == "CONFLICT_RAISED":
            for key in ("clause_id", "agencies", "topic"):
                if key not in payload:
                    errors.append(f"payload 缺少字段：{key}")
            if "agencies" in payload and not isinstance(payload["agencies"], list):
                errors.append("payload.agencies 必须是部门列表")
        elif event_type == "CASE_REVIEWED":
            action = payload.get("action")
            if action not in _CASE_ACTIONS:
                errors.append("payload.action 必须是 accept/decide/close")
            elif action == "accept":
                for key in ("applicant", "region", "subject", "clause_ids", "accepted_at"):
                    if key not in payload:
                        errors.append(f"payload 缺少字段：{key}")
                if "clause_ids" in payload and not isinstance(payload["clause_ids"], list):
                    errors.append("payload.clause_ids 必须是条款标识列表")
            elif action == "decide":
                for key in ("decision", "decided_by", "decided_at"):
                    if key not in payload:
                        errors.append(f"payload 缺少字段：{key}")
        return errors

    # ---- 回放 ----

    def _rebuild(self) -> None:
        """按真实发生时间重放全部事件，迟到版本自然归位。"""
        self._reset()
        ordered = sorted(
            self._events.values(),
            key=lambda e: (parse_ts(e["occurred_at"]), e["version"], e["event_id"]),
        )
        for event in ordered:
            self._apply(event)
        self._propagate()

    def _apply(self, event: dict) -> None:
        handler = {
            "POLICY_PUBLISHED": self._apply_policy,
            "DEPENDENCY_DECLARED": self._apply_edge,
            "OPINION_ISSUED": self._apply_opinion,
            "CONFLICT_RAISED": self._apply_conflict,
            "CASE_REVIEWED": self._apply_case,
        }[event["event_type"]]
        handler(event)

    def _apply_policy(self, event: dict) -> None:
        payload = event["payload"]
        action = payload["action"]
        clause_id = event["aggregate_id"]
        clause = self.clauses.setdefault(clause_id, ClauseState(clause_id=clause_id))
        if action in {"publish", "amend"}:
            old_regions = list(clause.region_scope)
            old_subjects = list(clause.subject_scope)
            clause.version = event["version"]
            clause.title = payload["title"]
            clause.owning_agency = payload["owning_agency"]
            clause.region_scope = list(payload.get("region_scope", []))
            clause.subject_scope = list(payload.get("subject_scope", []))
            clause.materials = list(payload.get("materials", []))
            clause.published_at = payload.get("published_at", event["occurred_at"])
            clause.effective_from = payload["effective_from"]
            clause.repealed_at = None
            clause.replaced_by = None
            clause.history.append(
                {
                    "version": clause.version,
                    "title": clause.title,
                    "owning_agency": clause.owning_agency,
                    "region_scope": list(clause.region_scope),
                    "subject_scope": list(clause.subject_scope),
                    "materials": list(clause.materials),
                    "published_at": clause.published_at,
                    "effective_from": clause.effective_from,
                    "event_id": event["event_id"],
                    "occurred_at": event["occurred_at"],
                }
            )
            if action == "amend":
                self._changes.append(
                    ClauseChange(
                        clause_id=clause_id,
                        kind="amend",
                        occurred_at=event["occurred_at"],
                        event_id=event["event_id"],
                        version=event["version"],
                        region_scope=sorted(set(old_regions) | set(clause.region_scope)),
                        subject_scope=sorted(set(old_subjects) | set(clause.subject_scope)),
                    )
                )
        elif action == "postpone":
            clause.effective_from = payload["new_effective_from"]
            self._postponements.append(
                {
                    "clause_id": clause_id,
                    "new_effective_from": payload["new_effective_from"],
                    "occurred_at": event["occurred_at"],
                    "event_id": event["event_id"],
                    "region_scope": list(clause.region_scope),
                    "subject_scope": list(clause.subject_scope),
                }
            )
        elif action == "repeal":
            clause.repealed_at = payload["repealed_at"]
            clause.replaced_by = payload.get("replaced_by")
            self._changes.append(
                ClauseChange(
                    clause_id=clause_id,
                    kind="repeal",
                    occurred_at=event["occurred_at"],
                    event_id=event["event_id"],
                    version=clause.version,
                    region_scope=list(clause.region_scope),
                    subject_scope=list(clause.subject_scope),
                )
            )
        elif action == "transition":
            clause.transitions.append(
                {
                    "cutoff": payload["transition"]["cutoff"],
                    "note": payload["transition"].get("note", ""),
                    "event_id": event["event_id"],
                }
            )

    def _apply_edge(self, event: dict) -> None:
        payload = event["payload"]
        key = (payload["from_clause"], payload["to_clause"], payload.get("kind", "cites"))
        if payload["action"] == "declare":
            self.edges[key] = {
                "edge_id": event["aggregate_id"],
                "note": payload.get("note", ""),
                "declared_at": event["occurred_at"],
            }
        else:
            self.edges.pop(key, None)

    def _apply_opinion(self, event: dict) -> None:
        payload = event["payload"]
        self.opinions[event["aggregate_id"]] = {
            "agency": payload["agency"],
            "clause_id": payload["clause_id"],
            "text": payload["text"],
            "visibility": payload.get("visibility", "public"),
            "issued_at": event["occurred_at"],
        }

    def _apply_conflict(self, event: dict) -> None:
        payload = event["payload"]
        conflict = self.conflicts.setdefault(
            event["aggregate_id"],
            {
                "clause_id": payload["clause_id"],
                "agencies": list(payload["agencies"]),
                "topic": payload["topic"],
                "status": "open",
                "resolution": "",
            },
        )
        if payload.get("action") == "resolve":
            conflict["status"] = "resolved"
            conflict["resolution"] = payload.get("resolution", "")

    def _apply_case(self, event: dict) -> None:
        payload = event["payload"]
        action = payload["action"]
        case = self.cases.setdefault(event["aggregate_id"], CaseState(case_id=event["aggregate_id"]))
        if action == "accept":
            case.applicant = payload["applicant"]
            case.region = payload["region"]
            case.subject = payload["subject"]
            case.clause_ids = list(payload["clause_ids"])
            case.materials_provided = list(payload.get("materials_provided", []))
            case.accepted_at = payload["accepted_at"]
            case.status = "accepted"
        elif action == "decide":
            basis = {
                clause_id: self.clauses[clause_id].version
                for clause_id in self._closure(case.clause_ids)
                if clause_id in self.clauses
            }
            case.decision = Decision(
                decision=payload["decision"],
                decided_by=payload["decided_by"],
                decided_at=payload["decided_at"],
                basis=basis,
            )
            case.status = "decided"
        elif action == "close":
            case.status = "closed"

    # ---- 传播 ----

    def _closure(self, roots: list[str]) -> set[str]:
        """沿依赖边求传递闭包：事项直接依据的条款加上被引用的条款。"""
        seen = set(roots)
        stack = list(roots)
        while stack:
            current = stack.pop()
            for from_clause, to_clause, _kind in self.edges:
                if from_clause == current and to_clause not in seen:
                    seen.add(to_clause)
                    stack.append(to_clause)
        return seen

    def _matching_transition(self, clause: ClauseState | None, accepted_at: str) -> dict | None:
        if clause is None:
            return None
        for transition in clause.transitions:
            if parse_ts(accepted_at) < parse_ts(transition["cutoff"]):
                return transition
        return None

    def _propagate(self) -> None:
        """复核标记只落在真实依赖项上：依赖闭包、地区主体范围、时间顺序三者同时满足。"""
        for case in self.cases.values():
            closure = self._closure(case.clause_ids)
            case.closure = sorted(closure)
            if not case.accepted_at:
                continue
            accepted = parse_ts(case.accepted_at)
            decided_at = parse_ts(case.decision.decided_at) if case.decision else None
            for change in self._changes:
                if change.clause_id not in closure:
                    continue
                if not (
                    scope_matches(change.region_scope, case.region)
                    and scope_matches(change.subject_scope, case.subject)
                ):
                    continue
                occurred = parse_ts(change.occurred_at)
                if accepted >= occurred:
                    continue
                transition = self._matching_transition(self.clauses.get(change.clause_id), case.accepted_at)
                if transition is not None:
                    case.reminders.append(
                        {
                            "clause_id": change.clause_id,
                            "kind": "transition",
                            "text": f"过渡安排：{transition['note']}（截止 {transition['cutoff']}），按旧规则办理",
                            "event_id": change.event_id,
                        }
                    )
                    continue
                title = self.clauses[change.clause_id].title if change.clause_id in self.clauses else change.clause_id
                if change.kind == "amend":
                    reason = f"依据条款《{title}》已修订为第{change.version}版，需复核"
                else:
                    reason = f"依据条款《{title}》已废止，需复核"
                if decided_at is not None:
                    if decided_at < occurred:
                        # 已生效决定保留原依据，仅标记待复核
                        case.decision.pending_review = True
                        case.review_marks.append(
                            ReviewMark(change.clause_id, change.version, "decision", reason, change.event_id, change.occurred_at)
                        )
                else:
                    case.review_marks.append(
                        ReviewMark(change.clause_id, change.version, "case", reason, change.event_id, change.occurred_at)
                    )
            for postponement in self._postponements:
                if postponement["clause_id"] not in closure:
                    continue
                if not (
                    scope_matches(postponement["region_scope"], case.region)
                    and scope_matches(postponement["subject_scope"], case.subject)
                ):
                    continue
                occurred = parse_ts(postponement["occurred_at"])
                if accepted >= occurred or (decided_at is not None and decided_at < occurred):
                    continue
                title = (
                    self.clauses[postponement["clause_id"]].title
                    if postponement["clause_id"] in self.clauses
                    else postponement["clause_id"]
                )
                case.reminders.append(
                    {
                        "clause_id": postponement["clause_id"],
                        "kind": "postpone",
                        "text": f"《{title}》生效时间延期至 {postponement['new_effective_from']}，受理口径以原规则为准",
                        "event_id": postponement["event_id"],
                    }
                )

    # ---- 查询 ----

    def review_queue(self) -> list[dict]:
        """当前待复核清单：在办事项与已生效决定分开列出。"""
        queue = []
        for case in sorted(self.cases.values(), key=lambda c: c.case_id):
            case_marks = [m for m in case.review_marks if m.kind == "case"]
            decision_marks = [m for m in case.review_marks if m.kind == "decision"]
            if case_marks or decision_marks:
                queue.append(
                    {
                        "case_id": case.case_id,
                        "case_marks": case_marks,
                        "decision_pending_review": bool(decision_marks),
                        "decision_basis": dict(case.decision.basis) if case.decision else None,
                    }
                )
        return queue
