"""Existing imported cabinet cards: real SQLite and core contract, isolated providers."""
import copy
import hashlib
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import canonical_materials
import employer_intake as intake
import employer_intake_review as review
from domain import Problem
import test_employer_intake as fixture
from test_assessment_core_bridge import Service as CoreService, RUBRIC


def profile():
    text = 'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<v Рекрутер>Как действовали?</v>\n\n00:00:02.000 --> 00:00:08.000\n<v Кандидат>Уточнил задачу и согласовал следующий шаг.</v>\n'
    return {'source': {'kind': 'dsa-panel', 'candidateId': '123', 'example': True},
        'cvText': 'Полный текст резюме. Не терять конец.', 'transcriptText': text,
        'transcriptSource': {'candidateId': '123', 'sha256': hashlib.sha256(text.encode()).hexdigest()},
        'recordings': [{'source': 'dsa-panel', 'sourceCandidateId': '123',
            'publicUrl': 'https://kabinet.158-160-179-53.sslip.io/api/video/123', 'duration': 10, 'mime': 'video/mp4'}],
        'chapters': [{'title': 'Опыт', 'seconds': 0, 'endSeconds': 2},
                     {'title': 'Следующий шаг', 'seconds': 2, 'endSeconds': 10}]}


class DisplayService:
    def __init__(self):
        self.calls, self.status, self.body = [], 'queued', None

    def call(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path == '/v1/levels':
            self.body = body
        if path in ('/v1/levels', '/v1/presentations'):
            return {'assessment': {'revisionId': self.body['assessment']['run_id'], 'feedback': [],
                'criteria': [{'id': 'skill', 'level': None, 'evidence': []}],
                'provenance': {'vacancyProfileId': body['vacancyProfile']['profile_id']}}}
        if path.endswith('/retry'):
            self.status = 'queued'
        return {'preparationId': 'prep', 'status': self.status, 'candidateId': self.body['candidateId'],
            'coreRunId': self.body['assessment']['run_id'], 'sourceRevision': self.body['sourceRevision']}


class IntakeReviewTest(unittest.TestCase):
    setUp = fixture.EmployerIntakeTest.setUp
    tearDown = fixture.EmployerIntakeTest.tearDown
    connect = fixture.EmployerIntakeTest.connect
    body = staticmethod(fixture.EmployerIntakeTest.body)

    def candidate(self):
        with self.connect() as db:
            person = intake.add(db, 'a', self.va, self.body())['candidates'][0]
            person['preparedProfile'] = profile()
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(person), person['id']))
        return person['id']

    def configure(self):
        self.cid = self.candidate()
        self.core, self.service = CoreService(), DisplayService()
        portal = SimpleNamespace(connect=self.connect, lock=threading.RLock())
        self.bridge = review.Bridge(portal, core=self.core, service=self.service, rubric=RUBRIC)
        self.portal = portal
        empty = {'sourceCount': 0, 'recordings': [], 'transcripts': [], 'transcription': {'status': 'processing'}}
        for p in (patch.dict('os.environ', {'HR_CORE_CATALOG_ENABLED': '1',
                  'HR_MATERIALS_CORE_URL': 'http://127.0.0.1:1', 'HR_MATERIALS_CORE_KEY': 'test-key'}),
                  patch('canonical_materials.CoreMaterialsCatalog.legacy_materials', return_value=empty),
                  patch('vacancy_profile_bridge.configured', return_value=SimpleNamespace(
                      read=lambda vid: {'state': 'ready', 'profile': {'profile_id': 'profile-v1'}}))):
            p.start(); self.addCleanup(p.stop)
        return empty

    def tick(self, bridge=None):
        with self.connect() as db:
            db.execute('UPDATE er_intake_preparations SET next_check=0')
        return (bridge or self.bridge).tick()

    def test_empty_catalog_restores_verified_video_transcript_and_topic_boundaries(self):
        self.configure()
        with self.connect() as db:
            result = canonical_materials.read_intake_db(db, 'a', self.va, self.cid)
        self.assertEqual(result['transcription']['status'], 'ready')
        self.assertEqual(result['transcripts'][0]['recordingId'], result['recordings'][0]['id'])
        self.assertEqual([c['seconds'] for c in result['chapters']], [0, 2])
        self.assertEqual(result['materialOrigin'], 'verified-import')

    def test_current_pending_revoked_or_failed_source_cannot_be_replaced_by_import(self):
        empty = self.configure()
        for pending in ({**empty, 'sourceCount': 1}, {**empty, 'recordings': [{'id': 'new-source'}]}):
            with patch('canonical_materials.CoreMaterialsCatalog.legacy_materials', return_value=pending), self.connect() as db:
                self.assertEqual(canonical_materials.read_intake_db(db, 'a', self.va, self.cid), pending)
        for status in (410, 503):
            with patch('canonical_materials.CoreMaterialsCatalog.legacy_materials', side_effect=Problem('unavailable', status)), self.connect() as db:
                with self.assertRaises(Problem): canonical_materials.read_intake_db(db, 'a', self.va, self.cid)

    def test_invalid_transcript_and_wrong_workspace_fail_closed(self):
        self.configure()
        with self.connect() as db:
            with self.assertRaises(Problem): canonical_materials.read_intake_db(db, 'b', self.va, self.cid)
            body = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (self.cid,)).fetchone()[0])
            body['preparedProfile']['transcriptText'] += 'tampered'
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(body), self.cid))
        with self.connect() as db, self.assertRaises(Problem):
            canonical_materials.read_intake_db(db, 'a', self.va, self.cid)

    def test_full_corpus_same_assessment_in_list_and_orbit_restart_reuses_run(self):
        self.configure()
        with self.connect() as db:
            self.assertIsNone(review.projection(db, 'a', self.va, self.cid)['assessment'])
        self.assertEqual(self.core.calls, [])  # Opening a card starts no model.
        self.tick()
        corpus = self.core.calls[0][2]['corpus']
        self.assertEqual(corpus['candidate_id'], 'dsa-panel:123')
        self.assertEqual(corpus['sources'][0]['units'][0]['text'], profile()['cvText'])
        units = corpus['sources'][1]['units']
        self.assertEqual(len(units), 1)
        table = json.loads(units[0]['text'])
        self.assertEqual(table['columns'], ['start_ms', 'end_ms', 'speaker', 'text'])
        self.assertEqual(table['rows'], [[0, 2000, 'Рекрутер', 'Как действовали?'],
            [2000, 8000, 'Кандидат', 'Уточнил задачу и согласовал следующий шаг.']])
        with self.connect() as db:
            view = review.projection(db, 'a', self.va, self.cid)
            self.assertEqual(view['status'], 'pending')
            self.assertEqual(intake.list_candidates(db, 'a', self.va)['candidates'][0]['assessment'], view['assessment'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0], 0)
        restarted = review.Bridge(self.portal, core=self.core, service=self.service, rubric=RUBRIC)
        self.service.status = 'ready'; self.tick(restarted)
        with self.connect() as db:
            self.assertEqual(review.projection(db, 'a', self.va, self.cid)['status'], 'completed')
        self.tick(restarted)
        self.assertEqual(sum(method == 'POST' for method, _, _ in self.core.calls), 1)
        self.assertEqual(sum(path == '/v1/preparations' for _, path, _ in self.service.calls), 1)

    def test_failure_is_a_team_task_no_auto_retry_and_existing_levels_stay(self):
        self.configure(); self.tick()
        self.service.status = 'failed'; self.tick()
        calls = len(self.service.calls)
        self.tick(); self.tick()
        self.assertEqual(len(self.service.calls), calls)
        with self.connect() as db:
            self.assertEqual(review.tasks(db)[0]['error'], 'quote_preparation_failed')
            self.assertIsNotNone(review.projection(db, 'a', self.va, self.cid)['assessment'])
        self.bridge.retry(self.cid, 'human-retry')
        self.tick(); self.service.status = 'ready'; self.tick()
        with self.connect() as db:
            self.assertEqual(review.projection(db, 'a', self.va, self.cid)['status'], 'completed')

    def test_archived_card_is_not_discovered_or_exposed(self):
        self.configure()
        with self.connect() as db:
            db.execute("UPDATE em_intake_candidates SET body=json_set(body,'$.archived',json('true')) WHERE id=?", (self.cid,))
        self.assertFalse(self.tick()); self.assertEqual(self.core.calls, [])
        with self.connect() as db, self.assertRaises(Problem):
            review.projection(db, 'a', self.va, self.cid)

    def test_import_duration_uses_transcript_clock_precision_without_accepting_long_overshoot(self):
        self.configure()
        with self.connect() as db:
            body = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (self.cid,)).fetchone()[0])
            body['preparedProfile']['recordings'][0]['duration'] = 7.9997
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(body), self.cid))
        self.assertTrue(self.bridge.snapshot(self.cid)['chunks'])
        with self.connect() as db:
            body['preparedProfile']['recordings'][0]['duration'] = 7.9987
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(body), self.cid))
        with self.assertRaises(Problem): self.bridge.snapshot(self.cid)

    def test_changed_materials_during_preparation_cannot_publish_stale_result(self):
        self.configure(); self.tick(); self.service.status = 'ready'
        snap = self.bridge.snapshot(self.cid)
        with patch.object(self.bridge, 'snapshot', side_effect=[snap, {**snap, 'revision': 'changed'}]):
            self.tick()
        with self.connect() as db:
            self.assertEqual(review.tasks(db)[0]['error'], 'intake_materials_changed')
            self.assertEqual(review.projection(db, 'a', self.va, self.cid)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
