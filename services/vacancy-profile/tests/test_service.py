import copy
import http.client
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from contextlib import redirect_stdout

from jev_client import JevError
from vacancy_profile.engine import UNKNOWN, digest, interpret, prepare, validate_plan
from vacancy_profile.store import RunStore, RunUnavailable
from vacancy_profile.ops import backup
from vacancy_profile.api import BoundedHTTPServer, handler, work_once

ROOT = Path(__file__).resolve().parents[3]
RUBRIC = json.loads((ROOT / 'packages/assessment-contracts/rubrics/sales-v1.json').read_text())
TOKEN = 'a' * 64


def vacancy(revision='v1'):
    return {'vacancy_id': 'v1', 'revision': revision,
            'data': {'title': 'B2B sales', 'description': 'Full vacancy text\n' * 100,
                     'clarifications': [{'question': 'tasks?', 'answer': 'new sales'}]}}


def response(plan, level=None, importance='I2'):
    answers = {}
    for key, question in plan['request']['questions'].items():
        options = question['criteria']
        choice = (importance if key.endswith(':importance') else level or next(iter(options)))
        answers[key] = {'type': 'choice', 'choice': choice, 'confidence': .9,
                        'probabilities': {option: int(option == choice) for option in options}}
    return {'model': plan['request']['model'], 'answers': answers,
            'usage': {'input_tokens': 123, 'output_tokens': 4}}


