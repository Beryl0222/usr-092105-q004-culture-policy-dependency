"""修订发布前的影响模拟：在事件日志副本上回放假想事件，不改动正式状态。"""
from __future__ import annotations

from src.service import CoordinationService


def _marked_cases(service: CoordinationService, kind: str) -> set[str]:
    return {case.case_id for case in service.cases.values() for m in case.review_marks if m.kind == kind}


def _reminded_cases(service: CoordinationService, kind: str) -> set[str]:
    return {case.case_id for case in service.cases.values() for r in case.reminders if r["kind"] == kind}


def simulate_revision(service: CoordinationService, draft_events: list[dict]) -> dict:
    """模拟一组假想事件（修订/废止/延期/过渡）发布后的影响范围。

    返回受影响的地区、将进入复核的在办事项、将待复核的已生效决定，
    以及受过渡安排保护、仅收到延期提醒的事项。正式状态不受影响。
    """
    clone = service.clone()
    for event in draft_events:
        errors = clone.ingest(event)
        if errors:
            return {"ok": False, "errors": errors}
    new_case_marks = _marked_cases(clone, "case") - _marked_cases(service, "case")
    new_decision_marks = _marked_cases(clone, "decision") - _marked_cases(service, "decision")
    protected = _reminded_cases(clone, "transition") - _reminded_cases(service, "transition")
    postponed = _reminded_cases(clone, "postpone") - _reminded_cases(service, "postpone")
    affected = new_case_marks | new_decision_marks | protected | postponed
    return {
        "ok": True,
        "affected_regions": sorted({clone.cases[c].region for c in affected}),
        "in_flight_cases": sorted(new_case_marks),
        "decisions_pending_review": sorted(new_decision_marks),
        "transition_protected": sorted(protected),
        "postpone_reminders": sorted(postponed),
    }
