import unittest

from src.notice import applicant_notice, caseworker_briefing
from src.service import CoordinationService
from src.simulate import simulate_revision

T = "2026-09-20T12:00:00+08:00"


def ev(event_id, event_type, aggregate_type, aggregate_id, occurred_at, version, summary, **payload):
    return {
        "event_id": event_id,
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "occurred_at": occurred_at,
        "version": version,
        "summary": summary,
        "payload": payload,
    }


def publish(event_id, clause_id, occurred_at, version, title, agency, regions, subjects, materials, effective_from):
    return ev(
        event_id, "POLICY_PUBLISHED", "policy_clause", clause_id, occurred_at, version, f"发布《{title}》",
        action="publish", title=title, owning_agency=agency, region_scope=regions,
        subject_scope=subjects, materials=materials, effective_from=effective_from,
    )


def accept(event_id, case_id, occurred_at, applicant, region, subject, clause_ids, materials):
    return ev(
        event_id, "CASE_REVIEWED", "application_case", case_id, occurred_at, 1, f"受理 {applicant} 的申请",
        action="accept", applicant=applicant, region=region, subject=subject,
        clause_ids=clause_ids, materials_provided=materials, accepted_at=occurred_at,
    )


def base_service() -> CoordinationService:
    """数字展演补助办法(A) 引用 文化贸易办法(B)；滨江区一件在办、西湖区一件、一件仅依 B。"""
    svc = CoordinationService()
    assert svc.ingest(publish(
        "evt-0001", "clause-a", "2026-07-01T09:00:00+08:00", 1,
        "数字展演补助办法", "文旅局", ["滨江区"], ["数字展演"],
        ["项目方案", "预算明细"], "2026-08-01T00:00:00+08:00",
    )) == []
    assert svc.ingest(publish(
        "evt-0002", "clause-b", "2026-07-01T09:30:00+08:00", 1,
        "文化贸易办法", "商务局", ["滨江区"], ["数字展演", "文化贸易"],
        ["资质证明"], "2026-08-01T00:00:00+08:00",
    )) == []
    assert svc.ingest(ev(
        "evt-0003", "DEPENDENCY_DECLARED", "dependency_edge", "edge-a-b",
        "2026-07-05T10:00:00+08:00", 1, "补助办法引用文化贸易办法",
        action="declare", from_clause="clause-a", to_clause="clause-b", kind="requires",
    )) == []
    assert svc.ingest(accept(
        "evt-0004", "case-001", "2026-09-01T10:00:00+08:00",
        "某数字展演公司", "滨江区", "数字展演", ["clause-a"], ["项目方案"],
    )) == []
    assert svc.ingest(accept(
        "evt-0005", "case-002", "2026-09-02T10:00:00+08:00",
        "外地展演团队", "西湖区", "数字展演", ["clause-a"], ["项目方案"],
    )) == []
    assert svc.ingest(accept(
        "evt-0006", "case-004", "2026-09-03T10:00:00+08:00",
        "某文化贸易企业", "滨江区", "文化贸易", ["clause-b"], ["资质证明"],
    )) == []
    return svc


def amend(event_id, clause_id, occurred_at, version, title, agency, regions, subjects, materials, effective_from):
    return ev(
        event_id, "POLICY_PUBLISHED", "policy_clause", clause_id, occurred_at, version, f"修订《{title}》",
        action="amend", title=title, owning_agency=agency, region_scope=regions,
        subject_scope=subjects, materials=materials, effective_from=effective_from,
    )


