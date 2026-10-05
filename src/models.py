"""领域状态模型：只保存可由事件回放得到的事实。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def parse_ts(value: str) -> datetime:
    """把 ISO 时间统一成可比较的感知时区时间。"""
    return datetime.fromisoformat(value)


def scope_matches(scope: list[str], value: str) -> bool:
    """空范围表示面向全部地区或主体。"""
    return not scope or value in scope


@dataclass
class ClauseChange:
    """一次会影响依赖方的条款变动（修订或废止），范围取变动前后的并集。"""

    clause_id: str
    kind: str  # "amend" | "repeal"
    occurred_at: str
    event_id: str
    version: int
    region_scope: list[str]
    subject_scope: list[str]


@dataclass
class ClauseState:
    """政策条款的当前版本与完整版本历史。"""

    clause_id: str
    version: int = 0
    title: str = ""
    owning_agency: str = ""
    region_scope: list[str] = field(default_factory=list)
    subject_scope: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    published_at: str | None = None
    effective_from: str | None = None
    repealed_at: str | None = None
    replaced_by: str | None = None
    transitions: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)


@dataclass
class ReviewMark:
    """复核标记：说明哪次条款变动让哪个对象进入待复核。"""

    clause_id: str
    clause_version: int
    kind: str  # "case" 在办事项 | "decision" 已生效决定
    reason: str
    event_id: str
    created_at: str


@dataclass
class Decision:
    """已生效决定：依据版本快照永久保留，复核只加标记不改依据。"""

    decision: str
    decided_by: str
    decided_at: str
    basis: dict[str, int]  # clause_id -> 作出决定时的条款版本
    pending_review: bool = False


@dataclass
class CaseState:
    """受理事项及其复核标记、提醒。"""

    case_id: str
    applicant: str = ""
    region: str = ""
    subject: str = ""
    clause_ids: list[str] = field(default_factory=list)
    materials_provided: list[str] = field(default_factory=list)
    accepted_at: str | None = None
    status: str = "registered"  # registered | accepted | decided | closed
    closure: list[str] = field(default_factory=list)
    decision: Decision | None = None
    review_marks: list[ReviewMark] = field(default_factory=list)
    reminders: list[dict] = field(default_factory=list)
