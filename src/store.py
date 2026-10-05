"""事件存储：幂等追加、版本约束、JSONL 持久化、派生事件重建。"""

import json
from pathlib import Path

from .validator import validate_event_full


class ValidationError(ValueError):
    """事件未通过契约校验。"""


class VersionConflictError(ValueError):
    """同一聚合出现了不同事件占用同一版本号。"""


class EventStore:
    def __init__(self, path: str | Path | None = None):
        self._events: list[dict] = []
        self._ids: set[str] = set()
        self._path = Path(path) if path else None
        if self._path and self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._load(json.loads(line))

    # ---- 接收 ----
    def append(self, record: dict) -> tuple[dict, bool]:
        """接收部门事实事件。返回 (事件, 是否新写入)；event_id 重试时幂等跳过。"""
        errors = validate_event_full(record)
        if errors:
            raise ValidationError("；".join(errors))
        if record["event_id"] in self._ids:
            return self._by_id(record["event_id"]), False
        conflict = self._find_version_conflict(record)
        if conflict is not None:
            raise VersionConflictError(conflict)
        self._load(record)
        if self._path:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record, True

    def replace_derived(self, derived: list[dict]) -> list[dict]:
        """用一批新的协调派生事件整体替换旧的派生事件（迟到事件后可安全重建）。

        部门事实事件（origin != "coordinator"）永不删除。
        """
        kept = [e for e in self._events if e.get("origin") != "coordinator"]
        removed = [e for e in self._events if e.get("origin") == "coordinator"]
        # 为派生事件在各聚合内分配部门版本号之后的版本
        max_version: dict[tuple[str, str], int] = {}
        for e in kept:
            key = (e["aggregate_type"], e["aggregate_id"])
            max_version[key] = max(max_version.get(key, 0), e["version"])
        for e in derived:
            key = (e["aggregate_type"], e["aggregate_id"])
            max_version[key] += 1
            e["version"] = max_version[key]
            e.setdefault("origin", "coordinator")
        self._events = kept + derived
        self._ids = {e["event_id"] for e in self._events}
        if self._path:
            self._path.write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self._events),
                encoding="utf-8",
            )
        return removed

    # ---- 查询 ----
    def events(self, include_derived: bool = True) -> list[dict]:
        """重放顺序：先事实事件，再协调派生事件；组内按真实发生时间，再按入库顺序。"""
        factual = [e for e in self._events if e.get("origin") != "coordinator"]
        derived = [e for e in self._events if e.get("origin") == "coordinator"]
        factual.sort(key=lambda e: (e["occurred_at"], e.get("_seq", 0)))
        if include_derived:
            derived.sort(key=lambda e: (e["occurred_at"], e.get("_seq", 0)))
            return factual + derived
        return factual

    def factual_events(self) -> list[dict]:
        return self.events(include_derived=False)

    def __len__(self) -> int:
        return len(self._events)

    # ---- 内部 ----
    def _load(self, record: dict) -> None:
        record = dict(record)
        record["_seq"] = len(self._events)
        self._events.append(record)
        self._ids.add(record["event_id"])

    def _by_id(self, event_id: str) -> dict:
        return next(e for e in self._events if e["event_id"] == event_id)

    def _find_version_conflict(self, record: dict) -> str | None:
        if record.get("origin") == "coordinator":
            return None
        for e in self._events:
            if e.get("origin") == "coordinator":
                continue
            if (
                e["aggregate_type"] == record["aggregate_type"]
                and e["aggregate_id"] == record["aggregate_id"]
                and e["version"] == record["version"]
                and e["event_id"] != record["event_id"]
            ):
                return (
                    f"聚合 {record['aggregate_type']}/{record['aggregate_id']} 版本 {record['version']} "
                    f"已被事件 {e['event_id']} 占用"
                )
        return None