class PropagationTest(unittest.TestCase):
    def test_closure_includes_cited_clauses(self) -> None:
        svc = base_service()
        self.assertEqual(svc.cases["case-001"].closure, ["clause-a", "clause-b"])
        self.assertEqual(svc.review_queue(), [])

    def test_amend_reaches_only_true_dependents(self) -> None:
        svc = base_service()
        # 修订 A：仅滨江区、依赖 A 的 case-001 进入复核；case-004 只依 B，不受影响
        svc.ingest(amend("evt-0100", "clause-a", "2026-09-20T09:00:00+08:00", 2,
                         "数字展演补助办法", "文旅局", ["滨江区"], ["数字展演"],
                         ["项目方案", "预算明细", "技术安全说明"], "2026-11-01T00:00:00+08:00"))
        marked = {m.clause_id for m in svc.cases["case-001"].review_marks}
        self.assertEqual(marked, {"clause-a"})
        self.assertEqual(svc.cases["case-002"].review_marks, [])  # 地区不符
        self.assertEqual(svc.cases["case-004"].review_marks, [])  # 无真实依赖
        # 修订 B：沿引用边传播到 case-001，也命中直接依 B 的 case-004
        svc.ingest(amend("evt-0101", "clause-b", "2026-09-21T09:00:00+08:00", 2,
                         "文化贸易办法", "商务局", ["滨江区"], ["数字展演", "文化贸易"],
                         ["资质证明", "年报"], "2026-11-01T00:00:00+08:00"))
        self.assertIn("clause-b", {m.clause_id for m in svc.cases["case-001"].review_marks})
        self.assertIn("clause-b", {m.clause_id for m in svc.cases["case-004"].review_marks})
        self.assertEqual(svc.cases["case-002"].review_marks, [])

    def test_decided_case_keeps_basis_and_is_flagged(self) -> None:
        svc = base_service()
        svc.ingest(accept("evt-0200", "case-003", "2026-08-01T10:00:00+08:00",
                          "已办结展演公司", "滨江区", "数字展演", ["clause-a"], ["项目方案", "预算明细", "资质证明"]))
        svc.ingest(ev("evt-0201", "CASE_REVIEWED", "application_case", "case-003",
                      "2026-08-20T15:00:00+08:00", 2, "作出补助决定",
                      action="decide", decision="approved", decided_by="文旅局",
                      decided_at="2026-08-20T15:00:00+08:00"))
        svc.ingest(amend("evt-0202", "clause-b", "2026-09-20T09:00:00+08:00", 2,
                         "文化贸易办法", "商务局", ["滨江区"], ["数字展演", "文化贸易"],
                         ["资质证明"], "2026-11-01T00:00:00+08:00"))
        decision = svc.cases["case-003"].decision
        self.assertTrue(decision.pending_review)  # 明确待复核
        self.assertEqual(decision.basis, {"clause-a": 1, "clause-b": 1})  # 原依据保留
        queue = {entry["case_id"]: entry for entry in svc.review_queue()}
        self.assertEqual(queue["case-003"]["decision_basis"], {"clause-a": 1, "clause-b": 1})
        self.assertTrue(queue["case-003"]["decision_pending_review"])

    def test_transition_protects_grandfathered_cases(self) -> None:
        svc = base_service()
        svc.ingest(amend("evt-0300", "clause-b", "2026-09-20T09:00:00+08:00", 2,
                         "文化贸易办法", "商务局", ["滨江区"], ["数字展演", "文化贸易"],
                         ["资质证明", "年报"], "2026-11-01T00:00:00+08:00"))
        svc.ingest(ev("evt-0301", "POLICY_PUBLISHED", "policy_clause", "clause-b",
                      "2026-09-20T10:00:00+08:00", 3, "发布过渡安排",
                      action="transition",
                      transition={"cutoff": "2026-12-31T23:59:59+08:00",
                                  "note": "已受理事项继续按旧规则办理"}))
        case = svc.cases["case-001"]
        self.assertEqual(case.review_marks, [])  # 过渡保护，不进复核
        self.assertEqual(case.reminders[0]["kind"], "transition")
        self.assertIn("按旧规则办理", case.reminders[0]["text"])

    def test_late_version_is_reordered_into_history(self) -> None:
        svc = base_service()
        svc.ingest(accept("evt-0400", "case-003", "2026-08-01T10:00:00+08:00",
                          "已办结展演公司", "滨江区", "数字展演", ["clause-a"], ["项目方案"]))
        svc.ingest(ev("evt-0401", "CASE_REVIEWED", "application_case", "case-003",
                      "2026-08-20T15:00:00+08:00", 2, "作出补助决定",
                      action="decide", decision="approved", decided_by="文旅局",
                      decided_at="2026-08-20T15:00:00+08:00"))
        self.assertEqual(svc.cases["case-003"].decision.basis["clause-a"], 1)
        # 迟到的修订：真实发生时间早于决定，重放后决定依据应指向第 2 版
        svc.ingest(amend("evt-0402", "clause-a", "2026-08-10T09:00:00+08:00", 2,
                         "数字展演补助办法", "文旅局", ["滨江区"], ["数字展演"],
                         ["项目方案"], "2026-08-15T00:00:00+08:00"))
        decision = svc.cases["case-003"].decision
        self.assertEqual(decision.basis["clause-a"], 2)
        self.assertFalse(decision.pending_review)  # 决定晚于修订，无需复核
        self.assertEqual([h["version"] for h in svc.clauses["clause-a"].history], [1, 2])

    def test_postpone_reminds_but_does_not_flag(self) -> None:
        svc = base_service()
        svc.ingest(ev("evt-0500", "POLICY_PUBLISHED", "policy_clause", "clause-a",
                      "2026-09-20T09:00:00+08:00", 2, "生效时间延期",
                      action="postpone", new_effective_from="2027-01-01T00:00:00+08:00"))
        case = svc.cases["case-001"]
        self.assertEqual(case.review_marks, [])  # 延期不产生复核
        self.assertEqual(case.reminders[0]["kind"], "postpone")
        self.assertIn("2027-01-01", case.reminders[0]["text"])
        self.assertEqual(svc.cases["case-002"].reminders, [])  # 地区不符，不打扰

    def test_repeal_flags_dependents(self) -> None:
        svc = base_service()
        svc.ingest(ev("evt-0600", "POLICY_PUBLISHED", "policy_clause", "clause-b",
                      "2026-09-20T09:00:00+08:00", 2, "废止文化贸易办法",
                      action="repeal", repealed_at="2026-10-01T00:00:00+08:00"))
        reasons = [m.reason for m in svc.cases["case-001"].review_marks]
        self.assertTrue(any("已废止" in r for r in reasons))
        self.assertTrue(svc.clauses["clause-b"].repealed_at)


