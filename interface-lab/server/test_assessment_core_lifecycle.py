"""Durable HR commands -> real HTTP assessment process -> SQLite result.

Provider answers and materials are explicit synthetic fixtures; no paid API,
ASR, delivery, or production resources are used. The real canonical collector,
assessment planner, ledger, API, worker and result interpreter participate.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'services/assessment-core'))
sys.path.insert(0, str(ROOT / 'services/assessment-core/tests'))

from assessment_corpus import CandidateCorpusCollector, SQLiteResumeReader
from assessment_core_bridge import Bridge
from assessment_core_lifecycle import Lifecycle, Scope, configured_scope, ready_hr_interview, start_background
from core_source_client import ServiceHTTP
from domain import Problem
from test_hr_corpus import fixture, synthetic_rubric


TOKEN = 'a1' * 32
PROCESS = r'''
import json, sys, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from assessment_core.api import handler, worker
from assessment_core.runstore import RunStore
from assessment_core.providers.jev import JevError
db, address_file, calls_file, outcome = sys.argv[1:]
store = RunStore(db)
owner = store.runtime_lock()
owner.__enter__()
store.recover()
class FixtureProvider:
    def evaluate(self, request):
        with open(calls_file, 'a') as output:
            output.write(json.dumps(request) + '\n')
        if outcome == 'unknown':
            raise JevError('timeout', outcome_unknown=True)
        if outcome == 'invalid':
            raise JevError('invalid_response', received_body=b'PRIVATE INVALID RESPONSE')
        return {'model': 'jev-1.14.0', 'usage': {'input_tokens': 10, 'output_tokens': 1},
                'answers': {key: {'type': 'choice', 'choice': 'insufficient_data', 'confidence': 1.0,
                    'probabilities': {option: float(option == 'insufficient_data') for option in q['criteria']}}
                    for key, q in request['questions'].items()}}
server = ThreadingHTTPServer(('127.0.0.1', 0), handler(store, 'jev-1.14.0', 'a1'*32))
Path(address_file).write_text(str(server.server_port))
if outcome != 'paused':
    threading.Thread(target=worker, args=(store, FixtureProvider(), threading.Event()), daemon=True).start()
server.serve_forever(poll_interval=0.01)
'''


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class MaterialFixture:
    def __init__(self, data):
        self.data, self.http = data, self

    def call(self, method, path):
        return deepcopy(self.data.dossier)

    def primary_transcripts(self, kind, owner_id):
        return deepcopy(self.data.primaries[(kind, owner_id)])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.hr = self.dir / 'hr.db'
        self.process = None
        self.addCleanup(self.stop_core)
        self.data = fixture()
        self.data.candidate_id = self.data.dossier['candidate_id'] = self.data.cv['candidate_id'] = 'c1'
        with self.connect() as db:
            db.executescript('''CREATE TABLE cp_candidates (id TEXT PRIMARY KEY,profile TEXT DEFAULT '{}',
              updated_at TEXT,email TEXT,searching INTEGER DEFAULT 1,created_at TEXT);
              CREATE TABLE cp_interviews (id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT,
                completed_at TEXT,room TEXT);
              CREATE TABLE cp_intake (candidate_id TEXT,contact_email TEXT);
              CREATE TABLE cp_resumes (candidate_id TEXT PRIMARY KEY,filename TEXT,mime TEXT,
                text_content TEXT,content BLOB,uploaded_at TEXT);''')
            db.execute("INSERT INTO cp_candidates (id,email,created_at) VALUES ('c1','test@example.test','2026-10-26T00:00:00Z')")
            db.execute("INSERT INTO cp_interviews VALUES ('hr','c1','completed','2026-10-26T10:00:00Z',NULL)")
            db.execute('INSERT INTO cp_resumes VALUES (?,?,?,?,?,?)', ('c1', self.data.cv['filename'],
                self.data.cv['mime'], self.data.cv['text_content'], b'SYNTHETIC ORIGINAL CV BYTES', self.data.cv['uploaded_at']))
        self.collector = CandidateCorpusCollector(MaterialFixture(self.data), SQLiteResumeReader(self.hr),
                                                  authorize=self.authorize)
        self.rubric = synthetic_rubric()
        # Per-parameter scales differ; both survive the unchanged full-corpus plan.
        self.rubric['parameters'][1]['levels'].append({'level_id': 'three', 'label': 'Три', 'description': 'Третий уровень'})
        self.start_core('success')
        self.bridge = Bridge(self.connect, threading.RLock(), self.authorize, self.collector,
                             self.service, self.rubric, 'jev-1.14.0')
        self.lifecycle = Lifecycle(self.bridge, Scope(('c1',), batch_size=1))

    def connect(self):
        return sqlite3.connect(self.hr, factory=Connection)

    def authorize(self, cid):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM cp_candidates WHERE id=?', (cid,)).fetchone():
                raise Problem('denied', 403)

    def start_core(self, outcome):
        self.stop_core()
        address = self.dir / 'address'
        address.unlink(missing_ok=True)
        self.process = subprocess.Popen([sys.executable, '-u', '-c', PROCESS,
            str(self.dir / 'core.db'), str(address), str(self.dir / 'provider-calls'), outcome],
            env={**os.environ, 'PYTHONPATH': str(ROOT / 'services/assessment-core')},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while not address.exists():
            if self.process.poll() is not None:
                self.fail(self.process.stderr.read().decode())
            if time.monotonic() >= deadline:
                self.fail('core process startup deadline')
            time.sleep(.01)
        self.service = ServiceHTTP('http://127.0.0.1:' + address.read_text(), TOKEN, timeout=2)
        if hasattr(self, 'bridge'):
            self.bridge.service = self.service

    def stop_core(self):
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.process.stderr.close()
            self.process = None

    def await_state(self, states):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.lifecycle.process()
            status = self.lifecycle.candidate_status('c1')
            if status['state'] in states:
                return status
            time.sleep(.02)
        self.fail('lifecycle terminal state deadline: ' + str(self.lifecycle.status()))

    def calls(self):
        path = self.dir / 'provider-calls'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_ready_transition_full_corpus_completion_and_no_repeat_after_restart(self):
        owner = self.data.dossier['interviews'][0]
        owner['status'] = 'live'
        self.lifecycle.tick()
        self.assertEqual(self.lifecycle.candidate_status('c1')['state'], 'blocked')
        self.assertEqual(self.calls(), [])
        owner['status'] = 'completed'
        self.lifecycle.tick()
        self.lifecycle.tick()
        done = self.await_state({'completed'})
        request, = self.calls()
        snapshot = self.collector.collect('c1')
        self.assertEqual([c['text'] for c in request['state']['chunks']],
                         [u['text'] for source in snapshot['corpus']['sources'] for u in source['units']])
        self.assertEqual(len(request['questions']), 15)
        self.assertEqual(set(request['questions']['parameter-1']['criteria']),
                         {'one', 'two', 'three', 'insufficient_data'})
        self.assertEqual(set(request['questions']['parameter-0']['criteria']), {'one', 'two', 'insufficient_data'})
        self.assertNotIn('vacancy', request)
        self.start_core('success')
        self.lifecycle = Lifecycle(self.bridge, Scope(('c1',), batch_size=1))
        self.lifecycle.recover()
        for _ in range(3):
            self.lifecycle.tick()
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.bridge.read('c1')['coreRun']['run_id'], done['work']['run_id'])
        # Changing stored CV is a new complete corpus and a new durable command.
        with self.connect() as db:
            db.execute("UPDATE cp_resumes SET text_content=text_content || ' UPDATED'")
        self.lifecycle.tick()
        self.lifecycle.tick()
        changed = self.await_state({'completed'})
        self.assertNotEqual(done['work']['run_id'], changed['work']['run_id'])
        self.assertEqual(len(self.calls()), 2)

    def test_missing_second_part_and_audio_only_do_not_enroll(self):
        owner = next(o for o in self.data.dossier['interviews'] if o['id'] == 'interview-1')
        primary = self.data.primaries[('interview', 'interview-1')]
        primary['selections'][1]['status'] = 'processing'
        pending = primary['transcripts'].pop(1)
        owner['materials']['transcript_selections'] = deepcopy(primary['selections'])
        self.lifecycle.tick()
        self.assertEqual(self.lifecycle.candidate_status('c1')['state'], 'blocked')
        self.assertEqual(self.calls(), [])
        primary['selections'][1]['status'] = 'ready'
        primary['transcripts'].append(pending)
        owner['materials']['transcript_selections'] = deepcopy(primary['selections'])
        for interview in self.data.dossier['interviews']:
            for asset in interview['materials']['assets']:
                if asset['kind'] == 'video':
                    asset['kind'] = 'audio'
        self.lifecycle.tick()
        self.assertEqual(self.lifecycle.candidate_status('c1')['state'], 'blocked')
        self.assertFalse(ready_hr_interview(self.collector.collect('c1')))
        self.assertEqual(self.calls(), [])

    def test_durable_queued_work_and_core_queue_survive_restart(self):
        self.start_core('paused')
        self.lifecycle.scan()
        self.lifecycle = Lifecycle(self.bridge, Scope(('c1',), batch_size=1))
        self.lifecycle.recover()
        self.lifecycle.process()
        self.assertEqual(self.lifecycle.candidate_status('c1')['state'], 'submitted')
        self.assertEqual(self.calls(), [])
        self.start_core('success')
        done = self.await_state({'completed'})
        self.assertTrue(done['work']['run_id'])
        self.assertEqual(len(self.calls()), 1)

    def test_unknown_outcome_and_invalid_provider_never_retry_same_revision(self):
        for outcome, terminal in [('unknown', 'outcome_unknown'), ('invalid', 'failed')]:
            self.start_core(outcome)
            with self.connect() as db:
                db.execute('UPDATE cp_resumes SET text_content=text_content || ?', (outcome,))
            self.lifecycle.tick()
            self.await_state({terminal})
            count = len(self.calls())
            self.start_core('success')
            self.lifecycle = Lifecycle(self.bridge, Scope(('c1',), batch_size=1))
            self.lifecycle.recover()
            for _ in range(3):
                self.lifecycle.tick()
            self.assertEqual(self.lifecycle.candidate_status('c1')['state'], terminal)
            self.assertEqual(len(self.calls()), count)

    def test_interrupted_unacknowledged_dispatch_stays_visible_without_post(self):
        self.lifecycle.scan()
        with self.connect() as db:
            db.execute("UPDATE ac_auto_work SET state='dispatching'")
        self.lifecycle = Lifecycle(self.bridge, Scope(('c1',), batch_size=1))
        self.lifecycle.recover()
        self.lifecycle.tick()
        self.lifecycle.tick()
        self.assertEqual(self.lifecycle.candidate_status('c1')['state'], 'dispatch_unknown')
        self.assertEqual(self.bridge.read('c1')['state'], 'not_started')
        self.assertEqual(self.calls(), [])

    def test_lost_http_acknowledgement_does_not_repost_after_restart(self):
        service = self.bridge.service
        def lose_receipt(method, path, body=None):
            receipt = service.call(method, path, body)
            if method == 'POST':
                raise Problem('synthetic_transport_loss', 503)
            return receipt
        with patch.object(self.bridge, 'service') as uncertain:
            uncertain.call.side_effect = lose_receipt
            self.lifecycle.tick()
        self.assertEqual(self.lifecycle.candidate_status('c1')['state'], 'dispatch_unknown')
        # The real independent service accepted and completed the one command.
        deadline = time.monotonic() + 5
        while not self.calls() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(len(self.calls()), 1)
        self.lifecycle = Lifecycle(self.bridge, self.lifecycle.scope)
        self.lifecycle.recover()
        self.lifecycle.tick()
        self.lifecycle.tick()
        self.assertEqual(self.lifecycle.candidate_status('c1')['state'], 'dispatch_unknown')
        self.assertEqual(len(self.calls()), 1)

    def test_acknowledged_dispatch_recovery_only_reads_existing_core_run(self):
        self.lifecycle.scan()
        self.bridge.start('c1')
        with self.connect() as db:
            db.execute("UPDATE ac_auto_work SET state='dispatching'")
        self.lifecycle = Lifecycle(self.bridge, self.lifecycle.scope)
        self.lifecycle.recover()
        self.await_state({'completed'})
        self.assertEqual(len(self.calls()), 1)

    def test_get_and_diagnostics_do_not_enroll_or_dispatch(self):
        self.assertEqual(self.bridge.read('c1')['state'], 'not_started')
        self.assertEqual(self.lifecycle.status()['work'], {})
        self.assertEqual(self.lifecycle.candidate_status('c1'), {'state': 'not_enrolled'})
        self.assertEqual(self.calls(), [])

    def test_source_change_between_scan_and_dispatch_does_not_submit(self):
        self.lifecycle.scan()
        with self.connect() as db:
            db.execute("UPDATE cp_resumes SET text_content=text_content || ' CHANGED BEFORE DISPATCH'")
        self.lifecycle.process()
        self.assertEqual(self.lifecycle.candidate_status('c1')['state'], 'superseded')
        self.assertEqual(self.calls(), [])

    def test_repeated_poll_failures_are_bounded_across_restart(self):
        self.start_core('paused')
        self.lifecycle.tick()
        self.stop_core()
        self.lifecycle.process()
        self.lifecycle = Lifecycle(self.bridge, self.lifecycle.scope)
        self.lifecycle.recover()
        self.lifecycle.process()
        self.lifecycle.process()
        result = self.lifecycle.candidate_status('c1')
        self.assertEqual(result['state'], 'poll_failed')
        self.assertEqual(result['work']['poll_errors'], 3)
        self.lifecycle.process()
        self.assertEqual(self.lifecycle.candidate_status('c1')['work']['poll_errors'], 3)
        self.assertEqual(self.calls(), [])

    def test_runtime_ownership_and_shutdown_release_are_real(self):
        settings = {'HR_ASSESSMENT_CORE_AUTO_ENABLED': '1', 'HR_ASSESSMENT_CORE_AUTO_CANDIDATE_IDS': 'c1'}
        with patch.dict(os.environ, settings, clear=True), patch('assessment_core_bridge.configured', return_value=self.bridge):
            runtime = start_background(self.connect, self.bridge.lock, self.authorize)
            try:
                with self.assertRaises(Problem):
                    start_background(self.connect, self.bridge.lock, self.authorize)
            finally:
                self.assertTrue(runtime.stop())
            runtime = start_background(self.connect, self.bridge.lock, self.authorize)
            self.assertTrue(runtime.stop())

    def test_scope_cursor_bounds_and_explicit_future_cutoff(self):
        with self.connect() as db:
            for cid, created in [('c0', '2020-01-01T00:00:00Z'), ('c2', '2026-10-27T00:00:00Z')]:
                db.execute('INSERT INTO cp_candidates (id,email,created_at) VALUES (?,?,?)', (cid, cid+'@test', created))
                db.execute('INSERT INTO cp_interviews VALUES (?,?,\'completed\',NULL,NULL)', ('hr-'+cid, cid))
        with patch.object(self.bridge.collector, 'collect', side_effect=ValueError('PRIVATE SOURCE')):
            future = Lifecycle(self.bridge, Scope(created_since='2026-10-26T00:00:00Z', batch_size=1))
            self.assertEqual(future.scan(), 1)
            self.assertEqual(future.status()['cursor'], 'c1')
            future = Lifecycle(self.bridge, future.scope)
            self.assertEqual(future.scan(), 1)
            self.assertEqual(future.status()['cursor'], 'c2')
            self.assertEqual(future.scan(), 0)
            self.assertEqual(future.status()['cursor'], '')
            self.assertEqual(future.candidate_status('c0'), {'state': 'not_enrolled'})
            self.assertNotIn('PRIVATE SOURCE', json.dumps(future.candidate_status('c2')))

    def test_configuration_is_opt_in_and_refuses_unbounded_historical_scope(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(configured_scope())
        with patch.dict(os.environ, {'HR_ASSESSMENT_CORE_AUTO_ENABLED': '1'}, clear=True):
            with self.assertRaises(Problem):
                configured_scope()
        with self.assertRaises(ValueError):
            Scope(created_since='2026-10-26')


if __name__ == '__main__':
    unittest.main()