class Fake:
    calls = 0

    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def evaluate(self, request):
        self.calls += 1
        self.request = copy.deepcopy(request)
        if self.error:
            raise self.error
        return copy.deepcopy(self.value)


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.plan = prepare(vacancy(), RUBRIC)

    def test_full_snapshot_catalog_and_two_choices(self):
        plan = self.plan
        self.assertEqual(plan['request']['state'], {'vacancy': vacancy()})
        self.assertEqual(len(plan['request']['questions']), 2 * len(RUBRIC['parameters']))
        self.assertEqual(plan['rubric']['sha256'], digest(RUBRIC))
        self.assertEqual(plan['rubric']['version'], '2026-10-26.behavioral-anchors.1')
        for p in RUBRIC['parameters']:
            q = plan['request']['questions'][p['parameter_id'] + ':level']
            self.assertEqual({l['level_id']: l['description'] for l in p['levels']},
                             {k: v for k, v in q['criteria'].items() if k != UNKNOWN})
            self.assertIn(p['definition'], q['instructions'])
            importance = plan['request']['questions'][p['parameter_id'] + ':importance']
            for level in p['levels']:
                self.assertIn(level['description'], importance['instructions'])
        self.assertEqual(prepare(vacancy(), RUBRIC), plan)
        changed = vacancy()
        changed['data']['description'] += ' changed'
        self.assertNotEqual(prepare(changed, RUBRIC)['run_id'], plan['run_id'])

    def test_unknown_is_not_zero(self):
        result = interpret(self.plan, response(self.plan, level=UNKNOWN))
        self.assertTrue(all(p['status'] == 'needs_review' and p['importance'] == 2 for p in result['parameters']))
        result = interpret(self.plan, response(self.plan, importance=UNKNOWN))
        self.assertTrue(all(p['status'] == 'needs_review' and p['importance'] == UNKNOWN for p in result['parameters']))
        result = interpret(self.plan, response(self.plan, level=UNKNOWN, importance='I0'))
        self.assertTrue(all(p['status'] == 'irrelevant' and p['importance'] == 0 for p in result['parameters']))

    def test_tampered_or_candidate_input_rejected(self):
        for field in ('state', 'questions'):
            broken = copy.deepcopy(self.plan)
            broken['request'][field] = {}
            with self.assertRaises(ValueError):
                validate_plan(broken)
        for wrong in ({'candidate_id': 'c', 'data': {}}, {**vacancy(), 'corpus': 'private'},
                      {**vacancy(), 'data': {'nonfinite': float('nan')}}, {**vacancy(), 'data': {2: 'x'}}):
            with self.assertRaises(ValueError):
                prepare(wrong, RUBRIC)

    def test_invalid_choice_probabilities_rejected(self):
        raw = response(self.plan)
        next(iter(raw['answers'].values()))['probabilities'] = {'bad': 1}
        with self.assertRaises(JevError):
            interpret(self.plan, raw)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RunStore(Path(self.temp.name) / 'runs.sqlite3')
        self.plan = prepare(vacancy(), RUBRIC)
        self.client = Fake(response(self.plan))

    def test_idempotent_queue_raw_replay_and_permissions(self):
        first = self.store.submit(self.plan)
        self.assertEqual(first['status'], 'queued')
        self.assertEqual(self.store.submit(self.plan)['id'], first['id'])
        self.assertTrue(work_once(self.store, self.client))
        result = self.store.replay(first['id'])
        self.assertEqual(result['profile_id'], first['id'])
        self.assertEqual(self.client.calls, 1)
        self.assertFalse(work_once(self.store, self.client))
        self.assertEqual(self.store.execute(self.plan, self.client), result)
        self.assertEqual(self.client.calls, 1)
        self.assertEqual(self.store.get(first['id'])['response'], self.client.value)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_timeout_and_rejection_are_private_and_not_retried(self):
        self.store.submit(self.plan)
        client = Fake(error=JevError('timeout', outcome_unknown=True, received_body=b'PRIVATE'))
        self.assertFalse(work_once(self.store, client))
        self.assertEqual(self.store.get(self.plan['run_id'])['status'], 'outcome_unknown')
        self.assertEqual(self.store.submit(self.plan)['status'], 'outcome_unknown')
        self.assertFalse(work_once(self.store, client))
        self.assertEqual(client.calls, 1)
        self.assertNotIn('PRIVATE', json.dumps(self.store.diagnostics(self.plan['run_id'])))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT body FROM provider_rejections').fetchone()[0], b'PRIVATE')

    def test_known_provider_failure_is_failed(self):
        self.store.submit(self.plan)
        self.assertFalse(work_once(self.store, Fake(error=JevError('authentication', status=401))))
        self.assertEqual(self.store.get(self.plan['run_id'])['status'], 'failed')

    def test_recovery_saved_and_interrupted_and_queued(self):
        self.store.submit(self.plan)
        other = prepare(vacancy('v2'), RUBRIC)
        queued = prepare(vacancy('v3'), RUBRIC)
        self.store.submit(other)
        self.store.submit(queued)
        raw = response(self.plan)
        with self.store.db() as db:
            db.execute("UPDATE runs SET status='response_saved',response=?,response_sha256=? WHERE id=?",
                       (json.dumps(raw), digest(raw), self.plan['run_id']))
            db.execute("UPDATE runs SET status='sent' WHERE id=?", (other['run_id'],))
        recovered = RunStore(self.store.path)
        with recovered.runtime_lock():
            recovered.recover()
        self.assertEqual(recovered.get(self.plan['run_id'])['status'], 'completed')
        self.assertEqual(recovered.get(other['run_id'])['status'], 'outcome_unknown')
        self.assertEqual(recovered.get(queued['run_id'])['status'], 'queued')
        self.assertEqual(recovered.profile(self.plan['run_id'])['run_id'], self.plan['run_id'])

    def test_corrupt_saved_response_fails_recovery_without_retry(self):
        self.store.submit(self.plan)
        with self.store.db() as db:
            db.execute("UPDATE runs SET status='response_saved',response='{}',response_sha256='bad' WHERE id=?",
                       (self.plan['run_id'],))
        self.store.recover()
        self.assertEqual(self.store.get(self.plan['run_id'])['status'], 'failed')
        self.assertEqual(self.store.get(self.plan['run_id'])['error']['code'], 'invalid_saved_response')
        self.assertFalse(work_once(self.store, self.client))
        self.assertEqual(self.client.calls, 0)

    def rejected_response(self, raw):
        self.store.submit(self.plan)
        client = Fake(error=JevError('invalid_response', outcome_unknown=True, received_body=raw))
        self.assertFalse(work_once(self.store, client))
        return hashlib.sha256(raw).hexdigest(), client

    def test_explicit_rejection_recovery_rounded_raw_and_cli_zero_posts(self):
        raw_response = response(self.plan)
        first_answers = list(raw_response['answers'].values())[:2]
        for answer in first_answers:
            answer['probabilities'][answer['choice']] = .99
        raw = json.dumps(raw_response).encode()
        body_hash, client = self.rejected_response(raw)
        run_id = self.plan['run_id']
        with mock.patch('jev_client.JevClient.evaluate', side_effect=AssertionError('POST forbidden')) as provider:
            profile = self.store.recover_rejection(run_id, body_hash)
            self.assertEqual(self.store.recover_rejection(run_id, body_hash), profile)
            output = io.StringIO()
            with mock.patch('sys.argv', ['ops', 'recover-rejection', run_id, '--db', str(self.store.path),
                                         '--expected-body-sha256', body_hash]), redirect_stdout(output):
                from vacancy_profile.ops import main
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.getvalue())['provider_posts'], 0)
            provider.assert_not_called()
        self.assertEqual(client.calls, 1)
        row = self.store.get(run_id)
        self.assertEqual((row['status'], row['error']), ('completed', None))
        self.assertEqual(row['response'], raw_response)
        self.assertEqual(self.store.current_profile(run_id), profile)
        self.assertEqual(sum(profile['parameters'][0]['raw_answers']['level']['probabilities'].values()), .99)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT body FROM provider_rejections').fetchone()[0], raw)
        audit = self.store.recovery_audit(run_id)
        self.assertEqual(audit['body_sha256'], body_hash)
        self.assertEqual(audit['original_error']['code'], 'invalid_response')
        self.assertEqual(audit['phase'], 'completed')
        audit_path = self.store.path.parent / 'operator-recoveries' / (run_id + '.json')
        self.assertEqual(audit_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(audit_path.parent.glob('*.json'))), 1)

    def test_rejection_recovery_interruption_resumes_without_schema_change(self):
        raw = json.dumps(response(self.plan)).encode()
        body_hash, _ = self.rejected_response(raw)
        run_id = self.plan['run_id']
        with self.store.db() as db:
            schema_before = [tuple(row) for row in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')]
        with mock.patch.object(self.store, 'replay', side_effect=RuntimeError('interrupted after saved response')):
            with self.assertRaises(RuntimeError):
                self.store.recover_rejection(run_id, body_hash)
        self.assertEqual(self.store.get(run_id)['status'], 'response_saved')
        self.assertEqual(self.store.recovery_audit(run_id)['phase'], 'prepared')
        restarted = RunStore(self.store.path)
        with restarted.runtime_lock():
            restarted.recover()
        self.assertEqual(restarted.get(run_id)['status'], 'completed')
        profile = restarted.recover_rejection(run_id, body_hash)
        self.assertEqual(profile['run_id'], run_id)
        self.assertEqual(restarted.recovery_audit(run_id)['phase'], 'completed')
        with restarted.db() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')],
                             schema_before)

    def test_rejection_recovery_refuses_hash_invalid_body_or_owned_database(self):
        raw = json.dumps(response(self.plan)).encode()
        body_hash, _ = self.rejected_response(raw)
        original = self.store.get(self.plan['run_id'])
        with self.assertRaisesRegex(RunUnavailable, 'hash_mismatch'):
            self.store.recover_rejection(self.plan['run_id'], '0' * 64)
        with self.store.runtime_lock():
            with self.assertRaisesRegex(RunUnavailable, 'store_in_use'):
                self.store.recover_rejection(self.plan['run_id'], body_hash)
        self.assertEqual(self.store.get(self.plan['run_id']), original)
        invalid = b'{"invalid":"PRIVATE RAW"}'
        with self.store.db() as db:
            db.execute('UPDATE provider_rejections SET body=?', (invalid,))
        with self.assertRaises(JevError):
            self.store.recover_rejection(self.plan['run_id'], hashlib.sha256(invalid).hexdigest())
        self.assertEqual(self.store.get(self.plan['run_id']), original)
        self.assertIsNone(self.store.recovery_audit(self.plan['run_id']))

    def test_rejection_recovery_refuses_wrong_states_no_body_or_wrong_error(self):
        raw = json.dumps(response(self.plan)).encode()
        body_hash, _ = self.rejected_response(raw)
        run_id = self.plan['run_id']
        for status in ('queued', 'sent', 'failed', 'response_saved', 'completed'):
            with self.store.db() as db:
                db.execute('UPDATE runs SET status=? WHERE id=?', (status, run_id))
            original = self.store.get(run_id)
            with self.assertRaises(RunUnavailable):
                self.store.recover_rejection(run_id, body_hash)
            self.assertEqual(self.store.get(run_id), original)
        with self.store.db() as db:
            db.execute("UPDATE runs SET status='outcome_unknown',error=? WHERE id=?",
                       (json.dumps({'code': 'timeout'}), run_id))
        with self.assertRaisesRegex(RunUnavailable, 'state_conflict'):
            self.store.recover_rejection(run_id, body_hash)
        with self.store.db() as db:
            db.execute('DELETE FROM provider_rejections WHERE run_id=?', (run_id,))
        with self.assertRaisesRegex(RunUnavailable, 'unavailable'):
            self.store.recover_rejection(run_id, body_hash)

    def test_exclusive_owner(self):
        with self.store.runtime_lock():
            with self.assertRaisesRegex(RunUnavailable, 'store_in_use'):
                with RunStore(self.store.path).runtime_lock():
                    pass

    def test_manual_revisions_immutable_conflict_and_idempotence(self):
        base = self.store.execute(self.plan, self.client)
        pid = base['parameters'][0]['parameter_id']
        changes = [{'parameter_id': pid, 'required_level_id': None, 'importance': 3}]
        revision = self.store.revision(base['run_id'], base['profile_id'], 'hr:alice', changes)
        self.assertNotEqual(revision['profile_id'], base['profile_id'])
        self.assertEqual(revision['parameters'][0]['status'], 'needs_review')
        self.assertEqual(revision['parameters'][0]['raw_answers'], base['parameters'][0]['raw_answers'])
        self.assertEqual(self.store.profile(base['profile_id']), base)
        self.assertEqual(self.store.replay(base['run_id']), base)
        self.assertEqual(self.store.current_profile(base['run_id']), revision)
        self.assertEqual(self.store.revision(base['run_id'], base['profile_id'], 'hr:alice', changes), revision)
        with self.assertRaisesRegex(RunUnavailable, 'revision_conflict'):
            self.store.revision(base['run_id'], base['profile_id'], 'hr:bob', changes)
        for weight in (True, -1, 4, None):
            bad = [{**changes[0], 'importance': weight}]
            with self.assertRaises(ValueError):
                self.store.revision(base['run_id'], revision['profile_id'], 'hr:alice', bad)

    def test_queue_backpressure_does_not_drop_work(self):
        plans = [prepare(vacancy(str(i)), RUBRIC) for i in range(101)]
        for p in plans[:100]:
            self.store.submit(p)
        self.assertEqual(self.store.submit(plans[0])['status'], 'queued')
        with self.assertRaisesRegex(RunUnavailable, 'queue_full'):
            self.store.submit(plans[100])
        self.assertEqual(self.store.diagnostics()['jobs'], {'queued': 100})

    def test_backup_restore_keeps_raw_and_revision(self):
        base = self.store.execute(self.plan, self.client)
        manual = self.store.revision(base['run_id'], base['profile_id'], 'hr:backup-test',
            [{'parameter_id': base['parameters'][0]['parameter_id'], 'required_level_id': None, 'importance': 0}])
        target = Path(self.temp.name) / 'restored.sqlite3'
        with self.store.runtime_lock():
            with self.assertRaises(RunUnavailable):
                backup(self.store.path, target)
        stats = backup(self.store.path, target)
        self.assertEqual(stats['counts']['profiles'], 2)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        restored = RunStore(target)
        self.assertEqual(restored.replay(base['run_id']), base)
        self.assertEqual(restored.current_profile(base['run_id']), manual)
        self.assertEqual(restored.get(base['run_id'])['response'], self.client.value)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RunStore(Path(self.temp.name) / 'runs.sqlite3')
        self.server = BoundedHTTPServer(('127.0.0.1', 0), handler(self.store, 'jev-1.14.0', TOKEN))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, value=None, auth=TOKEN, raw=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        hs = {'Content-Type': 'application/json'}
        if auth:
            hs['Authorization'] = 'Bearer ' + auth
        hs.update(headers or {})
        body = raw if raw is not None else json.dumps(value).encode() if value is not None else None
        connection.request(method, path, body=body, headers=hs)
        incoming = connection.getresponse()
        result = incoming.status, json.loads(incoming.read())
        connection.close()
        return result

    def test_real_http_completion_and_revision_paths(self):
        code, queued = self.request('POST', '/v1/vacancy-profiles', {'vacancy': vacancy(), 'rubric': RUBRIC})
        self.assertEqual(code, 202)
        plan = self.store.get(queued['run_id'])['plan']
        self.assertTrue(work_once(self.store, Fake(response(plan, importance=UNKNOWN))))
        path = '/v1/vacancy-profiles/' + queued['run_id']
        code, completed = self.request('GET', path)
        self.assertEqual((code, completed['status']), (200, 'completed'))
        profile = completed['profile']
        changes = [{'parameter_id': profile['parameters'][0]['parameter_id'],
                    'required_level_id': profile['parameters'][0]['levels'][1]['level_id'], 'importance': 3}]
        body = {'expected_parent_id': profile['profile_id'], 'author': 'hr:test', 'changes': changes}
        code, revised = self.request('POST', path + '/revisions', body)
        self.assertEqual(code, 201)
        rid = revised['profile']['profile_id']
        self.assertEqual(self.request('GET', '/v1/vacancy-profiles/profiles/' + rid)[1], revised)
        self.assertEqual(self.request('GET', path + '/current')[1], revised)
        self.assertEqual(self.request('GET', path)[1], completed)
        body['author'] = 'hr:other'
        self.assertEqual(self.request('POST', path + '/revisions', body)[0], 409)
        self.assertNotIn('description', json.dumps(self.request('GET', path + '/diagnostics')[1]))

    def test_auth_health_readiness_and_invalid_json(self):
        self.assertEqual(self.request('GET', '/healthz', auth=None)[0], 200)
        self.assertEqual(self.request('GET', '/readyz', auth=None)[0], 401)
        self.assertEqual(self.request('GET', '/readyz')[0], 200)
        self.assertEqual(self.request('POST', '/v1/vacancy-profiles', {}, auth='b' * 64)[0], 401)
        for raw in (b'{"vacancy":1,"vacancy":2}', b'{"private":NaN}', b'[]', b'\xff'):
            code, result = self.request('POST', '/v1/vacancy-profiles', raw=raw)
            self.assertEqual((code, result), (422, {'error': 'invalid_input'}))
        self.assertEqual(self.request('POST', '/v1/vacancy-profiles', raw=b'{}',
                                     headers={'Content-Length': str(4 * 1024 * 1024 + 1)})[0], 413)
        self.assertEqual(self.request('POST', '/v1/vacancy-profiles', {'vacancy': vacancy(), 'rubric': RUBRIC, 'candidate': {}})[0], 422)
        self.assertEqual(self.request('GET', '/v1/vacancy-profiles/' + 'f' * 64)[0], 404)