class NoticeTest(unittest.TestCase):
    def service_with_opinions(self) -> CoordinationService:
        svc = base_service()
        svc.ingest(amend("evt-0700", "clause-a", "2026-09-20T09:00:00+08:00", 2,
                         "数字展演补助办法", "文旅局", ["滨江区"], ["数字展演"],
                         ["项目方案", "预算明细", "技术安全说明"], "2026-11-01T00:00:00+08:00"))
        svc.ingest(ev("evt-0701", "OPINION_ISSUED", "agency_opinion", "op-1",
                      "2026-09-21T10:00:00+08:00", 1, "法律顾问组内部会商",
                      agency="法律顾问组", clause_id="clause-a",
                      text="内部会商：建议暂缓按新版执行", visibility="internal"))
        svc.ingest(ev("evt-0702", "OPINION_ISSUED", "agency_opinion", "op-2",
                      "2026-09-21T11:00:00+08:00", 1, "文旅局公开说明",
                      agency="文旅局", clause_id="clause-a",
                      text="新版自十一月一日起施行", visibility="public"))
        svc.ingest(ev("evt-0703", "CONFLICT_RAISED", "agency_opinion", "cf-1",
                      "2026-09-22T09:00:00+08:00", 1, "两局口径冲突",
                      clause_id="clause-b", agencies=["文旅局", "商务局"],
                      topic="资质证明是否互认"))
        return svc

    def test_applicant_notice_hides_internal_deliberation(self) -> None:
        svc = self.service_with_opinions()
        notice = applicant_notice(svc, "case-001")
        text = str(notice)
        self.assertNotIn("内部会商", text)
        self.assertNotIn("冲突", text)
        self.assertNotIn("法律顾问组", text)
        self.assertEqual(len(notice["public_opinions"]), 1)
        # 补正说明列出当前版本所缺材料
        missing = {m for item in notice["items"] for m in item["missing_materials"]}
        self.assertEqual(missing, {"预算明细", "技术安全说明", "资质证明"})
        self.assertTrue(any("已修订" in r for r in notice["review_reasons"]))

    def test_caseworker_briefing_points_agency_and_gaps(self) -> None:
        svc = self.service_with_opinions()
        briefing = caseworker_briefing(svc, "case-001")
        agencies = {r["owning_agency"] for r in briefing["responsible"]}
        self.assertEqual(agencies, {"文旅局", "商务局"})
        gaps = {m for r in briefing["responsible"] for m in r["missing_materials"]}
        self.assertEqual(gaps, {"预算明细", "技术安全说明", "资质证明"})
        self.assertEqual(len(briefing["open_conflicts"]), 1)
        self.assertIn("内部会商", str(briefing["opinions"]))
        self.assertEqual(briefing["review_marks"][0]["kind"], "case")


