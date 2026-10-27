import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from assessment_core.api import work_once
from assessment_core.engine import prepare
from assessment_core.providers.jev import JevError
from assessment_core.runstore import RunStore, RunUnavailable


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RunStore(Path(self.temp.name) / "runs.sqlite3")
        examples = Path(__file__).resolve().parents[1] / "examples"
        self.plan = prepare(json.loads((examples / "corpus.synthetic.json").read_text()),
                            json.loads((examples / "rubric.synthetic.json").read_text()))

    def response(self):
        return {"model": "jev-1.14.0", "usage": {"input_tokens": 1, "output_tokens": 1},
                "answers": {key: {"type": "choice", "choice": "insufficient_data", "confidence": 1.0,
                                  "probabilities": {option: float(option == "insufficient_data")
                                                    for option in question["criteria"]}}
                            for key, question in self.plan["request"]["questions"].items()}}

    def test_submit_worker_read_and_repeat_use_one_provider_call(self):
        test = self
        calls = []
        class Client:
            def evaluate(self, request):
                calls.append(copy.deepcopy(request))
                test.assertEqual(test.store.get(test.plan["run_id"])["status"], "sent")
                return test.response()
        first = self.store.submit(self.plan)
        self.assertEqual(first["status"], "queued")
        self.assertEqual(self.store.submit(self.plan)["id"], first["id"])
        self.assertTrue(work_once(self.store, Client()))
        self.assertFalse(work_once(self.store, Client()))
        row = self.store.get(first["id"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["result"]["corpus_sha256"], self.plan["corpus"]["sha256"])
        self.assertEqual(self.store.submit(self.plan)["status"], "completed")
        self.assertEqual(calls, [self.plan["request"]])

    def test_database_owner_is_exclusive_even_with_another_service_port(self):
        other = RunStore(self.store.path)
        with self.store.runtime_lock():
            with self.assertRaisesRegex(RunUnavailable, "store_in_use"):
                with other.runtime_lock():
                    self.fail("second owner acquired an active database")
        with other.runtime_lock():
            other.recover()

    def test_recovery_preserves_queued_and_never_resends_sent(self):
        self.store.submit(self.plan)
        with self.store.runtime_lock():
            self.store.recover()
        self.assertEqual(self.store.get(self.plan["run_id"])["status"], "queued")
        with self.store.db() as db:
            db.execute("UPDATE runs SET status='sent'")
        with self.store.runtime_lock():
            self.store.recover()
        self.assertEqual(self.store.get(self.plan["run_id"])["status"], "outcome_unknown")
        self.assertIsNone(self.store.next_queued())

    def test_recovery_finishes_saved_response_offline(self):
        class Client:
            def evaluate(inner, request):
                return self.response()
        with patch("assessment_core.runstore.interpret", side_effect=ValueError("interrupted")):
            with self.assertRaises(ValueError):
                self.store.execute(self.plan, Client())
        self.assertEqual(self.store.get(self.plan["run_id"])["status"], "response_saved")
        with self.store.runtime_lock():
            self.store.recover()
        self.assertEqual(self.store.get(self.plan["run_id"])["status"], "completed")

    def test_corrupted_queued_plan_fails_without_repeated_worker_calls(self):
        self.store.submit(self.plan)
        with self.store.db() as db:
            db.execute("UPDATE runs SET plan='{}'")
        self.assertIsNone(self.store.next_queued())
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT status FROM runs").fetchone()[0], "failed")

    def test_completed_replay_refuses_a_different_contract_version(self):
        class Client:
            def evaluate(inner, request):
                return self.response()
        self.store.execute(self.plan, Client())
        original = self.store.get(self.plan["run_id"])
        with patch("assessment_core.engine.VERSION", "future-v2"):
            with self.assertRaises(ValueError):
                self.store.replay(self.plan["run_id"])
        self.assertEqual(self.store.get(self.plan["run_id"])["result"], original["result"])


if __name__ == "__main__":
    unittest.main()
