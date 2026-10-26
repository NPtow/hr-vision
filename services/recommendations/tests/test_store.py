from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from recommendations.engine import InvalidInput, canonical_json
from recommendations.store import ResultStore, StoreUnavailable
from fixtures import request_fixture, synthetic_rubric


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "private" / "results.db"
        self.rubric = synthetic_rubric()
        self.value = request_fixture(self.rubric)
        self.store = ResultStore(self.path, rubric=self.rubric)

    def test_committed_snapshot_restart_and_read_never_recalculates(self):
        result = self.store.submit(self.value)
        operation_id = result["recommendation_id"]
        with self.store.db() as db:
            row = db.execute("SELECT * FROM recommendations").fetchone()
            self.assertEqual(row["input"], canonical_json(self.value))
            self.assertEqual(json.loads(row["result"]), result)
        new_store = ResultStore(self.path, rubric=self.rubric)
        with patch("recommendations.store.recommend", side_effect=AssertionError("must not calculate")):
            self.assertEqual(new_store.get(operation_id), result)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertTrue(new_store.ready())

    def test_concurrent_duplicate_submissions_store_one_result_and_id(self):
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda _: self.store.submit(deepcopy(self.value)), range(16)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(self.store.metadata()["saved_results"], 1)
        self.value["candidate_fields"][0]["revision"] = "fields-v2"
        changed = self.store.submit(self.value)
        self.assertNotEqual(changed["recommendation_id"], results[0]["recommendation_id"])
        self.assertEqual(self.store.metadata()["saved_results"], 2)

    def test_rejection_is_atomic_and_never_persists_partial_pool(self):
        value = request_fixture(self.rubric, count=2)
        value["candidate_profiles"][1]["rubric_sha256"] = "f" * 64
        with self.assertRaises(InvalidInput):
            self.store.submit(value)
        self.assertEqual(self.store.metadata()["saved_results"], 0)

    def test_sqlite_backup_restore_preserve_inputs_and_exact_immutable_results(self):
        first = self.store.submit(self.value)
        self.value["candidate_fields"][0]["revision"] = "v2"
        second = self.store.submit(self.value)
        backup = Path(self.temporary.name) / "backup.sqlite3"
        restored_path = Path(self.temporary.name) / "restored.sqlite3"
        self.assertEqual(self.store.backup(backup), {"status": "completed", "saved_results": 2})
        self.assertEqual(ResultStore.restore(backup, restored_path), {"status": "completed", "saved_results": 2})
        restored = ResultStore(restored_path, rubric=self.rubric)
        for result in (first, second):
            self.assertEqual(restored.get(result["recommendation_id"]), result)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(restored_path.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            self.store.backup(backup)
        with self.assertRaises(FileExistsError):
            ResultStore.restore(backup, restored_path)

    def test_saved_result_or_input_tampering_is_reported_without_repairing_on_get(self):
        result = self.store.submit(self.value)
        operation_id = result["recommendation_id"]
        with self.store.db() as db:
            db.execute("UPDATE recommendations SET result=? WHERE id=?", ("{}", operation_id))
        with self.assertRaisesRegex(StoreUnavailable, "integrity_error"):
            self.store.get(operation_id)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT result FROM recommendations").fetchone()[0], "{}")
        backup = Path(self.temporary.name) / "corrupt-backup.db"
        self.store.backup(backup)
        target = Path(self.temporary.name) / "bad-restore.db"
        with self.assertRaisesRegex(StoreUnavailable, "integrity_error"):
            ResultStore.restore(backup, target)
        self.assertFalse(target.exists())

    def test_runtime_lock_prevents_two_process_owners(self):
        second = ResultStore(self.path, rubric=self.rubric)
        with self.store.runtime_lock():
            with self.assertRaisesRegex(StoreUnavailable, "store_in_use"):
                with second.runtime_lock():
                    pass
        with second.runtime_lock():
            pass
        with self.assertRaisesRegex(StoreUnavailable, "not_found"):
            second.get("0" * 64)

    def test_read_does_not_recreate_missing_database(self):
        self.path.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            self.store.get("0" * 64)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