class SimulationTest(unittest.TestCase):
    def test_simulate_revision_without_mutating(self) -> None:
        svc = base_service()
        svc.ingest(accept("evt-0800", "case-003", "2026-08-01T10:00:00+08:00",
                          "已办结展演公司", "滨江区", "数字展演", ["clause-a"], ["项目方案"]))
        svc.ingest(ev("evt-0801", "CASE_REVIEWED", "application_case", "case-003",
                      "2026-08-20T15:00:00+08:00", 2, "作出补助决定",
                      action="decide", decision="approved", decided_by="文旅局",
                      decided_at="2026-08-20T15:00:00+08:00"))
        draft = amend("evt-draft-1", "clause-b", "2026-10-10T09:00:00+08:00", 2,
                      "文化贸易办法", "商务局", ["滨江区"], ["数字展演", "文化贸易"],
                      ["资质证明", "年报"], "2026-12-01T00:00:00+08:00")
        report = simulate_revision(svc, [draft])
        self.assertTrue(report["ok"])
        self.assertEqual(report["affected_regions"], ["滨江区"])
        self.assertEqual(report["in_flight_cases"], ["case-001", "case-004"])
        self.assertEqual(report["decisions_pending_review"], ["case-003"])
        # 正式状态未被污染
        self.assertEqual(svc.review_queue(), [])
        self.assertEqual(svc.clauses["clause-b"].version, 1)

    def test_simulate_rejects_invalid_draft(self) -> None:
        svc = base_service()
        bad = amend("evt-draft-2", "clause-b", T, 2, "文化贸易办法", "商务局",
                    ["滨江区"], ["数字展演"], ["资质证明"], "2026-12-01T00:00:00+08:00")
        del bad["payload"]["title"]
        report = simulate_revision(svc, [bad])
        self.assertFalse(report["ok"])
        self.assertIn("payload 缺少字段：title", report["errors"])


class IngestTest(unittest.TestCase):
    def test_invalid_event_returns_chinese_errors(self) -> None:
        svc = CoordinationService()
        errors = svc.ingest({"event_id": "evt-x", "event_type": "POLICY_PUBLISHED",
                             "aggregate_type": "policy_clause", "aggregate_id": "c1",
                             "occurred_at": T, "version": 1, "summary": "缺 payload"})
        self.assertIn("缺少字段：payload", errors)

    def test_duplicate_event_id_is_idempotent(self) -> None:
        svc = base_service()
        before = len(svc.clauses["clause-a"].history)
        event = publish("evt-0001", "clause-a", "2026-07-01T09:00:00+08:00", 1,
                        "数字展演补助办法", "文旅局", ["滨江区"], ["数字展演"],
                        ["项目方案"], "2026-08-01T00:00:00+08:00")
        self.assertEqual(svc.ingest(event), [])
        self.assertEqual(len(svc.clauses["clause-a"].history), before)


if __name__ == "__main__":
    unittest.main()