class ProcessTests(unittest.TestCase):
    def test_independent_process_recovers_without_provider_and_restart_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RunStore(Path(temp) / 'runs.sqlite3')
            plan = prepare(vacancy(), RUBRIC)
            other = prepare(vacancy('interrupted'), RUBRIC)
            store.submit(plan)
            store.submit(other)
            raw = response(plan)
            with store.db() as db:
                db.execute("UPDATE runs SET status='response_saved',response=?,response_sha256=? WHERE id=?",
                           (json.dumps(raw), digest(raw), plan['run_id']))
                db.execute("UPDATE runs SET status='sent' WHERE id=?", (other['run_id'],))
            with socket.socket() as reserved:
                reserved.bind(('127.0.0.1', 0))
                port = reserved.getsockname()[1]
            env = {**os.environ, 'VACANCY_PROFILE_SERVICE_TOKEN': TOKEN, 'VACANCY_PROFILE_JEV_API_KEY': 'test-key',
                   'VACANCY_PROFILE_DB': str(store.path), 'VACANCY_PROFILE_PORT': str(port)}
            for cycle in range(2):
                process = subprocess.Popen([sys.executable, '-m', 'vacancy_profile.api'], cwd=ROOT,
                                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 8
                    while True:
                        try:
                            c = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                            c.request('GET', '/readyz', headers={'Authorization': 'Bearer ' + TOKEN})
                            r = c.getresponse()
                            r.read()
                            c.close()
                            if r.status == 200:
                                break
                        except OSError:
                            pass
                        if process.poll() is not None or time.monotonic() >= deadline:
                            self.fail('service did not become ready')
                        time.sleep(.05)
                    c = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                    c.request('GET', '/v1/vacancy-profiles/' + plan['run_id'], headers={'Authorization': 'Bearer ' + TOKEN})
                    incoming = c.getresponse()
                    value = json.loads(incoming.read())
                    c.close()
                    self.assertEqual((incoming.status, value['status']), (200, 'completed'))
                    self.assertEqual(value['profile']['profile_id'], plan['run_id'])
                    self.assertEqual(store.get(other['run_id'])['status'], 'outcome_unknown')
                finally:
                    process.send_signal(signal.SIGINT)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 0, stderr.decode())
                    self.assertNotIn(b'Full vacancy text', stdout + stderr)


class ReleaseTests(unittest.TestCase):
    def test_release_imports_and_catalog_without_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'release'
            subprocess.run([sys.executable, str(ROOT / 'services/vacancy-profile/deploy/build_release.py'),
                            str(destination)], check=True, stdout=subprocess.DEVNULL)
            self.assertFalse(list(destination.rglob('*.pyc')))
            env = {**os.environ, 'PYTHONPATH': str(destination)}
            subprocess.run([sys.executable, '-I', '-B', '-c',
                "import sys; sys.path.insert(0, sys.argv[1]); import vacancy_profile.api, jev_client", str(destination)],
                cwd=temp, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(json.loads((destination / 'rubrics/sales-v1.json').read_text()), RUBRIC)
            manifest = json.loads((destination / 'release-manifest.json').read_text())
            self.assertIn('jev_client/__init__.py', manifest['files'])
            self.assertIn('vacancy_profile/api.py', manifest['files'])
            self.assertFalse(list(destination.rglob('*.pyc')))


if __name__ == '__main__':
    unittest.main()
