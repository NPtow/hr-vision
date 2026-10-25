"""Real HR schema + fake private transport; no model calls or external writes."""
import json
import unittest
from unittest.mock import patch

import assessment_jobs as jobs
import selection
from public_profiles import PublicProfiles
from domain import Problem
import test_candidate_portal as fixture


class AssessmentJobsTest(unittest.TestCase):
    def setUp(self):
        self.flow = fixture.CandidatePortalTest()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.addCleanup(self.flow.tearDown)
        self.person, _ = self.flow.completed_person(name='Synthetic fixture')
        self.shares = PublicProfiles(self.flow.portal)
        with self.flow.connect() as db:
            selection.init(db)
            self.shares.issue(db, self.person['id'], issuer='test', origin='https://portal.example')
        self.bridge = jobs.Bridge(self.flow.portal, request=self.request)
        self.requests = []

    def request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        return {'id': 'a' * 64, 'status': 'queued', 'assessment': None, 'error': None}

    def materials(self, cid, *, access_check):
        with self.flow.connect() as db:
            access_check(db)
        return {'recordings': [{'id': 'bound-recording'}], 'transcripts': [{'text':
            '[00:01-00:05] Р: Что вы сделали?\n[00:05-00:12] К: Уточнила вопрос клиента и предложила решение.'}]}

    def test_public_issue_persists_intent_in_transaction(self):
        with self.flow.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM as_bindings').fetchone()[0], 1)
            self.shares.issue(db, self.person['id'], issuer='test', origin='https://portal.example')
            self.assertEqual(db.execute('SELECT count(*) FROM as_bindings').fetchone()[0], 1)
        self.assertEqual(self.requests, [])

    def test_queue_materials_are_server_bound_and_poll_reuses_job(self):
        with patch.object(self.flow.portal, 'materials', side_effect=self.materials):
            self.assertTrue(self.bridge.tick())
        method, path, payload = self.requests[0]
        self.assertEqual(path, '/v1/jobs')
        self.assertEqual(payload['subject'], 'candidate:' + self.person['id'])
        self.assertEqual(set(payload), {'subject', 'sourceHash', 'chunks'})
        self.assertEqual(payload['chunks'][1]['startMs'], 5000)
        self.assertNotIn('email', json.dumps(payload))
        with self.flow.connect() as db:
            db.execute('UPDATE as_bindings SET next_check=0')
        with patch.object(self.flow.portal, 'materials', side_effect=AssertionError('must poll existing job')):
            self.bridge.tick()
        self.assertEqual(self.requests[1][:2], ('GET', '/v1/jobs/' + 'a' * 64))

    def test_revocation_during_dispatch_does_not_publish_result(self):
        def revoke(method, path, payload=None):
            with self.flow.connect() as db:
                self.shares.revoke(db, self.person['id'])
            return self.request(method, path, payload)
        self.bridge.request = revoke
        with patch.object(self.flow.portal, 'materials', side_effect=self.materials):
            self.bridge.tick()
        with self.flow.connect() as db:
            self.assertIsNone(db.execute('SELECT job_id FROM as_bindings').fetchone()[0])

    def test_projection_rejects_stale_source_and_wrong_recording(self):
        with self.flow.connect() as db:
            revision = jobs.candidate_revision(db, self.person['id'])
            db.execute("UPDATE as_bindings SET status='completed',source_revision=?,recording_id='recording',assessment=?",
                       (revision, json.dumps({'revisionId': 'test', 'criteria': []})))
            self.assertIsNotNone(jobs.read_candidate(db, self.person['id'], 'recording')['assessment'])
            self.assertIsNone(jobs.read_candidate(db, self.person['id'], 'wrong')['assessment'])
            db.execute("UPDATE cp_resumes SET uploaded_at='changed'")
            self.assertIsNotNone(jobs.read_candidate(db, self.person['id'])['assessment'])
            db.execute("UPDATE cp_interviews SET completed_at='changed'")
            self.assertIsNone(jobs.read_candidate(db, self.person['id'])['assessment'])

    def test_vacancy_intent_uses_existing_base_and_distinct_match(self):
        with self.flow.connect() as db:
            jobs.enqueue(db, 'candidate', self.person['id'], 'vacancy-one')
            jobs.enqueue(db, 'candidate', self.person['id'], 'vacancy-two')
            jobs.enqueue(db, 'candidate', self.person['id'], 'vacancy-one')
            self.assertEqual(db.execute('SELECT count(*) FROM as_bindings').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM as_matches').fetchone()[0], 2)

    def test_invalid_transcript_is_terminal_instead_of_infinite_loading(self):
        with patch.object(self.bridge, 'snapshot', side_effect=Problem('transcript_format_unsupported', 422)):
            self.bridge.tick()
        with self.flow.connect() as db:
            result = jobs.read_candidate(db, self.person['id'], 'recording')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error'], 'transcript_format_unsupported')
        self.assertEqual(self.requests, [])

    def test_provider_inline_speaker_is_preserved_and_uniquely_bound(self):
        transcript = 'WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nNikita: Что вы сделали?\n\n00:00:05.000 --> 00:00:12.000\nElena: Я уточнила задачу.\n'
        chunks = jobs.assessment_chunks(transcript, 'Иванова Елена')
        self.assertEqual([r['speakerName'] for r in chunks], ['Nikita', 'Кандидат'])
        self.assertEqual(chunks[1]['text'], 'Elena: Я уточнила задачу.')
        self.assertEqual(chunks[1]['startMs'], 5000)
        unrelated = jobs.assessment_chunks(transcript, 'Анна Петрова')
        self.assertEqual(unrelated[1]['speakerName'], 'Elena')
        ambiguous = transcript + '\n00:00:12.000 --> 00:00:15.000\nЕлена: Другая участница.\n'
        self.assertNotIn('Кандидат', [r['speakerName'] for r in jobs.assessment_chunks(ambiguous, 'Иванова Елена')])


if __name__ == '__main__':
    unittest.main()
