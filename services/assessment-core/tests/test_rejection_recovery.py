"""Offline reconciliation of retained bytes: exact hashes, crash safety, zero POSTs."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import stat
import unittest
from unittest.mock import patch

from assessment_core.__main__ import main
from assessment_core.corpus import canonical_json
from assessment_core.engine import digest
from assessment_core.providers.jev import JevError
from assessment_core.runstore import RunStore, RunUnavailable
import test_runstore as fixtures


class RejectionRecoveryTests(unittest.TestCase):
    setUp = fixtures.RunStoreTests.setUp

    def reject(self, response=None, raw=None, unknown=True):
        raw = raw if raw is not None else json.dumps(response or fixtures.response_fixture()).encode('utf-8')
        self.client.evaluate.side_effect = JevError('invalid_response', outcome_unknown=unknown, received_body=raw)
        with self.assertRaises(JevError):
            self.store.execute(self.plan, self.client)
        return raw, hashlib.sha256(raw).hexdigest()

    def rows(self):
        with self.store.db() as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
                    for table in ('runs', 'provider_rejections')}

    def test_recovery_preserves_machine_float_tie_raw_and_summary_is_private(self):
        response = fixtures.response_fixture()
        response['answers']['independence']['probabilities'] = {
            'guided': .33999999999999997, 'independent': .34, 'insufficient_data': .32}
        raw, body_hash = self.reject(response)
        run_id = self.plan['run_id']
        with self.store.db() as db:
            original_plan = db.execute('SELECT plan FROM runs').fetchone()[0]
            schema_before = [tuple(row) for row in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')]
        with patch('jev_client.JevClient.evaluate', side_effect=AssertionError('POST forbidden')) as provider, patch(
                'urllib.request.urlopen', side_effect=AssertionError('network forbidden')) as network:
            result = self.store.recover_rejection(run_id, body_hash)
            self.assertEqual(self.store.recover_rejection(run_id, body_hash), result)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['recover-rejection', run_id, '--db', str(self.path),
                                       '--expected-body-sha256', body_hash]), 0)
            summary = json.loads(output.getvalue())
            self.assertEqual(set(summary), {'run_id', 'status', 'provider_posts', 'body_sha256',
                                           'plan_sha256', 'response_sha256', 'audit_phase'})
            self.assertEqual(summary['provider_posts'], 0)
            provider.assert_not_called()
            network.assert_not_called()
        self.client.evaluate.assert_called_once()
        row = self.store.get(run_id)
        self.assertEqual((row['status'], row['error']), ('completed', None))
        self.assertEqual(row['response'], response)
        self.assertEqual(result['parameters'][0]['selected_level_id'], 'guided')
        self.assertEqual(result['parameters'][0]['levels'][0]['probability'], .33999999999999997)
        self.assertEqual(result['parameters'][0]['levels'][1]['probability'], .34)
        self.assertEqual(row['response_sha256'], digest(response))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT body FROM provider_rejections').fetchone()[0], raw)
            self.assertEqual([tuple(row) for row in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')],
                             schema_before)
        audit = self.store.recovery_audit(run_id)
        self.assertEqual(audit['original_error'], {'code': 'invalid_response', 'status': None})
        self.assertEqual(audit['original_status'], 'outcome_unknown')
        self.assertEqual(audit['body_sha256'], body_hash)
        self.assertEqual(audit['plan_sha256'], hashlib.sha256(original_plan.encode()).hexdigest())
        self.assertEqual(audit['response_sha256'], digest(response))
        self.assertEqual(audit['phase'], 'completed')
        directory = self.path.parent / 'operator-recoveries'
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((directory / (run_id + '.json')).stat().st_mode), 0o600)

    def test_definitive_invalid_response_can_be_recovered_but_other_errors_cannot(self):
        _, body_hash = self.reject(unknown=False)
        self.assertEqual(self.store.get(self.plan['run_id'])['status'], 'failed')
        self.store.recover_rejection(self.plan['run_id'], body_hash)
        self.assertEqual(self.store.recovery_audit(self.plan['run_id'])['original_status'], 'failed')
        self.client.evaluate.assert_called_once()

    def test_before_commit_interruption_retains_original_rows_and_prepared_audit_can_resume(self):
        _, body_hash = self.reject()
        original = self.rows()
        save = self.store._save_recovery_audit
        def interrupted(run_id, audit):
            save(run_id, audit)
            raise RuntimeError('crash before row promotion')
        with patch.object(self.store, '_save_recovery_audit', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.store.recover_rejection(self.plan['run_id'], body_hash)
        self.assertEqual(self.rows(), original)
        self.assertEqual(self.store.recovery_audit(self.plan['run_id'])['phase'], 'prepared')
        restarted = RunStore(self.path)
        restarted.recover_rejection(self.plan['run_id'], body_hash)
        self.assertEqual(restarted.get(self.plan['run_id'])['status'], 'completed')
        self.client.evaluate.assert_called_once()

    def test_after_promotion_interruption_startup_replay_and_explicit_resume_need_no_provider(self):
        _, body_hash = self.reject()
        with patch.object(self.store, 'replay', side_effect=RuntimeError('crash after saved response')):
            with self.assertRaises(RuntimeError):
                self.store.recover_rejection(self.plan['run_id'], body_hash)
        self.assertEqual(self.store.get(self.plan['run_id'])['status'], 'response_saved')
        self.assertEqual(self.store.recovery_audit(self.plan['run_id'])['phase'], 'prepared')
        restarted = RunStore(self.path)
        with restarted.runtime_lock():
            restarted.recover()
        self.assertEqual(restarted.get(self.plan['run_id'])['status'], 'completed')
        restarted.recover_rejection(self.plan['run_id'], body_hash)
        self.assertEqual(restarted.recovery_audit(self.plan['run_id'])['phase'], 'completed')
        self.client.evaluate.assert_called_once()

    def test_completed_row_before_completed_audit_resumes_and_does_not_resubmit(self):
        _, body_hash = self.reject()
        save = self.store._save_recovery_audit
        def interrupted(run_id, audit):
            if audit['phase'] == 'completed':
                raise RuntimeError('crash before completed audit')
            save(run_id, audit)
        with patch.object(self.store, '_save_recovery_audit', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.store.recover_rejection(self.plan['run_id'], body_hash)
        self.assertEqual(self.store.get(self.plan['run_id'])['status'], 'completed')
        self.assertEqual(self.store.recovery_audit(self.plan['run_id'])['phase'], 'prepared')
        result = self.store.recover_rejection(self.plan['run_id'], body_hash)
        cached = self.store.execute(self.plan, self.client)
        self.assertEqual(result, cached)
        self.client.evaluate.assert_called_once()

    def test_invalid_hash_owned_store_or_invalid_domain_leave_original_rows_untouched(self):
        _, body_hash = self.reject()
        original = self.rows()
        with self.assertRaisesRegex(RunUnavailable, 'body_hash_mismatch'):
            self.store.recover_rejection(self.plan['run_id'], '0' * 64)
        with self.store.runtime_lock():
            with self.assertRaisesRegex(RunUnavailable, 'store_in_use'):
                self.store.recover_rejection(self.plan['run_id'], body_hash)
        with patch('assessment_core.runstore.interpret', side_effect=ValueError('incompatible domain')):
            with self.assertRaises(ValueError):
                self.store.recover_rejection(self.plan['run_id'], body_hash)
        self.assertEqual(self.rows(), original)
        self.assertIsNone(self.store.recovery_audit(self.plan['run_id']))

    def test_strict_raw_decoder_rejects_duplicate_nonfinite_wrong_choice_and_malformed_json(self):
        for raw in (b'{"model":"one","model":"two"}', b'{"n":NaN}', b'PRIVATE INVALID JSON'):
            with self.subTest(raw=raw):
                # Each independent ledger keeps one immutable provider rejection.
                self.path = Path(self.directory.name) / hashlib.sha256(raw).hexdigest() / 'runs.sqlite3'
                self.store = RunStore(self.path)
                _, body_hash = self.reject(raw=raw)
                original = self.rows()
                with self.assertRaises(JevError):
                    self.store.recover_rejection(self.plan['run_id'], body_hash)
                self.assertEqual(self.rows(), original)
                self.assertIsNone(self.store.recovery_audit(self.plan['run_id']))
        response = fixtures.response_fixture()
        response['answers']['independence']['choice'] = 'independent'
        self.path = Path(self.directory.name) / 'wrong-choice' / 'runs.sqlite3'
        self.store = RunStore(self.path)
        _, body_hash = self.reject(response)
        original = self.rows()
        with self.assertRaises(JevError):
            self.store.recover_rejection(self.plan['run_id'], body_hash)
        self.assertEqual(self.rows(), original)

    def test_wrong_state_missing_rejection_and_changed_frozen_plan_are_rejected(self):
        _, body_hash = self.reject()
        run_id = self.plan['run_id']
        for status in ('queued', 'sent', 'response_saved', 'completed'):
            with self.store.db() as db:
                db.execute('UPDATE runs SET status=? WHERE id=?', (status, run_id))
            original = self.rows()
            with self.assertRaises(RunUnavailable):
                self.store.recover_rejection(run_id, body_hash)
            self.assertEqual(self.rows(), original)
        with self.store.db() as db:
            db.execute("UPDATE runs SET status='outcome_unknown',error=? WHERE id=?",
                       (canonical_json({'code': 'timeout'}), run_id))
        with self.assertRaisesRegex(RunUnavailable, 'state_conflict'):
            self.store.recover_rejection(run_id, body_hash)
        with self.store.db() as db:
            changed = deepcopy(self.plan)
            changed['request']['state']['chunks'][0]['text'] += ' tampered'
            db.execute('UPDATE runs SET plan=?,error=? WHERE id=?', (canonical_json(changed),
                       canonical_json({'code': 'invalid_response'}), run_id))
        original = self.rows()
        with self.assertRaises(ValueError):
            self.store.recover_rejection(run_id, body_hash)
        self.assertEqual(self.rows(), original)
        with self.store.db() as db:
            db.execute('DELETE FROM provider_rejections')
        with self.assertRaisesRegex(RunUnavailable, 'unavailable'):
            self.store.recover_rejection(run_id, body_hash)

    def test_resume_refuses_tampered_audit_or_saved_response(self):
        _, body_hash = self.reject()
        run_id = self.plan['run_id']
        self.store.recover_rejection(run_id, body_hash)
        audit = self.store.recovery_audit(run_id)
        self.store._save_recovery_audit(run_id, {**audit, 'plan_sha256': '0' * 64})
        with self.assertRaisesRegex(RunUnavailable, 'audit_hash_mismatch'):
            self.store.recover_rejection(run_id, body_hash)
        self.store._save_recovery_audit(run_id, audit)
        with self.store.db() as db:
            db.execute('UPDATE runs SET response=? WHERE id=?', (canonical_json({'untrusted': True}), run_id))
        with self.assertRaisesRegex(RunUnavailable, 'response_hash_mismatch'):
            self.store.recover_rejection(run_id, body_hash)

    def test_cli_failure_sanitizes_body_and_does_not_create_missing_database(self):
        raw, body_hash = self.reject(raw=b'PRIVATE CANDIDATE RAW')
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(['recover-rejection', self.plan['run_id'], '--db', str(self.path),
                         '--expected-body-sha256', body_hash])
        self.assertEqual(code, 2)
        self.assertNotIn('PRIVATE', output.getvalue() + errors.getvalue())
        self.assertEqual(output.getvalue(), '')
        missing = self.path.parent / 'does-not-exist.sqlite3'
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(main(['recover-rejection', self.plan['run_id'], '--db', str(missing),
                                   '--expected-body-sha256', body_hash]), 2)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
