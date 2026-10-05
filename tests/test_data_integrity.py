import json
import tempfile
import unittest
from pathlib import Path

from src.validator import validate_event_full
from src.service import CoordinationService

ROOT = Path(__file__).parents[1]


def load_jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / "data" / name).read_text(encoding="utf-8").splitlines() if line.strip()]


class DataAndPersistenceTest(unittest.TestCase):
    def test_all_fixture_events_pass_full_validation(self) -> None:
        for name in ("scenario.jsonl", "revision_candidates.jsonl"):
            for e in load_jsonl(name):
                with self.subTest(event=e["event_id"], file=name):
                    self.assertEqual(validate_event_full(e), [])

    def test_jsonl_store_roundtrip_and_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.jsonl"
            svc = CoordinationService(path)
            svc.ingest_many(load_jsonl("scenario.jsonl"))
            svc.ingest_many(load_jsonl("revision_candidates.jsonl"))
            first = {(i["case_id"], i["action"]) for i in svc.current_impacts()}

            svc2 = CoordinationService(path)  # 重新加载落库数据
            second = {(i["case_id"], i["action"]) for i in svc2.current_impacts()}
            self.assertEqual(first, second)

    def test_simulation_matches_actual_publish(self) -> None:
        revision = load_jsonl("revision_candidates.jsonl")
        svc = CoordinationService()
        svc.ingest_many(load_jsonl("scenario.jsonl"))
        report = svc.simulate(revision)["report"]
        sim = {r["case_id"]: r["action"] for r in
               report["pending_cases_for_review"] + report["cases_continue_under_transition"]}

        svc.ingest_many(revision)
        actual = {i["case_id"]: i["action"] for i in svc.current_impacts()}
        self.assertEqual(sim, actual)


if __name__ == "__main__":
    unittest.main()
