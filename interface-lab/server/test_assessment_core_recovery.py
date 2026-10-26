"""Synthetic recovery/version regressions; no provider or application writes."""
from copy import deepcopy
import os
import unittest
from unittest.mock import patch

from test_assessment_core_bridge import BridgeTests
from assessment_core_lifecycle import Lifecycle, Scope, configured_scope, public_candidate_status
from domain import Problem


class RecoveryTests(unittest.TestCase):
    setUp = BridgeTests.setUp
    connect = BridgeTests.connect
    authorize = BridgeTests.authorize

    def lifecycle(self):
        with self.connect() as db:
            db.execute('ALTER TABLE cp_candidates ADD COLUMN created_at TEXT')
            db.execute('CREATE TABLE cp_interviews (candidate_id TEXT,status TEXT)')
            db.execute("INSERT INTO cp_interviews VALUES ('c1','completed')")
        original = self.collector.collect
        def collect(cid):
            value = original(cid)
            value['corpus']['sources'][0]['units'][0]['text'] += self.collector.revision
            value['manifest']['conversations'] = [{'owner_kind': 'interview', 'status': 'ready',
                'lifecycle_status': 'completed', 'primary_status': 'ready', 'recordings': [
                    {'kind': 'video', 'transcript_id': 'synthetic', 'selection': {'status': 'ready'}}]}]
            return value
        self.collector.collect = collect
        return Lifecycle(self.bridge, Scope(('c1',), batch_size=10))

    def test_return_to_source_and_rubric_reuses_actual_current_binding(self):
        self.lifecycle()
        first = self.bridge.start('c1')['coreRun']
        self.collector.revision = 'source-v2'
        second = self.bridge.start('c1')['coreRun']
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.collector.revision = 'source-v1'
        self.assertEqual(self.bridge.start('c1')['coreRun']['run_id'], first['run_id'])
        self.bridge._save(self.bridge.identity('c1', 'source-v2'), 'c1', second)
        self.assertEqual(self.bridge.read('c1')['coreRun']['run_id'], first['run_id'])
        original_rubric = deepcopy(self.bridge.rubric)
        self.bridge.rubric['parameters'][0]['title'] = 'A new rubric'
        from assessment_core_bridge import digest
        self.bridge.rubric_hash = digest(self.bridge.rubric)
        third = self.bridge.start('c1')['coreRun']
        self.assertNotEqual(first['run_id'], third['run_id'])
        self.bridge.rubric = original_rubric
        self.bridge.rubric_hash = digest(original_rubric)
        self.assertEqual(self.bridge.read('c1')['coreRun']['run_id'], first['run_id'])
        self.assertEqual(sum(call[0] == 'POST' for call in self.service.calls), 3)

    def test_acknowledged_old_work_recovers_after_outage_without_post(self):
        lifecycle = self.lifecycle()
        with patch('assessment_core_lifecycle.time.time', return_value=1000):
            lifecycle.tick()
        with self.connect() as db:
            db.execute("UPDATE ac_auto_work SET state='submitted',created=0,poll_errors=0")
        with patch.object(self.service, 'call', side_effect=Problem('offline', 503)):
            for now in (1100, 1200, 1300):
                with patch('assessment_core_lifecycle.time.time', return_value=now):
                    lifecycle.process()
        state = lifecycle.candidate_status('c1')
        self.assertEqual(state['state'], 'submitted')
        self.assertEqual(state['work']['poll_errors'], 3)
        calls = len(self.service.calls)
        lifecycle = Lifecycle(self.bridge, lifecycle.scope)
        with patch('assessment_core_lifecycle.time.time', return_value=1300):
            lifecycle.recover()
            lifecycle.process()
        self.assertEqual(len(self.service.calls), calls)
        with patch('assessment_core_lifecycle.time.time', return_value=2000):
            lifecycle.process()
        self.assertEqual(lifecycle.candidate_status('c1')['state'], 'completed')
        self.assertEqual(lifecycle.candidate_status('c1')['work']['poll_errors'], 0)
        self.assertEqual(sum(call[0] == 'POST' for call in self.service.calls), 1)

    def test_legacy_poll_failed_receipt_resumes_but_unknown_dispatch_never_does(self):
        lifecycle = self.lifecycle()
        lifecycle.tick()
        with self.connect() as db:
            db.execute("UPDATE ac_auto_work SET state='poll_failed',error_code='assessment_read_failed',poll_errors=3")
        with patch('assessment_core_lifecycle.time.time', return_value=2000000000):
            lifecycle.recover()
            lifecycle.process()
        self.assertEqual(lifecycle.candidate_status('c1')['state'], 'completed')
        with self.connect() as db:
            db.execute("UPDATE ac_auto_work SET state='dispatch_unknown',error_code='dispatch_not_acknowledged'")
        count = len(self.service.calls)
        lifecycle.recover()
        lifecycle.tick()
        self.assertEqual(lifecycle.candidate_status('c1')['state'], 'dispatch_unknown')
        self.assertEqual(len(self.service.calls), count)

    def test_all_completed_hr_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {'HR_ASSESSMENT_CORE_AUTO_ENABLED': '1',
                                   'HR_ASSESSMENT_CORE_AUTO_ALL_COMPLETED_HR': '1'}, clear=True):
            scope = configured_scope()
            self.assertTrue(scope.all_completed_hr)
        with patch.dict(os.environ, {'HR_ASSESSMENT_CORE_AUTO_ENABLED': '1'}, clear=True):
            with self.assertRaises(Problem):
                configured_scope()

    def test_success_resets_consecutive_errors_and_old_pending_run_keeps_observing(self):
        lifecycle = self.lifecycle()
        lifecycle.tick()
        with self.connect() as db:
            run_id = db.execute('SELECT run_id FROM ac_auto_work').fetchone()[0]
            db.execute("UPDATE ac_auto_work SET state='submitted',created=0")
        pending = self.service.values[run_id]
        pending['status'], pending['assessment'] = 'sent', None
        for now, fail, errors in [(1100, True, 1), (1105, False, 0), (1106, True, 1)]:
            with patch('assessment_core_lifecycle.time.time', return_value=now):
                if fail:
                    with patch.object(self.service, 'call', side_effect=Problem('offline', 503)):
                        lifecycle.process()
                else:
                    lifecycle.process()
            observed = lifecycle.candidate_status('c1')
            self.assertEqual(observed['state'], 'submitted')
            self.assertEqual(observed['work']['poll_errors'], errors)
            if fail:
                self.assertEqual(observed['work']['next_poll_at'], now + 5)
            else:
                self.assertEqual(observed['error_code'], 'assessment_delayed')
        self.assertEqual(sum(call[0] == 'POST' for call in self.service.calls), 1)

    def test_readiness_storage_failure_cannot_block_scan_or_receipt_read(self):
        lifecycle = self.lifecycle()
        with patch.object(self.bridge, 'save_source_evidence', side_effect=RuntimeError('unavailable')) as save:
            lifecycle.tick()
            self.assertEqual(lifecycle.candidate_status('c1')['state'], 'completed')
            self.assertEqual(self.bridge.read('c1')['state'], 'ready')
            save.assert_not_called()

    def test_all_completed_scope_discovers_historical_and_new_ids_but_blocks_partial_corpus(self):
        self.lifecycle()
        for cid in ('historical-other', 'partial-other'):
            with self.connect() as db:
                db.execute('INSERT INTO cp_candidates VALUES (?,?)', (cid, '2020-01-01T00:00:00Z'))
                db.execute('INSERT INTO cp_interviews VALUES (?,?)', (cid, 'completed'))
        snapshot = self.collector.collect('c1')
        def collect(cid):
            value = deepcopy(snapshot)
            value['candidate_id'] = value['corpus']['candidate_id'] = cid
            if cid == 'partial-other':
                value['status'] = 'blocked'
            return value
        with patch.object(self.bridge, 'authorize'), patch.object(self.collector, 'collect', side_effect=collect):
            lifecycle = Lifecycle(self.bridge, Scope(all_completed_hr=True, batch_size=1))
            self.assertEqual(lifecycle.scan(), 1)
            with self.connect() as db:
                db.execute("INSERT INTO cp_candidates VALUES ('new-arrival','2026-11-01T00:00:00Z')")
                db.execute("INSERT INTO cp_interviews VALUES ('new-arrival','completed')")
            for _ in range(4):
                lifecycle.scan()
            self.assertEqual(lifecycle.candidate_status('historical-other')['state'], 'queued')
            self.assertEqual(lifecycle.candidate_status('new-arrival')['state'], 'queued')
            self.assertEqual(lifecycle.candidate_status('partial-other')['state'], 'blocked')
            with self.connect() as db:
                self.assertFalse(db.execute("SELECT 1 FROM ac_auto_work WHERE candidate_id='partial-other'").fetchone())
        self.assertEqual(self.service.calls, [])

    def test_local_public_projection_and_access_denial_never_dispatch_or_keep_polling(self):
        lifecycle = self.lifecycle()
        lifecycle.tick()
        with self.connect() as db:
            db.execute("UPDATE ac_auto_work SET state='submitted'")
            db.execute("UPDATE ac_auto_candidates SET state='submitted'")
        with patch.object(self.bridge, 'authorize', side_effect=Problem('denied', 403)), patch.object(
                self.service, 'call') as service:
            lifecycle.process()
            lifecycle.process()
            service.assert_not_called()
        with self.connect() as db, patch.object(self.service, 'call') as service, patch.object(
                self.collector, 'collect') as sources:
            observed = public_candidate_status(db, 'c1')
            self.assertEqual(observed['state'], 'stale')
            self.assertEqual(observed['errorCode'], 'candidate_access_denied')
            self.assertIsNone(observed['retryAt'])
            service.assert_not_called()
            sources.assert_not_called()


if __name__ == '__main__':
    unittest.main()
