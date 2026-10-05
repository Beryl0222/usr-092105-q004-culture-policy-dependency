"""端到端演示：数字展演项目面对文化贸易办法修订稿的全过程。

运行：python3 -m src.demo
"""

import json
from pathlib import Path

from src.service import CoordinationService
from src import views

DATA = Path(__file__).parents[1] / "data"


def load_jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def show(title: str, obj) -> None:
    print(f"\n{'='*20} {title} {'='*20}")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main() -> None:
    svc = CoordinationService()
    svc.ingest_many(load_jsonl("scenario.jsonl"))

    # ---------- 1. 修订稿发布前：模拟影响 ----------
    candidates = load_jsonl("revision_candidates.jsonl")
    sim = svc.simulate(candidates)
    show("发布前模拟：修订将让哪些地区/在办事项/既有决定受影响", sim["report"])

    # ---------- 2. 申请方口径（模拟尚未发布，仍只有部门补正） ----------
    state_before = svc.state()
    show("申请方补正说明 · CASE-1001（修订发布前）", views.applicant_notice(state_before, "CASE-1001"))

    # ---------- 3. 正式发布修订（迟到/延期/废止/过渡事件落库） ----------
    svc.ingest_many(candidates)

    state = svc.state()

    show("申请方补正说明 · CASE-1001（过渡期：按旧规则继续，不夹带内部会商）",
         views.applicant_notice(state, "CASE-1001"))
    show("申请方说明 · CASE-1004（过渡时限后受理：提示按修订补材料，只列材料不透露会商）",
         views.applicant_notice(state, "CASE-1004"))

    show("经办页面 · CASE-1004（责任部门、所缺条件、内部冲突意见一应俱全）",
         views.staff_workbench(state, "CASE-1004"))
    show("经办页面 · CASE-1002（已生效决定：原依据保留，免复核标记）",
         views.staff_workbench(state, "CASE-1002"))

    net = views.network(state)
    show("关系网络（节点与边，可回溯）", {"counts": net["counts"], "sample_edges": net["edges"][:6]})

    # ---------- 4. 迟到事件补录：漏发的延期，重放后传播自动校正 ----------
    late_extension = {
        "event_id": "evt-scn-0015-late",
        "event_type": "POLICY_EXTENDED",
        "aggregate_type": "policy_clause",
        "aggregate_id": "CL-TRADE",
        "occurred_at": "2026-09-25T09:00:00+08:00",
        "version": 5,
        "summary": "（迟到补录）旧办法曾延期至2027年3月（示例：迟到事件不改变本场景废止结论）",
        "payload": {"base_version": 1, "new_expires_at": "2027-03-31T00:00:00+08:00", "reason": "届中延期"},
    }
    before = len(svc.current_impacts())
    svc.ingest(late_extension)
    after = len(svc.current_impacts())
    show("迟到事件补录后对账", {"impact_count_before": before, "impact_count_after": after,
                              "note": "延期仅改有效期；已有废止与过渡仍按真实依赖传播，派生事件整体重建无重复"})


if __name__ == "__main__":
    main()
