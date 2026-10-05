"""传播引擎：迟到版本、延期、废止、过渡安排只沿真实依赖边传播。

输入 State（事实事件重放结果），输出每个受影响办件的结构化结论，并生成可安全重建的
协调派生事件（CASE_TRANSITION_APPLIED / CASE_REVIEWED，origin=coordinator）。

关键规则：
- 只沿 CASE_CLAUSE 边传播；CLAUSE_CLAUSE 仅用于关系网络展示。
- 过渡安排按 scope（地区/主体/事项）与受理时间过滤，不匹配的办件完全不受影响。
- 在办件：旧版有效 -> 无传播；适用过渡 -> 按过渡规则；废止且无过渡 -> 进入复核。
- 已决定：决定快照原依据永不改写；仅当过渡/废止明确要求 REVIEW 时才待复核，
  否则保留原依据、免复核（GRANDFATHER）。
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .timeutil import parse_dt, scope_match

_NOW = lambda: datetime.now(timezone.utc)


@dataclass
class Impact:
    case_id: str
    edge_id: str
    clause_id: str
    old_version: int
    kind: str  # TRANSITION / REVIEW / NONE
    action: str
    reason: str
    trigger_event_id: str
    review_key: str | None = None
    successor_version: int | None = None
    transition: dict | None = None
    repeal: dict | None = None
    missing_materials: list = field(default_factory=list)
    review_resolved: bool = False
    effective_at: str | None = None  # 后果实际激活时间（未来废止 -> 待生效）

    @property
    def is_real(self) -> bool:
        return self.kind != "NONE"

    @property
    def scheduled(self) -> bool:
        """后果已确定但尚未到激活时点。"""
        return bool(self.effective_at and parse_dt(self.effective_at) > _NOW())


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def assess_case_edges(state) -> list[Impact]:
    """对全部 CASE_CLAUSE 依赖边评估当前应有的传播结论（幂等、纯函数）。"""
    impacts: list[Impact] = []
    for edge in state.edges.values():
        if edge["kind"] != "CASE_CLAUSE" or not edge.get("active", True):
            continue
        case = state.cases.get(edge["case_id"])
        if case is None:
            continue
        ver = state.clause_version(edge["clause_id"], edge["clause_version"])
        if ver is None:
            continue
        impact = _assess_one(state, edge, case, ver)
        if impact:
            impacts.append(impact)
    return impacts


def _assess_one(state, edge, case, ver) -> Impact | None:
    base = dict(
        case_id=case["case_id"], edge_id=edge["edge_id"],
        clause_id=edge["clause_id"], old_version=ver["version"],
    )

    # 1) 过渡安排：取最新一条同时满足 scope 与受理时限的
    transition = _applicable_transition(case, ver)
    decided = case["status"] == "DECIDED" and case.get("decision")

    if transition:
        if decided:
            if transition.get("decisions_policy") == "REVIEW":
                key = f"TRANSITION:{transition['event_id']}"
                return Impact(
                    **base, kind="REVIEW", action="REVIEW_DECIDED_BASIS",
                    reason="过渡安排要求既有决定在切换到新规则前复核",
                    trigger_event_id=transition["event_id"], review_key=key,
                    transition=transition,
                    review_resolved=_resolved(case, key),
                )
            return None  # GRANDFATHER：原依据保留，不复核
        key = f"TRANSITION:{transition['event_id']}"
        if _resolved(case, key) or _external_review(case, key):
            return None
        if transition["rule"] == "CONTINUE_OLD_RULES":
            return Impact(**base, kind="TRANSITION", action="CONTINUE_OLD_RULES",
                          reason="过渡条款：受理时间符合条件，继续按原办法办理",
                          trigger_event_id=transition["event_id"], transition=transition)
        if transition["rule"] == "SUPPLEMENT_UNDER_NEW":
            missing = _missing_under_successor(state, edge, case, transition.get("successor_version"))
            return Impact(**base, kind="TRANSITION", action="SUPPLEMENT_UNDER_NEW",
                          reason="过渡条款：按新版本补齐材料后继续办理",
                          trigger_event_id=transition["event_id"], review_key=key,
                          successor_version=transition.get("successor_version"),
                          transition=transition, missing_materials=missing)
        if transition["rule"] == "REVIEW_BEFORE_DATE":
            return Impact(**base, kind="REVIEW", action="TRANSITION_REVIEW_BEFORE_DATE",
                          reason=f"过渡条款：须在 {transition.get('cutoff_date')} 前完成复核",
                          trigger_event_id=transition["event_id"], review_key=key,
                          transition=transition, review_resolved=_resolved(case, key))
        return None

    # 2) 废止（且无过渡安排）。废止未到生效时点时记为“待生效”，供发布前预警。
    repeal = ver.get("repeal")
    if repeal:
        if not scope_match(repeal.get("scope"), region=case["region"],
                           subject=case.get("subject_type") or "", matter_code=case["matter_code"]):
            return None  # 废止范围不覆盖该办件：不传播
        key = f"REPEAL:{repeal['event_id']}"
        successor = repeal.get("successor") or {}
        succ_ver = successor.get("version")
        repeal_at = repeal.get("at")
        not_yet = bool(repeal_at and parse_dt(repeal_at) > _NOW())
        if decided:
            if repeal.get("decisions_review") == "REVIEW":
                return Impact(**base, kind="REVIEW", action="REVIEW_DECIDED_BASIS",
                              reason="原依据已废止，且明确要求既有决定复核",
                              trigger_event_id=repeal["event_id"], review_key=key,
                              successor_version=succ_ver, repeal=repeal,
                              review_resolved=_resolved(case, key), effective_at=repeal_at)
            return None  # GRANDFATHER：已生效决定保留原依据、免复核
        if _resolved(case, key) or _external_review(case, key):
            return None
        if succ_ver is not None:
            missing = _missing_under_successor(state, edge, case, succ_ver)
            return Impact(**base, kind="REVIEW",
                          action="REVIEW_NEW_VERSION",
                          reason=("原依据将于废止生效后失效，存在新版本，需重新适配并补齐材料"
                                  if not_yet else "原依据已废止，存在新版本，需重新适配并补齐材料"),
                          trigger_event_id=repeal["event_id"], review_key=key,
                          successor_version=succ_ver, repeal=repeal, missing_materials=missing,
                          effective_at=repeal_at)
        return Impact(**base, kind="REVIEW", action="REVIEW_NO_SUCCESSOR",
                      reason="原依据已废止且无后继条款，需责任部门明确处理路径",
                      trigger_event_id=repeal["event_id"], review_key=key,
                      repeal=repeal, review_resolved=_resolved(case, key), effective_at=repeal_at)

    # 3) 旧版仍有效（含仅延期、仅发布新版本）：不传播
    return None


def _applicable_transition(case, ver):
    candidates = []
    for t in ver.get("transitions", []):
        if not scope_match(t.get("scope"), region=case["region"],
                           subject=case.get("subject_type") or "", matter_code=case["matter_code"]):
            continue
        if t.get("accepted_before") and parse_dt(case["accepted_at"]) > parse_dt(t["accepted_before"]):
            continue
        candidates.append(t)
    if not candidates:
        return None
    candidates.sort(key=lambda t: parse_dt(t["at"]))
    return candidates[-1]


def _missing_under_successor(state, edge, case, succ_ver) -> list[dict]:
    """新版本相对依赖声明时新增、且办件尚未提交的材料，附责任部门。"""
    if succ_ver is None:
        return []
    succ = state.clause_version(edge["clause_id"], int(succ_ver))
    if succ is None:
        return []
    declared = set(edge.get("required_material_codes") or [])
    out = []
    for m in succ.get("required_materials", []):
        code = m["code"]
        received = case["materials"].get(code, {}).get("status") == "RECEIVED"
        if code not in declared and not received:
            out.append({
                "code": code, "name": m.get("name"),
                "requirement": m.get("requirement"),
                "responsible_agency": m.get("responsible_agency"),
                "source": f"新版本 v{succ_ver} 新增",
            })
    return out


def _resolved(case, review_key: str) -> bool:
    return any(r["review_key"] == review_key for r in case.get("review_resolutions", []))


def _external_review(case, review_key: str) -> bool:
    return any(r["review_key"] == review_key for r in case.get("external_reviews", []))


# ---------------------------------------------------------------- 派生事件

def derived_events(state, impacts: list[Impact], now: str) -> list[dict]:
    """把传播结论物化为可回溯的协调派生事件（确定性 ID，迟到重放后整体重建）。"""
    out = []
    for im in impacts:
        case = state.cases[im.case_id]
        if im.kind == "TRANSITION" and im.action == "CONTINUE_OLD_RULES":
            if case.get("transition_applied"):
                continue
            out.append({
                "event_id": f"derived-transition-{im.case_id}-{_digest(im.trigger_event_id)}",
                "event_type": "CASE_TRANSITION_APPLIED",
                "aggregate_type": "application_case",
                "aggregate_id": im.case_id,
                "occurred_at": im.transition["at"],
                "summary": f"系统标记：{im.case_id} 符合过渡安排，按 {im.clause_id} v{im.old_version} 原规则继续办理",
                "origin": "coordinator",
                "payload": {
                    "clause_id": im.clause_id, "clause_version": im.old_version,
                    "rule": "CONTINUE_OLD_RULES", "trigger_event_id": im.trigger_event_id,
                    "public_note": im.transition.get("public_note"),
                    "cutoff_date": im.transition.get("cutoff_date"),
                },
            })
            continue
        if im.review_key and not im.review_resolved:
            out.append({
                "event_id": f"derived-review-{im.case_id}-{_digest(im.review_key)}",
                "event_type": "CASE_REVIEWED",
                "aggregate_type": "application_case",
                "aggregate_id": im.case_id,
                "occurred_at": now,
                "summary": (
                    f"系统预警：{im.case_id} 将于 {im.effective_at} 起因依据变动进入复核（{im.reason}）"
                    if im.scheduled
                    else f"系统标记：{im.case_id} 因依据变动待复核（{im.reason}）"
                ),
                "origin": "coordinator",
                "payload": {
                    "review_key": im.review_key,
                    "reason": im.reason,
                    "action": im.action,
                    "scheduled": im.scheduled,
                    "effective_at": im.effective_at,
                    "trigger_event_id": im.trigger_event_id,
                    "clause_id": im.clause_id,
                    "clause_version": im.old_version,
                    "successor_version": im.successor_version,
                    "transition_rule": im.transition["rule"] if im.transition else None,
                    "missing_material_codes": [m["code"] for m in im.missing_materials],
                },
            })
    return out
