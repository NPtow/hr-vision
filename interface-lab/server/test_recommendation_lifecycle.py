"""Private synthetic HR + real bridges; no provider or delivery operations."""
from copy import deepcopy
import json
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import recommendation_lifecycle as automatic
import recommendations_bridge as matching
import selection
import selection_facts as facts
import selection_status
import test_recommendations_bridge as fixture
from domain import Problem


class RecommendationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.IntegrationTest(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.connect, self.lock, self.portal = self.f.connect, self.f.lock, self.f.portal
        self.vid, self.cid, self.clock = self.f.vid, self.f.cid, [time.time()]
        self.posts, self.reads, self.accepted_profiles, self.results = [], [], {}, {}
        self.ready = {self.cid}
        self.profile_lose_ack = self.recommendation_lose_ack = self.unavailable = self.review = False
        self.importance = None
        self.f.vp.service = self.f.rb.service = SimpleNamespace(call=self.http)
        self.f.rb.candidate = SimpleNamespace(read=self.candidate_read)
        self.lifecycle = self.make_lifecycle()

    def make_lifecycle(self, **kwargs):
        return automatic.Lifecycle(self.connect, self.lock, self.portal, bridge_factory=self.factory,
                                   clock=lambda: self.clock[0], **kwargs)

    def factory(self, connect, lock, auth_v, auth_c):
        self.f.rb.authorize_vacancy, self.f.rb.authorize_candidate = auth_v, auth_c
        self.f.vp.authorize = auth_v
        return self.f.rb

    def candidate_read(self, cid):
        self.assertFalse(self.lock._is_owned(), 'candidate HTTP held portal lock')
        return {'state': 'ready', 'coreRun': {'assessment': {'candidate_id': cid, 'run_id': selection.digest(cid)}}} if cid in self.ready else {'state': 'queued'}

    def http(self, method, path, body=None):
        self.assertFalse(self.lock._is_owned(), 'service HTTP held portal lock')
        self.reads.append((method, path))
        if self.unavailable: raise Problem('PRIVATE unavailable text', 503)
        if method == 'POST': self.posts.append((path, deepcopy(body)))
        if path.startswith('/v1/vacancy-profiles'):
            if method == 'POST' and path == '/v1/vacancy-profiles':
                identity = selection.digest(body)
                if identity not in self.accepted_profiles:
                    value = self.f.call(method, path, body)
                    if self.review:
                        self.f.profile['parameters'][0].update(status='needs_review', importance=self.importance, required_level_id=None)
                        value['profile'] = deepcopy(self.f.profile)
                    self.accepted_profiles[identity] = value
                if self.profile_lose_ack:
                    self.profile_lose_ack = False
                    raise Problem('lost accepted profile ACK', 503)
                return deepcopy(self.accepted_profiles[identity])
            return self.f.call(method, path, body)
        if method == 'POST':
            vp, identity = body['vacancy_profile'], selection.digest(body)
            self.results[identity] = {'contract_version': 'recommendations-v1', 'recommendation_id': identity,
                'vacancy_id': self.vid, 'vacancy_profile_id': vp['profile_id'], 'rubric_id': vp['rubric_id'],
                'rubric_version': vp['rubric_version'], 'rubric_sha256': vp['rubric_sha256'], 'policy': deepcopy(matching.POLICY),
                'items': [{'candidate_id': p['candidate_id'], 'eligibility': 'unknown', 'status': 'scored',
                    'rank': i+1, 'score': .2, 'score_points': 60, 'required_checks': [], 'parameters': [], 'coverage': {}}
                    for i, p in enumerate(body['candidate_profiles'])]}
            if self.recommendation_lose_ack:
                self.recommendation_lose_ack = False
                raise Problem('lost accepted comparison ACK', 503)
            return deepcopy(self.results[identity])
        return deepcopy(self.results[path.rsplit('/', 1)[1]])

    def sweep(self):
        for _ in range(3): self.lifecycle.tick()

    def status(self):
        with self.connect() as db: return automatic.projection(db, self.vid)

    def test_all_ready_pool_automatic_and_unchanged_reused_without_side_effects(self):
        self.sweep(); self.assertEqual(self.status()['state'], 'ready')
        self.assertEqual((len(self.accepted_profiles), len(self.results)), (1, 1))
        calls = len(self.reads); self.clock[0] += 60; self.sweep()
        self.assertEqual(len(self.reads), calls)
        with self.connect() as db:
            for table in ('cn_outbox', 'cp_applications', 'sl_recommendation_nominations'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)

    def test_new_ready_and_removed_material_rebuild_complete_current_pool(self):
        self.sweep(); self.ready.add(self.f.pending)
        with self.connect() as db: self.f.evidence(db, self.f.pending)
        self.sweep(); self.assertEqual(self.status()['state'], 'ready')
        with self.connect() as db:
            self.assertEqual(json.loads(matching.latest(db, self.vid)['candidate_ids']), sorted(self.ready))
            selection_status.invalidate_evidence(db, self.f.pending)
        self.sweep(); self.assertEqual(self.status()['state'], 'ready')
        with self.connect() as db:
            self.assertEqual(json.loads(matching.latest(db, self.vid)['candidate_ids']), [self.cid])
        self.assertEqual(len(self.accepted_profiles), 1)

    def test_profile_lost_ack_persisted_backoff_restart_reuses_one_provider_run(self):
        self.profile_lose_ack = True; self.sweep()
        self.assertEqual(self.status()['state'], 'backoff'); self.assertEqual(len(self.accepted_profiles), 1)
        due, calls = self.status()['retryAt'], len(self.reads)
        self.lifecycle = self.make_lifecycle(); self.sweep(); self.assertEqual(len(self.reads), calls)
        self.clock[0] = due; self.sweep(); self.assertEqual(self.status()['state'], 'ready')
        self.assertEqual(len(self.accepted_profiles), 1)
        self.assertEqual(len([p for p, body in self.posts if p == '/v1/vacancy-profiles']), 2)

    def test_comparison_lost_ack_persisted_backoff_restart_reuses_one_result(self):
        self.recommendation_lose_ack = True; self.sweep()
        self.assertEqual(self.status()['state'], 'backoff'); self.assertEqual(len(self.results), 1)
        due, calls = self.status()['retryAt'], len(self.reads)
        self.lifecycle = self.make_lifecycle(); self.sweep(); self.assertEqual(len(self.reads), calls)
        self.clock[0] = due; self.sweep(); self.assertEqual(self.status()['state'], 'ready')
        self.assertEqual((len(self.results), len(self.accepted_profiles)), (1, 1))

    def test_post_bridge_same_pool_fact_race_cannot_stamp_new_versions_ready(self):
        original, changed = self.lifecycle._save_current, [False]
        def race(vid, pool, state, *args, **kwargs):
            if state == 'ready' and not changed[0]:
                changed[0] = True
                with self.connect() as db:
                    current = facts.read(db, 'candidate', self.cid)
                    facts.update(db, 'candidate', self.cid, {'revision': current['revision'],
                        'fields': {**current['fields'], 'work_locations': ['work-mode:remote']}})
            return original(vid, pool, state, *args, **kwargs)
        with patch.object(self.lifecycle, '_save_current', side_effect=race): self.lifecycle.tick()
        self.assertEqual(self.status()['state'], 'input_changed')
        self.sweep(); self.assertEqual(self.status()['state'], 'ready'); self.assertEqual(len(self.results), 2)

    def test_unknown_importance_blocks_without_busy_loop_unknown_target_is_allowed(self):
        self.review = True; self.sweep(); self.assertEqual(self.status()['state'], 'needs_review')
        calls = len(self.reads); self.clock[0] += 300; self.sweep(); self.assertEqual(len(self.reads), calls)
        self.assertEqual(len(self.results), 0)
        self.f.profile['profile_id'] = 'e'*64; self.f.profile['parameters'][0]['importance'] = 2
        with self.connect() as db:
            db.execute('UPDATE vp_bindings SET profile_id=?,profile=? WHERE vacancy_id=?',
                       ('e'*64, json.dumps(self.f.profile), self.vid))
        self.sweep(); self.assertEqual(self.status()['state'], 'ready'); self.assertEqual(len(self.results), 1)

    def test_1001_full_pool_explicit_refusal_never_truncates_or_calls_http(self):
        ids = ['candidate-%04d'%i for i in range(1001)]
        self.lifecycle = self.make_lifecycle(candidate_ids=lambda *args: ids); self.sweep()
        self.assertEqual(self.status()['state'], 'pool_too_large')
        self.assertEqual(self.status()['errorCode'], 'candidate_pool_limit_exceeded'); self.assertEqual(self.reads, [])
        with self.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT versions FROM rc_auto_vacancies').fetchone()[0])['pool'], ids)

    def test_outage_consecutive_backoff_persists_and_recovers_without_raw_messages(self):
        self.unavailable = True; self.sweep(); self.assertEqual(self.status()['state'], 'backoff')
        with self.connect() as db: first = db.execute('SELECT error_count,next_check FROM rc_auto_vacancies').fetchone()
        calls = len(self.reads); self.lifecycle = self.make_lifecycle(); self.sweep(); self.assertEqual(len(self.reads), calls)
        self.clock[0] = first[1]; self.sweep()
        with self.connect() as db: second = db.execute('SELECT error_count,next_check FROM rc_auto_vacancies').fetchone()
        self.assertEqual(second[0], 2); self.assertEqual(second[1]-self.clock[0], 10)
        self.assertNotIn('PRIVATE', json.dumps(self.status()))
        self.unavailable = False; self.clock[0] = second[1]; self.sweep(); self.assertEqual(self.status()['state'], 'ready')

    def test_closed_vacancy_becomes_inactive_before_http(self):
        with self.connect() as db:
            body = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (self.vid,)).fetchone()[0])
            body['status'] = 'closed'; db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps(body), self.vid))
        self.sweep(); self.assertEqual(self.status()['state'], 'inactive'); self.assertEqual(self.reads, [])

    def test_runtime_one_file_owner_shutdown_release_and_disable(self):
        fake = SimpleNamespace(interval=30, tick=lambda event: {'scanned': 0}, status=lambda: {'enabled': True})
        with patch.dict(os.environ, {'HR_RECOMMENDATIONS_AUTO_ENABLED':'1'}, clear=True), patch.object(automatic, 'Lifecycle', return_value=fake):
            runtime = automatic.start_background(self.connect, self.lock, self.portal)
            try:
                with self.assertRaises(Problem): automatic.start_background(self.connect, self.lock, self.portal)
                self.assertTrue(runtime.status()['running'])
            finally: self.assertTrue(runtime.stop())
            self.assertTrue(automatic.start_background(self.connect, self.lock, self.portal).stop())
        with patch.dict(os.environ, {'HR_RECOMMENDATIONS_AUTO_ENABLED':'0'}, clear=True):
            self.assertIsNone(automatic.start_background(self.connect, self.lock, self.portal))
        self.assertEqual(self.reads, [])

    def test_fair_bounded_vacancy_cursor_survives_restart(self):
        with self.connect() as db:
            others = [selection.employers.create_vacancy(db, 'a', {'title': 'Other '+str(i),
                'company': 'Synthetic company', 'city': 'Москва', 'remote': False,
                'description': 'Synthetic duties'})['id'] for i in range(2)]
        processed = []
        def factory(*args):
            def start(vid, revision, ids):
                processed.append(vid)
                return {'state': 'ready', 'candidateIds': ids, 'pendingCandidates': []}
            return SimpleNamespace(vacancy=SimpleNamespace(read=lambda vid: {'state': 'ready',
                'revision': vid, 'profile': {'profile_id': vid}}), start=start,
                read=lambda vid: {'state': 'not_started'})
        def lifecycle():
            return automatic.Lifecycle(self.connect, self.lock, self.portal, bridge_factory=factory,
                versions=lambda db, vid, ids: {'vacancy': vid, 'profile': vid, 'pool': ids},
                batch_size=1, clock=lambda: self.clock[0])
        self.lifecycle = lifecycle()
        self.assertEqual(self.lifecycle.scan(), 1)
        cursor = self.lifecycle.status()['cursor']
        self.lifecycle = lifecycle()
        self.assertEqual(self.lifecycle.status()['cursor'], cursor)
        for _ in range(6):
            self.assertLessEqual(self.lifecycle.scan(), 1)
        self.assertEqual(processed, sorted([self.vid, *others]))


if __name__ == '__main__': unittest.main()
