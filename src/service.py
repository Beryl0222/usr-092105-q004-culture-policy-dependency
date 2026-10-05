"""文化政策依赖协调服务：事件接收、依赖传播对账、发布前影响模拟。"""

import copy
from datetime import datetime, timezone, timedelta

from .store import EventStore
from .state import State
from .propagation import assess_case_edges, derived_events
from .validator import validate_event_full
from .store import ValidationError, VersionConflictError  # re-export

_CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(_CST).isoformat()


class CoordinationService:
    def __init__(self, path: str | None = None):
        self.store = EventStore(path)

    # ---------- 接收后继事件 ----------
    def ingest(self, record: dict) -> dict:
        """接收一个部门事实事件并重做传播对账。返回处理报告。"""
        event, created = self.store.append(record)
        if not created:
            return {"status": "DUPLICATE", "event_id": event["event_id"], "impacts": self.current_impacts()}
        impacts = self.reconcile()
        return {"status": "ACCEPTED", "event_id": event["event_id"], "impacts": impacts}

    def ingest_many(self, records: list[dict]) -> list[dict]:
        """批量接收（可含迟到补录），全部落库后只做一次对账。"""
        results = []
        for r in records:
            event, created = self.store.append(r)
            results.append({"status": "ACCEPTED" if created else "DUPLICATE", "event_id": event["event_id"]})
        impacts = self.reconcile()
        for r in results:
            r["impacts"] = impacts
        return results

    def reconcile(self, now: str | None = None) -> list[dict]:
        """从全部事实事件重放状态，重建传播结论与协调派生事件。

        迟到事件补录后调用同样安全：派生事件是结论不是事实，整体重建不会产生重复或遗漏。
        """
        state = State.replay(self.store.factual_events())
        impacts = assess_case_edges(state)
        fresh = derived_events(state, impacts, now or _now())
        self.store.replace_derived(fresh)
        return [self._impact_dict(im) for im in impacts]

    def current_impacts(self) -> list[dict]:
        state = State.replay(self.store.factual_events())
        return [self._impact_dict(im) for im in assess_case_edges(state)]

    def state(self) -> State:
        return State.replay(self.store.factual_events())

    # ---------- 发布前模拟 ----------
    def simulate(self, candidate_events: list[dict]) -> dict:
        """在不落库的前提下，试跑一批候选事件（如新修订发布+废止旧版+过渡安排），
        返回将进入复核/过渡的地区、在办事项、既有决定清单。

        模拟结论与正式 ingest 后 reconcile 的结论一致（同一套纯函数）。
        """
        candidates = []
        errors = []
        for i, rec in enumerate(candidate_events):
            errs = validate_event_full(rec)
            if errs:
                errors.append({"index": i, "event_id": rec.get("event_id"), "errors": errs})
            else:
                candidates.append(copy.deepcopy(rec))
        if errors:
            return {"status": "INVALID", "errors": errors}

        base = [copy.deepcopy(e) for e in self.store.factual_events()]
        # 候选事件若与库内 event_id 冲突，模拟中视为替换（部门修正重报场景）
        ids = {e["event_id"] for e in base}
        merged = [e for e in base if e["event_id"] not in {c["event_id"] for c in candidates}] + candidates
        state = State.replay(merged)
        impacts = assess_case_edges(state)
        return {"status": "OK", "report": self._impact_report(state, impacts, candidates)}

    # ---------- 输出整理 ----------
    @staticmethod
    def _impact_dict(im) -> dict:
        return {
            "case_id": im.case_id,
            "edge_id": im.edge_id,
            "clause_id": im.clause_id,
            "clause_version": im.old_version,
            "kind": im.kind,
            "action": im.action,
            "reason": im.reason,
            "trigger_event_id": im.trigger_event_id,
            "review_key": im.review_key,
            "successor_version": im.successor_version,
            "missing_materials": im.missing_materials,
            "review_resolved": im.review_resolved,
            "scheduled": im.scheduled,
            "effective_at": im.effective_at,
        }

    def _impact_report(self, state, impacts, candidates) -> dict:
        pending, decided, transition_continue, regions = [], [], [], set()
        trigger_ids = {c["event_id"] for c in candidates}
        for im in impacts:
            if im.trigger_event_id not in trigger_ids:
                continue  # 模拟报告只统计候选修订新触发的变化
            case = state.cases[im.case_id]
            regions.add(case["region"])
            row = {
                "case_id": im.case_id,
                "project_name": case.get("project_name"),
                "region": case["region"],
                "applicant": case.get("applicant"),
                "matter_code": case["matter_code"],
                "clause_id": im.clause_id,
                "from_version": im.old_version,
                "to_version": im.successor_version,
                "action": im.action,
                "reason": im.reason,
                "missing_materials": im.missing_materials,
                "scheduled": im.scheduled,
                "effective_at": im.effective_at,
            }
            if case["status"] == "DECIDED":
                row["decision_outcome"] = case["decision"]["outcome"]
                row["decided_at"] = case["decision"]["decided_at"]
                decided.append(row)
            elif im.kind == "TRANSITION" and im.action == "CONTINUE_OLD_RULES":
                transition_continue.append(row)
            else:
                pending.append(row)
        return {
            "affected_regions": sorted(regions),
            "pending_cases_for_review": pending,
            "decisions_for_review": decided,
            "cases_continue_under_transition": transition_continue,
            "counts": {
                "pending": len(pending),
                "scheduled_pending": len([r for r in pending if r["scheduled"]]),
                "decisions": len(decided),
                "transition_continue": len(transition_continue),
                "regions": len(regions),
            },
        }
