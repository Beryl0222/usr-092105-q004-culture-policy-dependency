"""校验领域事件信封与各事件类型的业务字段。"""

from datetime import datetime

EVENT_TYPES = {
    "POLICY_PUBLISHED",
    "POLICY_EXTENDED",
    "POLICY_REPEALED",
    "POLICY_TRANSITION_DECLARED",
    "DEPENDENCY_DECLARED",
    "DEPENDENCY_REBASED",
    "CONFLICT_RAISED",
    "CONFLICT_CLEARED",
    "OPINION_ISSUED",
    "CASE_ACCEPTED",
    "CASE_SUPPLEMENT_REQUESTED",
    "CASE_MATERIAL_RESUBMITTED",
    "CASE_DECIDED",
    "CASE_REVIEWED",
    "CASE_REVIEW_RESOLVED",
    "CASE_TRANSITION_APPLIED",
}

AGGREGATE_TYPES = {"policy_clause", "dependency_edge", "application_case", "agency_opinion"}

# 每种事件允许归属的聚合类型
EVENT_AGGREGATES = {
    "POLICY_PUBLISHED": {"policy_clause"},
    "POLICY_EXTENDED": {"policy_clause"},
    "POLICY_REPEALED": {"policy_clause"},
    "POLICY_TRANSITION_DECLARED": {"policy_clause"},
    "DEPENDENCY_DECLARED": {"dependency_edge"},
    "DEPENDENCY_REBASED": {"dependency_edge"},
    "CONFLICT_RAISED": {"agency_opinion"},
    "CONFLICT_CLEARED": {"agency_opinion"},
    "OPINION_ISSUED": {"agency_opinion"},
    "CASE_ACCEPTED": {"application_case"},
    "CASE_SUPPLEMENT_REQUESTED": {"application_case"},
    "CASE_MATERIAL_RESUBMITTED": {"application_case"},
    "CASE_DECIDED": {"application_case"},
    "CASE_REVIEWED": {"application_case"},
    "CASE_REVIEW_RESOLVED": {"application_case"},
    "CASE_TRANSITION_APPLIED": {"application_case"},
}

REQUIRED = ("event_id", "event_type", "aggregate_type", "aggregate_id", "occurred_at", "version", "summary")

# 各事件 payload 必填字段
PAYLOAD_REQUIRED = {
    "POLICY_PUBLISHED": ("clause_id", "version", "owner_agency", "effective_at", "regions", "matter_codes"),
    "POLICY_EXTENDED": ("base_version", "new_expires_at"),
    "POLICY_REPEALED": ("target_versions", "effective_at"),
    "POLICY_TRANSITION_DECLARED": ("target_versions", "rule", "scope"),
    "DEPENDENCY_DECLARED": ("edge_id", "kind", "clause_id", "clause_version"),
    "DEPENDENCY_REBASED": ("new_clause_id", "new_version"),
    "OPINION_ISSUED": ("opinion_id", "agency", "position", "content"),
    "CONFLICT_RAISED": ("topic", "opinions"),
    "CONFLICT_CLEARED": ("topic", "resolution"),
    "CASE_ACCEPTED": ("case_id", "applicant", "region", "matter_code", "accepted_at"),
    "CASE_SUPPLEMENT_REQUESTED": ("request_id", "material_codes"),
    "CASE_MATERIAL_RESUBMITTED": ("request_id", "material_codes"),
    "CASE_DECIDED": ("outcome", "decided_at", "basis"),
    "CASE_REVIEWED": ("review_key", "reason"),
    "CASE_REVIEW_RESOLVED": ("review_key", "resolution"),
    "CASE_TRANSITION_APPLIED": ("clause_id", "clause_version", "rule"),
}


def validate_event(record: dict) -> list[str]:
    """返回可以直接展示给接入方的中文错误（仅信封公共字段，保持原契约行为）。"""
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    return errors


def validate_event_full(record: dict) -> list[str]:
    """信封 + 事件类型/聚合一致性 + payload 必填字段的完整校验。"""
    errors = validate_event(record)
    et = record.get("event_type")
    at = record.get("aggregate_type")
    if et is not None and et not in EVENT_TYPES:
        errors.append(f"未知事件类型：{et}")
    if at is not None and at not in AGGREGATE_TYPES:
        errors.append(f"未知聚合类型：{at}")
    if et in EVENT_AGGREGATES and at in AGGREGATE_TYPES and at not in EVENT_AGGREGATES[et]:
        errors.append(f"事件 {et} 不能归属聚合 {at}")
    if "occurred_at" in record and not _parse_dt(record["occurred_at"]):
        errors.append("occurred_at 必须是 ISO 8601 日期时间")
    payload = record.get("payload")
    if et in PAYLOAD_REQUIRED:
        if not isinstance(payload, dict):
            errors.append("缺少 payload 业务字段")
        else:
            for name in PAYLOAD_REQUIRED[et]:
                if name not in payload:
                    errors.append(f"{et} 缺少 payload 字段：{name}")
    return errors


def _parse_dt(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc.astimezone().tzinfo)
    return dt
