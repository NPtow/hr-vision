"""Independent acceptance: three OS processes + actual HR bridges + real SQLite.

PROVIDER EVIDENCE BOUNDARY: responses below are SYNTHETIC typed Choice fixtures,
not recordings and not live JEV. They are seeded as response_saved, then replayed
by unmodified production processes. SourceCollector is an explicit source-boundary
fixture (the materials Core is not started). No runtime transport is monkeypatched.
Run (Python with hashlib.scrypt): /opt/homebrew/bin/python3 -m unittest discover -s services/tests/assessment-flow -v
"""
from contextlib import closing
import http.client
import json
import math
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
for relative in ('interface-lab/server', 'services/assessment-core', 'services/vacancy-profile',
                 'services/recommendations', 'packages/jev-client'):
    sys.path.insert(0, str(ROOT / relative))

from assessment_core.engine import prepare as candidate_plan
from assessment_core.runstore import RunStore as CandidateStore
from vacancy_profile.engine import prepare as vacancy_plan
from vacancy_profile.store import RunStore as VacancyStore
import assessment_core_bridge as candidates
from assessment_corpus import CollectionError
import vacancy_profile_bridge as vacancies
import recommendations_bridge as recommendations
from core_source_client import ServiceHTTP
from domain import Problem
import selection
import selection_facts as facts
import test_selection as portal_fixture

TOKEN = 'e1' * 32
RUBRIC_PATH = ROOT / 'packages/assessment-contracts/rubrics/sales-v1.json'
RUBRIC = json.loads(RUBRIC_PATH.read_text())


def typed_response(plan, *, unknown=False, vacancy=False):
    answers = {}
    for qid, question in plan['request']['questions'].items():
        keys = list(question['criteria'])
        if vacancy:
            selected = 'I2' if qid.endswith(':importance') else keys[2]
            probabilities = {key: int(key == selected) for key in keys}
        elif unknown:
            selected = 'insufficient_data'
            probabilities = {key: int(key == selected) for key in keys}
        else:
            # Numeric mass .8 is conditionally normalized only in derived q.
            probabilities = dict(zip(keys, [0, .16, .4, .24, 0, .2]))
            selected = keys[2]
        answers[qid] = {'type': 'choice', 'choice': selected, 'confidence': .4,
                        'probabilities': probabilities}
    return {'model': plan['request']['model'], 'answers': answers,
            'usage': {'input_tokens': 123, 'output_tokens': 16}}


def seed_saved(store, plan, response):
    store.submit(plan)
    with store.db() as db:
        db.execute("UPDATE runs SET status='response_saved',response=?,response_sha256=? WHERE id=?",
                   (candidates.dump(response), candidates.digest(response), plan['run_id']))


class SourceCollector:
    """Synthetic full CV + three HR interview parts; mutable external revision."""
    def __init__(self, authorize):
        self.authorize, self.revisions = authorize, {}

    def collect(self, cid):
        self.authorize(cid)
        revision = self.revisions.get(cid, 'synthetic-source-v1')
        sources = [{'source_id': cid + ':' + name, 'version': revision, 'kind': kind,
                    'units': [{'unit_id': 'u1', 'text': 'PRIVATE SYNTHETIC ' + name + '  \n' + revision,
                               'role': 'candidate'}]}
                   for name, kind in [('cv', 'cv'), ('interview-part1', 'interview'),
                                      ('interview-part2', 'interview'), ('interview-part3', 'interview')]]
        recordings = [{'kind': 'video', 'selection': {'status': 'ready'}, 'transcript_id': 'part1'},
                      {'kind': 'audio', 'selection': {'status': 'ready'}, 'transcript_id': 'part2'},
                      {'kind': 'audio', 'selection': {'status': 'ready'}, 'transcript_id': 'part3'}]
        return {'candidate_id': cid, 'status': 'ready', 'revision': revision,
                'corpus': {'candidate_id': cid, 'sources': sources},
                'manifest': {'issues': [], 'source_count': 4, 'unit_count': 4,
                             'text_characters': sum(len(s['units'][0]['text']) for s in sources),
                             'conversations': [{'owner_kind': 'interview', 'lifecycle_status': 'completed',
                                 'status': 'ready', 'primary_status': 'ready', 'recordings': recordings}]}}

    def revalidate(self, snapshot):
        value = self.collect(snapshot['candidate_id'])
        if value['revision'] != snapshot['revision']:
            raise CollectionError('sources_changed_since_collection')
        return value


class HTTPFlowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = portal_fixture.SelectionTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.connect, self.lock, self.portal = self.fixture.connect, self.fixture.portal.lock, self.fixture.portal
        self.vid = self.fixture.vacancy['id']
        self.ids = {name: self.fixture.person(consent=False) for name in ('pass', 'unknown', 'fail', 'unscored', 'pending')}
        self.denied = set()
        self.temp = tempfile.TemporaryDirectory(prefix='assessment-flow-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.collector = SourceCollector(self.auth_candidate)
        self.cstore, self.vstore = CandidateStore(self.path / 'candidate.sqlite3'), VacancyStore(self.path / 'vacancy.sqlite3')
        self.plans, self.raw = {}, {}
        for name, cid in self.ids.items():
            if name == 'pending':
                continue
            plan = candidate_plan(self.collector.collect(cid)['corpus'], RUBRIC)
            self.plans[cid] = plan
            self.raw[cid] = typed_response(plan, unknown=name == 'unscored')
            seed_saved(self.cstore, plan, self.raw[cid])
        with self.connect() as db:
            data = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (self.vid,)).fetchone()[0])
            self.revision = selection.vacancy(db, self.vid)['contentHash']
            for name, cid in self.ids.items():
                current = facts.read(db, 'candidate', cid)
                facts.update(db, 'candidate', cid, {'revision': current['revision'], 'fields': {
                    **facts.CANDIDATE, 'work_locations': ['work-mode:remote'] if name in ('pass', 'unscored')
                    else ['ru:moscow'] if name == 'fail' else None}})
            current = facts.read(db, 'vacancy', self.vid)
            facts.update(db, 'vacancy', self.vid, {'revision': current['revision'], 'fields': {
                'location': {'required': True, 'allowed_ids': ['work-mode:remote']},
                'employment': {'required': False, 'allowed_ids': None},
                'industry_experience': {'required': False, 'industry_id': None, 'minimum_months': None}}})
        self.vplan = vacancy_plan({'vacancy_id': self.vid, 'revision': self.revision, 'data': data}, RUBRIC)
        seed_saved(self.vstore, self.vplan, typed_response(self.vplan, vacancy=True))
        self.logs, self.processes, self.ports = {}, {}, {}
        self.addCleanup(self.stop_all)
        for service in ('candidate', 'vacancy', 'recommendations'):
            self.launch(service)
        self.environment = patch.dict(os.environ, {'HR_ASSESSMENT_CORE_ENABLED': '1',
            'HR_ASSESSMENT_CORE_RUBRIC': str(RUBRIC_PATH), 'HR_ASSESSMENT_CORE_MODEL': 'jev-1.14.0'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.cb = candidates.Bridge(self.connect, self.lock, self.auth_candidate, self.collector,
            self.client('candidate'), RUBRIC, 'jev-1.14.0')
        self.vb = vacancies.Bridge(self.connect, self.lock, self.auth_vacancy, self.client('vacancy'), RUBRIC, 'jev-1.14.0')
        self.rb = recommendations.Bridge(self.connect, self.lock, self.auth_vacancy, self.auth_candidate,
                                         self.vb, self.cb, self.client('recommendations'))
        for cid in self.plans:
            self.assertEqual(self.cb.start(cid)['state'], 'ready')
        self.assertEqual(self.vb.start(self.vid, self.revision)['state'], 'ready')

    def auth_vacancy(self, vid):
        if vid in self.denied:
            raise Problem('denied', 403)
        with self.connect() as db:
            selection.vacancy(db, vid)

    def auth_candidate(self, cid):
        if cid in self.denied:
            raise Problem('denied', 403)
        with self.connect() as db:
            selection.person(db, cid)

    def client(self, service):
        return ServiceHTTP('http://127.0.0.1:' + str(self.ports[service]), TOKEN, timeout=3)

    def launch(self, service):
        prefix, module, relative = {'candidate': ('ASSESSMENT_CORE', 'assessment_core.api', 'services/assessment-core'),
            'vacancy': ('VACANCY_PROFILE', 'vacancy_profile.api', 'services/vacancy-profile'),
            'recommendations': ('RECOMMENDATIONS', 'recommendations', 'services/recommendations')}[service]
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.ports[service] = probe.getsockname()[1]
        log = open(self.path / (service + '.log'), 'ab')
        self.logs[service] = log
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(str(ROOT / x) for x in
                    (relative, 'packages/jev-client')),
               prefix + '_SERVICE_TOKEN': TOKEN, prefix + '_PORT': str(self.ports[service]),
               prefix + '_DB': str(self.path / (service + '.sqlite3')),
               prefix + '_JEV_API_KEY': 'synthetic-never-call-provider'}
        process = subprocess.Popen([sys.executable, '-m', module], cwd=ROOT, env=env, stdout=log, stderr=log)
        self.processes[service] = process
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail((self.path / (service + '.log')).read_text())
            try:
                if self.http(service, 'GET', '/readyz')[0] == 200:
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(.02)
        self.fail(service + ' did not become ready')

    def stop_all(self):
        for process in self.processes.values():
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            process.wait(timeout=5)
        for log in self.logs.values():
            log.close()

    def http(self, service, method, path, value=None, token=TOKEN):
        conn = http.client.HTTPConnection('127.0.0.1', self.ports[service], timeout=3)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        conn.request(method, path, body=candidates.dump(value).encode() if value is not None else None, headers=headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def start(self):
        return self.rb.start(self.vid, self.revision, list(self.ids.values()))

    def test_full_http_flow_frozen_rubric_formula_required_gates_pending_pool(self):
        self.assertEqual(len(RUBRIC['parameters']), 16)
        for path in ('services/assessment-core/rubrics/analytics-baseline.json',
                     'services/recommendations/rubrics/sales-v1.json'):
            self.assertEqual(candidates.digest(json.loads((ROOT / path).read_text())), candidates.digest(RUBRIC))
        value = self.start()
        self.assertEqual(value['candidateIds'], sorted(self.ids.values()))
        self.assertEqual(value['pendingCandidates'], [{'candidateId': self.ids['pending'], 'state': 'not_started'}])
        items = {x['candidate_id']: x for x in value['result']['items']}
        for name, eligibility, status in [('pass', 'pass', 'scored'), ('unknown', 'unknown', 'scored'),
                                          ('fail', 'fail', 'filtered'), ('unscored', 'pass', 'unscored')]:
            self.assertEqual((items[self.ids[name]]['eligibility'], items[self.ids[name]]['status']), (eligibility, status))
        item = items[self.ids['pass']]
        self.assertAlmostEqual(item['score'], .1 * math.tanh(1), places=12)
        for parameter in item['parameters']:
            self.assertEqual([level['p_raw'] for level in parameter['levels']], [0, .16, .4, .24, 0])
            self.assertAlmostEqual(sum(level['q'] for level in parameter['levels']), 1)
        self.assertIsNone(items[self.ids['unscored']]['score'])
        self.assertIsNone(items[self.ids['unscored']]['rank'])
        self.assertEqual(items[self.ids['unscored']]['coverage']['included_weight'], 0)
        self.assertIsNone(items[self.ids['fail']]['rank'])
        eligible = {x['candidateId']: x for x in value['invitationEligibility']}
        self.assertTrue(eligible[self.ids['pass']]['canInvite'])
        self.assertEqual(eligible[self.ids['unknown']]['reason'], 'unknown_requires_approval')
        self.assertFalse(eligible[self.ids['fail']]['canInvite'])
        with self.connect() as db:
            rows = {x['id']: x for x in selection.directory(db, self.vid)['candidates']}
            self.assertTrue(rows[self.ids['pass']]['canAdd'])
            self.assertFalse(rows[self.ids['fail']]['canAdd'])
            for table in ('cn_outbox', 'sl_core_results', 'cp_applications'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_same_full_corpus_and_vacancy_snapshot_are_preserved_and_reads_do_not_bill(self):
        value = self.start()
        for cid, plan in self.plans.items():
            saved = self.cstore.get(plan['run_id'])
            self.assertEqual(saved['response'], self.raw[cid])
            state = saved['plan']['request']['state']
            self.assertEqual(len(state['chunks']), 4)
            self.assertEqual([c['text'] for c in state['chunks']],
                             [s['units'][0]['text'] for s in self.collector.collect(cid)['corpus']['sources']])
            self.assertNotIn('vacancy', state)
            self.assertNotIn(self.vid, candidates.dump(saved['plan']))
            self.assertEqual(len(saved['plan']['request']['questions']), 16)
        saved = self.vstore.get(self.vplan['run_id'])
        self.assertEqual(saved['plan']['request']['state'], {'vacancy': self.vplan['vacancy']})
        self.assertEqual(len(saved['plan']['request']['questions']), 32)
        self.assertNotIn('candidate', candidates.dump(saved['plan']['request']['state']))
        before = [self.cstore.diagnostics(), self.vstore.diagnostics()]
        for _ in range(3):
            self.assertEqual(self.rb.read(self.vid)['result'], value['result'])
        self.assertEqual([self.cstore.diagnostics(), self.vstore.diagnostics()], before)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0], 0)
        for service in self.logs:
            self.logs[service].flush()
            output = (self.path / (service + '.log')).read_text()
            for secret in (TOKEN, 'PRIVATE SYNTHETIC', 'synthetic-never-call-provider'):
                self.assertNotIn(secret, output)

    def test_fact_edit_and_manual_requirement_override_stale_before_invite(self):
        value = self.start()
        current_profile = self.vb.read(self.vid)['profile']
        first = current_profile['parameters'][0]
        revised = self.vb.revise(self.vid, {'revision': self.revision, 'profileId': current_profile['profile_id'],
            'changes': [{'parameter_id': first['parameter_id'], 'required_level_id': first['levels'][3]['level_id'], 'importance': 3}]}, author='hr:independent-acceptance')
        self.assertNotEqual(revised['profile']['profile_id'], current_profile['profile_id'])
        self.assertEqual(revised['profile']['parameters'][0]['raw_answers'], first['raw_answers'])
        stale = self.rb.read(self.vid)
        self.assertEqual(stale['state'], 'stale')
        self.assertTrue(all(not item['canInvite'] for item in stale['invitationEligibility']))
        current = self.start()
        with self.connect() as db:
            f = facts.read(db, 'candidate', self.ids['pass'])
            facts.update(db, 'candidate', self.ids['pass'], {'revision': f['revision'],
                         'fields': {**f['fields'], 'work_locations': ['ru:moscow']}})
            body = {'vacancyId': self.vid, 'candidateIds': [self.ids['pass']], 'requestId': 'stale-command',
                    'recommendationId': current['recommendationId'], 'revision': current['revision']}
            with self.assertRaises(Problem) as caught:
                selection.nominate_recommendation(db, self.portal, body, current)
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0], 0)
        self.assertEqual(self.rb.read(self.vid)['state'], 'stale')

    def test_independent_process_auth_wrong_tenant_and_atomic_incompatible_profiles(self):
        for service in self.ports:
            self.assertEqual(self.http(service, 'GET', '/healthz', token=None)[0], 200)
            self.assertEqual(self.http(service, 'GET', '/readyz', token=None)[0], 401)
            self.assertEqual(self.http(service, 'GET', '/readyz', token='f2' * 32)[0], 401)
        self.denied.add(self.ids['pass'])
        with self.assertRaises(Problem) as caught:
            self.start()
        self.assertEqual(caught.exception.status, 403)
        self.denied.clear()
        request, _, _ = self.rb.gather(self.vid, list(self.ids.values()))
        request['candidate_profiles'][0]['rubric_sha256'] = 'f' * 64
        self.assertEqual(self.http('recommendations', 'POST', '/v1/recommendations', request)[0], 422)
        with closing(sqlite3.connect(self.path / 'recommendations.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recommendations').fetchone()[0], 0)

    def nominate_then_consent(self, cid, *, approve_unknown=False):
        value = self.start()
        body = {'vacancyId': self.vid, 'candidateIds': [cid], 'requestId': 'acceptance-nomination',
                'recommendationId': value['recommendationId'], 'revision': value['revision']}
        if approve_unknown:
            body['unknownApprovals'] = [cid]
        with self.connect() as db:
            receipt = selection.nominate_recommendation(db, self.portal, body, value)
            self.assertEqual(receipt['waitingProfile'], [cid])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 0)
            # Simulate saved candidate consent and use its actual reconciliation path.
            profile = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (cid,)).fetchone()[0])
            profile['consentReuse'] = True
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (candidates.dump(profile), cid))
            selection.reconcile_nominations(db, self.portal, cid)
            self.assertEqual(db.execute('SELECT state FROM sl_recommendation_nominations').fetchone()[0], 'pending_verification')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 0)
        return value

    def continuation(self, *, clock=time.time):
        # Real bridges and HTTP clients, only the configured materials-source seam
        # uses this suite's explicit fixture. No model/service/SQL mocks.
        def factory(connect, lock, authorize_vacancy, authorize_candidate):
            candidate_bridge = candidates.Bridge(connect, lock, authorize_candidate, self.collector,
                self.client('candidate'), RUBRIC, 'jev-1.14.0')
            vacancy_bridge = vacancies.Bridge(connect, lock, authorize_vacancy,
                self.client('vacancy'), RUBRIC, 'jev-1.14.0')
            return recommendations.Bridge(connect, lock, authorize_vacancy, authorize_candidate,
                vacancy_bridge, candidate_bridge, self.client('recommendations'))
        return recommendations.NominationWorker(self.connect, self.lock, self.portal, bridge_factory=factory, clock=clock)

    def test_delayed_consent_changed_external_sources_blocks_without_new_binding(self):
        cid = self.ids['pass']
        self.nominate_then_consent(cid)
        with self.connect() as db:
            before = recommendations.local_versions(db, self.vid, sorted(self.ids.values()))
        self.collector.revisions[cid] = 'synthetic-source-v2-changed-after-nomination'
        with self.connect() as db:
            # This was the hole: local-only fingerprints cannot see remote changes.
            self.assertEqual(recommendations.local_versions(db, self.vid, sorted(self.ids.values())), before)
        self.assertEqual(self.continuation().run_once(), 1)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT state,error FROM sl_recommendation_nominations').fetchone(),
                             ('blocked', 'recommendation_stale'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sl_core_results').fetchone()[0], 0)
        self.assertEqual(self.cstore.diagnostics()['counts'], {'completed': 4})

    def test_delayed_consent_current_sources_publish_once_with_exact_unknown_approval(self):
        cid = self.ids['unknown']
        value = self.start()
        body = {'vacancyId': self.vid, 'candidateIds': [cid], 'requestId': 'unknown-no-approval',
                'recommendationId': value['recommendationId'], 'revision': value['revision']}
        with self.connect() as db:
            with self.assertRaises(Problem) as caught:
                selection.nominate_recommendation(db, self.portal, body, value)
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0], 0)
        value = self.nominate_then_consent(cid, approve_unknown=True)
        # A newly constructed worker resumes the durable pending operation.
        worker = self.continuation()
        self.assertEqual(worker.run_once(), 1)
        self.assertEqual(self.continuation().run_once(), 0)
        with self.connect() as db:
            state, approval = db.execute('SELECT state,unknown_approval FROM sl_recommendation_nominations').fetchone()
            self.assertEqual(state, 'proposed')
            self.assertEqual(json.loads(approval)['recommendationId'], value['recommendationId'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sl_core_results').fetchone()[0], 0)
            self.assertIsNone(facts.read(db, 'candidate', cid)['fields']['work_locations'])
        self.assertFalse(next(i for i in self.rb.read(self.vid)['invitationEligibility'] if i['candidateId'] == cid)['canInvite'])

    def test_transient_service_outage_retries_durable_consent_without_duplicate_publish(self):
        cid = self.ids['pass']
        self.nominate_then_consent(cid)
        process = self.processes['candidate']
        process.send_signal(signal.SIGINT)
        process.wait(timeout=5)
        worker = self.continuation()
        self.assertEqual(worker.run_once(), 1)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT state,error FROM sl_recommendation_nominations').fetchone(),
                             ('pending_verification', 'verification_unavailable'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 0)
        self.logs['candidate'].close()
        self.launch('candidate')
        self.assertEqual(self.continuation().run_once(), 0)
        self.assertEqual(self.continuation(clock=lambda: time.time() + 10).run_once(), 1)
        self.assertEqual(self.continuation().run_once(), 0)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM sl_recommendation_nominations').fetchone()[0], 'proposed')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 1)

    def test_blocked_old_sources_need_new_recommendation_and_new_unknown_approval(self):
        cid = self.ids['unknown']
        old = self.nominate_then_consent(cid, approve_unknown=True)
        self.collector.revisions[cid] = 'synthetic-source-v2-new-interview'
        self.assertEqual(self.continuation().run_once(), 1)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM sl_recommendation_nominations').fetchone()[0], 'blocked')
        # Only seed while the worker is stopped: never expose a queued synthetic
        # job to a running real provider client.
        self.processes['candidate'].send_signal(signal.SIGINT)
        self.processes['candidate'].wait(timeout=5)
        plan = candidate_plan(self.collector.collect(cid)['corpus'], RUBRIC)
        seed_saved(self.cstore, plan, typed_response(plan))
        self.logs['candidate'].close()
        self.launch('candidate')
        self.cb.service = self.client('candidate')
        self.assertEqual(self.cb.start(cid)['state'], 'ready')
        current = self.start()
        self.assertNotEqual(current['recommendationId'], old['recommendationId'])
        eligible = next(i for i in current['invitationEligibility'] if i['candidateId'] == cid)
        self.assertTrue(eligible['canInvite'])
        body = {'vacancyId': self.vid, 'candidateIds': [cid], 'requestId': 'explicit-new-recommendation',
                'recommendationId': current['recommendationId'], 'revision': current['revision']}
        with self.connect() as db:
            with self.assertRaises(Problem) as caught:
                selection.nominate_recommendation(db, self.portal, body, current)
            self.assertEqual(caught.exception.status, 409)
            receipt = selection.nominate_recommendation(db, self.portal, {**body, 'unknownApprovals': [cid]}, current)
            self.assertIn(cid, receipt['added'])
            approval = json.loads(db.execute('SELECT unknown_approval FROM sl_recommendation_nominations').fetchone()[0])
            self.assertEqual(approval['recommendationId'], current['recommendationId'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sl_recommendation_nomination_history').fetchone()[0], 1)
        self.assertEqual(self.continuation().run_once(), 1)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM sl_recommendation_nominations').fetchone()[0], 'proposed')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 1)

    def test_fact_change_at_final_lock_acquisition_cannot_return_current_invitation(self):
        outer = self
        class ConcurrentWriterAtFinalLock:
            """Schedule a real SQLite writer just before final commit lock acquisition."""
            enters = 0
            def __enter__(self):
                self.enters += 1
                if self.enters == 2:
                    with outer.connect() as db:
                        cid = outer.ids['pass']
                        old = facts.read(db, 'candidate', cid)
                        facts.update(db, 'candidate', cid, {'revision': old['revision'],
                            'fields': {**old['fields'], 'work_locations': ['ru:moscow']}})
                return outer.lock.__enter__()
            def __exit__(self, *args):
                return outer.lock.__exit__(*args)
        bridge = recommendations.Bridge(self.connect, ConcurrentWriterAtFinalLock(), self.auth_vacancy,
            self.auth_candidate, self.vb, self.cb, self.client('recommendations'))
        try:
            value = bridge.start(self.vid, self.revision, list(self.ids.values()))
        except Problem as problem:
            self.assertEqual(problem.status, 409)
        else:
            self.assertEqual(value['state'], 'stale')
            self.assertTrue(all(not row['canInvite'] for row in value['invitationEligibility']))

    def test_crash_restart_reads_identical_result_and_no_new_provider_calls(self):
        value = self.start()
        for service in list(self.processes):
            process = self.processes[service]
            process.kill()
            process.wait(timeout=5)
            self.logs[service].close()
            self.launch(service)
        self.cb.service = self.client('candidate')
        self.vb.service = self.client('vacancy')
        self.rb.service = self.client('recommendations')
        self.assertEqual(self.rb.read(self.vid)['result'], value['result'])
        self.assertEqual(self.start()['recommendationId'], value['recommendationId'])
        self.assertEqual(self.cstore.diagnostics()['counts'], {'completed': 4})
        self.assertEqual(self.vstore.diagnostics()['jobs'], {'completed': 1})


if __name__ == '__main__':
    unittest.main()
