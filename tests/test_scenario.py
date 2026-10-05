import json
import unittest
from pathlib import Path

from src.validator import validate_event, validate_event_full
from src.service import CoordinationService, ValidationError
from src import views

ROOT = Path(__file__).parents[1]


def load_jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / "data" / name).read_text(encoding="utf-8").splitlines() if line.strip()]


class ScenarioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = CoordinationService()
        self.svc.ingest_many(load_jsonl("scenario.jsonl"))
        self.revision = load_jsonl("revision_candidates.jsonl")

    # ---------- 契约 ----------
    def test_sample_envelope_still_valid(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_full_validation_rejects_bad_aggregate(self) -> None:
        bad = {
            "event_id": "evt-bad-0001", "event_type": "CASE_DECIDED",
            "aggregate_type": "policy_clause", "aggregate_id": "X",
            "occurred_at": "2026-10-05T10:00:00+08:00", "version": 1, "summary": "错误归属",
            "payload": {"outcome": "APPROVED", "decided_at": "2026-10-05", "basis": []},
        }
        errs = validate_event_full(bad)
        self.assertTrue(any("不能归属" in e for e in errs))

    # ---------- 幂等与版本 ----------
    def test_retry_same_event_id_is_idempotent(self) -> None:
        ev = load_jsonl("scenario.jsonl")[0]
        self.svc.ingest(ev)
        factual = self.svc.store.factual_events()
        self.assertEqual(len([e for e in factual if e["event_id"] == ev["event_id"]]), 1)

    def test_version_conflict_detected(self) -> None:
        ev = dict(load_jsonl("scenario.jsonl")[0])
        ev["event_id"] = "evt-conflict-01"
        with self.assertRaises(Exception):
            self.svc.ingest(ev)

    # ---------- 发布前模拟 ----------
    def test_simulation_lists_real_dependents_only(self) -> None:
        report = self.svc.simulate(self.revision)["report"]
        ids = {r["case_id"] for r in report["pending_cases_for_review"]}
        cont = {r["case_id"] for r in report["cases_continue_under_transition"]}
        # 9月30日前受理：过渡继续旧规则；之后受理：废止生效后进入复核
        self.assertIn("CASE-1001", cont)
        self.assertIn("CASE-1004", ids)
        # 宁波项目不在 scope 地区，绝不传播
        self.assertNotIn("CASE-1003", ids | cont)
        # 已决定且 GRANDFATHER：不进复核清单
        self.assertEqual(report["decisions_for_review"], [])
        # CASE-1004 将缺两个新版本材料，且责任部门可定位
        row = next(r for r in report["pending_cases_for_review"] if r["case_id"] == "CASE-1004")
        self.assertEqual({m["code"] for m in row["missing_materials"]}, {"M-TECH-GRANT", "M-DATA-PROMISE"})
        self.assertTrue(row["scheduled"])  # 废止 11-01 生效，当前为待生效预警

    def test_simulation_does_not_persist(self) -> None:
        self.svc.simulate(self.revision)
        state = self.svc.state()
        self.assertNotIn(2, state.clauses["CL-TRADE"]["versions"])

    # ---------- 发布后传播 ----------
    def test_publish_propagates_and_derived_events_rebuild(self) -> None:
        self.svc.ingest_many(self.revision)
        impacts = {i["case_id"]: i for i in self.svc.current_impacts()}
        self.assertEqual(impacts["CASE-1001"]["action"], "CONTINUE_OLD_RULES")
        self.assertEqual(impacts["CASE-1004"]["action"], "REVIEW_NEW_VERSION")
        self.assertNotIn("CASE-1003", impacts)  # 范围隔离
        # 再次对账：派生事件确定性重建，数量不翻倍
        self.svc.reconcile()
        derived = [e for e in self.svc.store.events() if e.get("origin") == "coordinator"]
        self.svc.reconcile()
        derived2 = [e for e in self.svc.store.events() if e.get("origin") == "coordinator"]
        self.assertEqual(len(derived), len(derived2))
        self.assertEqual({e["event_id"] for e in derived}, {e["event_id"] for e in derived2})

    def test_decided_case_preserves_basis_grandfather(self) -> None:
        self.svc.ingest_many(self.revision)
        wb = views.staff_workbench(self.svc.state(), "CASE-1002")
        self.assertTrue(wb["decision"]["basis_preserved"])
        self.assertEqual(wb["decision"]["reviews"], [])
        self.assertEqual(wb["decision"]["basis"][0]["document_no"], "杭文旅贸〔2024〕3号")

    # ---------- 双口径脱敏 ----------
    def test_applicant_notice_never_leaks_internal(self) -> None:
        # 修订前：补正请求 payload 夹带了 note / internal_draft
        notice = views.applicant_notice(self.svc.state(), "CASE-1001")
        blob = json.dumps(notice, ensure_ascii=False)
        self.assertNotIn("KJJ", blob)
        self.assertNotIn("会商", blob)
        self.assertNotIn("internal", blob.lower())
        self.assertTrue(any(i["code"] == "M-PLAN" for i in notice["items"]))

        # 修订后：经办页面必须能看到冲突与内部备注，申请方仍然看不到
        self.svc.ingest_many(self.revision)
        notice2 = views.applicant_notice(self.svc.state(), "CASE-1004")
        blob2 = json.dumps(notice2, ensure_ascii=False)
        self.assertNotIn("SJJ", blob2)
        self.assertNotIn("冲突", blob2)
        self.assertNotIn("局务会", blob2)
        self.assertIn("M-DATA-PROMISE", {i["code"] for i in notice2["items"]})

        wb = views.staff_workbench(self.svc.state(), "CASE-1004")
        self.assertIn("WHJ", wb["responsible_agencies"])
        self.assertIn("KJJ", wb["responsible_agencies"])
        self.assertIn("SJJ", wb["responsible_agencies"])
        conflicts = wb["conflicts"]
        self.assertTrue(any(c["topic"] == "数据安全承诺受理分工" for c in conflicts))
        self.assertTrue(any(m["code"] == "M-DATA-PROMISE" for m in wb["missing_conditions"]))

    # ---------- 迟到事件 ----------
    def test_late_event_replay_corrects_propagation(self) -> None:
        self.svc.ingest_many(self.revision)
        # 迟到补录一条更早的过渡安排：SUPPLEMENT_UNDER_NEW，受理时限更宽，覆盖 CASE-1004
        late = {
            "event_id": "evt-late-transition",
            "event_type": "POLICY_TRANSITION_DECLARED",
            "aggregate_type": "policy_clause",
            "aggregate_id": "CL-TRADE",
            "occurred_at": "2026-09-28T09:00:00+08:00",
            "version": 6,
            "summary": "（迟到补录）补充过渡口径：10月15日前受理项目按新版补材料",
            "payload": {
                "target_versions": [1],
                "rule": "SUPPLEMENT_UNDER_NEW",
                "scope": {"regions": ["330100"], "subjects": ["*"], "matter_codes": ["MT-DIGITAL-EXHIBITION"]},
                "accepted_before": "2026-10-15T23:59:59+08:00",
                "successor_version": 2,
                "public_note": "10月15日前已受理项目，按修订后的材料清单补齐即可继续办理。",
            },
        }
        self.svc.ingest(late)
        impacts = {i["case_id"]: i for i in self.svc.current_impacts()}
        self.assertEqual(impacts["CASE-1004"]["action"], "SUPPLEMENT_UNDER_NEW")
        # CASE-1001 受理更早，两条过渡都适用时取“最新声明”的一条 -> 仍按旧规则
        self.assertEqual(impacts["CASE-1001"]["action"], "CONTINUE_OLD_RULES")

    # ---------- 延期不传播 ----------
    def test_extension_alone_causes_no_review(self) -> None:
        self.svc.ingest({
            "event_id": "evt-ext-only",
            "event_type": "POLICY_EXTENDED",
            "aggregate_type": "policy_clause",
            "aggregate_id": "CL-TRADE",
            "occurred_at": "2026-09-25T09:00:00+08:00",
            "version": 9,
            "summary": "旧办法有效期延长",
            "payload": {"base_version": 1, "new_expires_at": "2027-03-31T00:00:00+08:00"},
        })
        self.assertEqual(self.svc.current_impacts(), [])

    # ---------- 复核解决后收口 ----------
    def test_review_resolution_clears_marking(self) -> None:
        self.svc.ingest_many(self.revision)
        key = next(i["review_key"] for i in self.svc.current_impacts() if i["case_id"] == "CASE-1004")
        self.svc.ingest({
            "event_id": "evt-resolve-1004",
            "event_type": "CASE_REVIEW_RESOLVED",
            "aggregate_type": "application_case",
            "aggregate_id": "CASE-1004",
            "occurred_at": "2026-11-02T10:00:00+08:00",
            "version": 2,
            "summary": "CASE-1004 复核完成，已按新版重新适配",
            "payload": {"review_key": key, "resolution": "ADAPTED_TO_NEW_VERSION"},
        })
        ids = {i["case_id"] for i in self.svc.current_impacts()}
        self.assertNotIn("CASE-1004", ids)

    # ---------- 关系网络 ----------
    def test_network_is_traceable(self) -> None:
        self.svc.ingest_many(self.revision)
        net = views.network(self.svc.state())
        node_ids = {n["id"] for n in net["nodes"]}
        self.assertIn("clause:CL-TRADE:v1", node_ids)
        self.assertIn("clause:CL-TRADE:v2", node_ids)
        self.assertIn("case:CASE-1001", node_ids)
        self.assertTrue(any(e["type"] == "DECIDED_ON" and e["from"] == "case:CASE-1002" for e in net["edges"]))


if __name__ == "__main__":
    unittest.main()
