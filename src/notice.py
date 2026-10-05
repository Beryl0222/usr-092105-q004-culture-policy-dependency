"""面向申请方与经办人的两类视图：同源事实，口径与过滤规则不同。"""
from __future__ import annotations

from dataclasses import asdict

from src.service import CoordinationService


def applicant_notice(service: CoordinationService, case_id: str) -> dict:
    """申请方补正说明：只含公开事实，不夹带内部会商意见与部门冲突。"""
    case = service.cases[case_id]
    items = []
    for clause_id in case.closure:
        clause = service.clauses.get(clause_id)
        if clause is None:
            continue
        if clause.repealed_at:
            items.append(
                {
                    "clause_id": clause_id,
                    "title": clause.title,
                    "missing_materials": [],
                    "note": "依据条款已废止，请等待主管部门发布新口径后再补正",
                }
            )
            continue
        missing = [m for m in clause.materials if m not in case.materials_provided]
        if missing:
            items.append(
                {
                    "clause_id": clause_id,
                    "title": clause.title,
                    "missing_materials": missing,
                    "effective_from": clause.effective_from,
                }
            )
    public_opinions = [
        {"agency": o["agency"], "clause_id": o["clause_id"], "text": o["text"]}
        for o in service.opinions.values()
        if o["clause_id"] in case.closure and o["visibility"] == "public"
    ]
    return {
        "case_id": case_id,
        "applicant": case.applicant,
        "items": items,
        "review_reasons": sorted({m.reason for m in case.review_marks}),
        "reminders": [r["text"] for r in case.reminders],
        "public_opinions": public_opinions,
    }


def caseworker_briefing(service: CoordinationService, case_id: str) -> dict:
    """经办页面简报：直接指出责任部门与所缺条件，并列出内部会商与未决冲突。"""
    case = service.cases[case_id]
    responsible = []
    for clause_id in case.closure:
        clause = service.clauses.get(clause_id)
        if clause is None:
            continue
        responsible.append(
            {
                "clause_id": clause_id,
                "title": clause.title,
                "owning_agency": clause.owning_agency,
                "missing_materials": [m for m in clause.materials if m not in case.materials_provided],
                "effective_from": clause.effective_from,
                "repealed_at": clause.repealed_at,
            }
        )
    conflicts = [
        c for c in service.conflicts.values() if c["clause_id"] in case.closure and c["status"] == "open"
    ]
    opinions = [o for o in service.opinions.values() if o["clause_id"] in case.closure]
    decision = None
    if case.decision is not None:
        decision = {
            "decision": case.decision.decision,
            "decided_by": case.decision.decided_by,
            "decided_at": case.decision.decided_at,
            "basis": dict(case.decision.basis),
            "pending_review": case.decision.pending_review,
        }
    return {
        "case_id": case_id,
        "status": case.status,
        "responsible": responsible,
        "review_marks": [asdict(m) for m in case.review_marks],
        "reminders": list(case.reminders),
        "open_conflicts": conflicts,
        "opinions": opinions,
        "decision": decision,
    }
