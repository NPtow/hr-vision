"""Consent/publication integration on a real isolated HR database."""
import json
import unittest
from unittest.mock import patch

import employer_review as review
import employer_store
import employer_projection
import assessment_core_bridge
import selection
import test_transcript_bundle
from types import SimpleNamespace
from domain import Problem
import test_employer_store as fixture


class Service:
    def __init__(self):
        self.status = 'queued'
        self.calls = []
        self.run_id = 'run'

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if path == '/v1/preparations':
            self.run_id = body['assessment']['run_id']
        if path == '/v1/presentations':
            return {'preparationId': 'prep', 'assessment': {'revisionId': self.run_id, 'criteria': [], 'feedback': [],
                    'provenance': {'vacancyProfileId': body['vacancyProfile']['profile_id']}}}
        return {'preparationId': 'prep', 'coreRunId': self.run_id, 'candidateId': self.cid,
                'sourceRevision': 'transcript-v1', 'status': self.status}


class EmployerReviewTests(unittest.TestCase):
    setUp = fixture.EmployerStoreTest.setUp
    tearDown = fixture.EmployerStoreTest.tearDown
    connect = fixture.EmployerStoreTest.connect
    person = fixture.EmployerStoreTest.person
    batch = staticmethod(fixture.EmployerStoreTest.batch)
    import_people = fixture.EmployerStoreTest.import_people

    def prepare_fixture(self, *, accepted=True):
        self.flag = patch.dict('os.environ', {'HR_EMPLOYER_REVIEW_ENABLED': '1'})
        self.flag.start(); self.addCleanup(self.flag.stop)
        cid = self.person()
        aid = self.import_people([cid])['applicationIds'][0]
        with self.connect() as db:
            if accepted:
                self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
            assessment_core_bridge.init(db)
            selection.init(db)
            rev = selection.person(db, cid)['sourceRevision']
            db.execute('''INSERT INTO ac_bindings
              (id,candidate_id,source_revision,rubric_hash,model,manifest,run_id,status,created,updated,request_format)
              VALUES ('binding',?,?, 'rubric','model','{}','run','completed',1,1,?)''',
                       (cid, rev, assessment_core_bridge.REQUEST_FORMAT))
        service = Service(); service.cid = cid
        bridge = review.Bridge(self.portal, service)
        body = {'candidateId': cid, 'assessment': {'run_id': 'run', 'parameters': []},
                'sourceRevision': 'transcript-v1', 'chunks': []}
        snap = patch.object(bridge, 'snapshot', return_value=(body, rev)); snap.start(); self.addCleanup(snap.stop)
        self.body = body
        profile = patch.object(bridge, 'vacancy_profile', return_value={'profile_id': 'vacancy-profile'})
        profile.start(); self.addCleanup(profile.stop)
        return cid, aid, bridge, service

    def next_tick(self, bridge):
        with self.connect() as db:
            db.execute('UPDATE er_preparations SET next_check=0')
        return bridge.tick()

    def test_no_call_before_consent_hidden_until_prepared_then_one_projection(self):
        cid, aid, bridge, service = self.prepare_fixture(accepted=False)
        self.assertFalse(bridge.tick()); self.assertEqual(service.calls, [])
        with self.connect() as db:
            self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
            self.assertEqual(employer_store.applications(db, 'a'), [])
            with self.assertRaises(Problem): employer_store.application(db, 'a', aid)
        self.assertTrue(bridge.tick())
        with self.connect() as db:
            self.assertEqual(employer_store.applications(db, 'a'), [])
        service.status = 'ready'; self.next_tick(bridge)
        with self.connect() as db:
            records = employer_store.applications(db, 'a')
            self.assertEqual(len(records), 1)
            person = employer_projection.person(db, {'workspaceId': 'a'}, self.portal, records[0], self.vacancy)
            self.assertEqual(person['assessment'], review.projection(db, aid)['assessment'])
            self.assertEqual(person['assessment']['revisionId'], 'run')
        self.assertFalse(self.next_tick(bridge))
        self.assertEqual(service.calls.count(('POST', '/v1/preparations')), 1)

    def test_failure_persists_team_task_and_never_auto_retries(self):
        _, aid, bridge, service = self.prepare_fixture()
        with patch.object(service, 'call', side_effect=OSError('unavailable')):
            bridge.tick()
        for _ in range(3): self.assertFalse(self.next_tick(bridge))
        with self.connect() as db:
            self.assertEqual(review.tasks(db)['tasks'][0]['applicationId'], aid)
            self.assertEqual(employer_store.applications(db, 'a'), [])
        bridge.retry(aid, 'human-retry'); self.next_tick(bridge)
        self.assertEqual(service.calls.count(('POST', '/v1/preparations')), 1)

    def test_new_core_version_updates_both_views_without_removing_candidate(self):
        _, aid, bridge, service = self.prepare_fixture()
        bridge.tick(); service.status = 'ready'; self.next_tick(bridge)
        with self.connect() as db:
            db.execute("UPDATE ac_bindings SET run_id='new-run'")
            self.assertEqual(review.projection(db, aid)['status'], 'pending')
            self.assertEqual(review.projection(db, aid)['assessment']['revisionId'], 'run')
            self.assertEqual(len(employer_store.applications(db, 'a')), 1)
        self.body['assessment']['run_id'] = 'new-run'
        self.next_tick(bridge); self.next_tick(bridge)
        with self.connect() as db:
            record = employer_store.applications(db, 'a')[0]
            person = employer_projection.person(db, {'workspaceId': 'a'}, self.portal, record, self.vacancy)
            self.assertEqual(person['assessment'], review.projection(db, aid)['assessment'])
            self.assertEqual(person['assessment']['revisionId'], 'new-run')

    def test_upstream_wait_is_not_a_failed_job_or_a_model_retry(self):
        _, aid, bridge, service = self.prepare_fixture()
        with patch.object(bridge, 'snapshot', side_effect=review.Waiting):
            for _ in range(3): self.next_tick(bridge)
        self.assertEqual(service.calls, [])
        with self.connect() as db:
            self.assertEqual(review.tasks(db)['tasks'], [])
        self.next_tick(bridge); service.status = 'ready'; self.next_tick(bridge)
        with self.connect() as db:
            self.assertEqual(review.projection(db, aid)['status'], 'completed')

    def test_existing_approvals_are_discovered_for_all_candidates_without_hiding_them(self):
        _, aid, bridge, _ = self.prepare_fixture()
        cid2 = self.person(); aid2 = self.import_people([cid2], external='batch-two')['applicationIds'][0]
        cid3 = self.person(); self.import_people([cid3], external='batch-three')
        with self.connect() as db:
            self.portal.decide_application(db, cid2, {'applicationId': aid2, 'decision': 'accepted'})
            db.execute('DELETE FROM er_preparations')
        bridge.discover()
        with self.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT application_id FROM er_preparations')}, {aid, aid2})
            self.assertEqual({r['applicationId'] for r in employer_store.applications(db, 'a')}, {aid, aid2})
        with patch.object(bridge, 'snapshot', side_effect=OSError('unavailable')):
            self.next_tick(bridge)
        with self.connect() as db:
            self.assertEqual(len(employer_store.applications(db, 'a')), 2)

    def test_consent_withdrawal_blocks_completion_and_team_can_see_failure(self):
        cid, aid, bridge, service = self.prepare_fixture()
        bridge.tick(); service.status = 'ready'
        with self.connect() as db:
            p = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (cid,)).fetchone()[0])
            p['consentReuse'] = False
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (json.dumps(p), cid))
        self.next_tick(bridge)
        with self.connect() as db:
            self.assertEqual(review.tasks(db)['tasks'][0]['error'], 'review_consent_or_access_changed')
            self.assertEqual(employer_store.applications(db, 'a'), [])

    def test_snapshot_uses_actual_bundle_contract_and_only_reads_core(self):
        cid, aid, bridge, _ = self.prepare_fixture()
        core = SimpleNamespace(read=lambda c: {'state': 'ready', 'coreRun': {
            'status': 'completed', 'assessment': {'candidate_id': c, 'run_id': 'run'}}})
        with patch.object(assessment_core_bridge, 'configured', return_value=core), \
             patch.object(self.portal, 'materials', return_value=test_transcript_bundle.materials()):
            body, _ = review.Bridge.snapshot(bridge, aid)
        self.assertEqual(body['assessment']['candidate_id'], cid)
        self.assertEqual(set(body['chunks'][0]), {'id', 'text', 'speaker', 'recordingId', 'transcriptId', 'startMs', 'endMs'})
        self.assertEqual([c['recordingId'] for c in body['chunks']], ['part-a', 'part-b'])

    def test_direct_candidate_approval_waits_for_employer_assignment(self):
        with patch.dict('os.environ', {'HR_EMPLOYER_REVIEW_ENABLED': '1'}):
            cid = self.person()
            with self.connect() as db:
                self.portal.propose_application(db, {'candidateId': cid, 'vacancyId': self.vacancy['id']})
                aid = db.execute('SELECT id FROM cp_applications WHERE candidate_id=?', (cid,)).fetchone()[0]
                self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
                self.assertTrue(review.tracked(db, aid))
            service = Service()
            bridge = review.Bridge(self.portal, service)
            self.assertFalse(bridge.tick())
            self.assertEqual(service.calls, [])
            self.import_people([cid])
            with self.connect() as db:
                self.assertEqual(employer_store.applications(db, 'a'), [])


if __name__ == '__main__':
    unittest.main()
