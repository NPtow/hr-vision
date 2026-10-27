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

    def test_invalid_active_bundle_before_snapshot_is_terminal_without_unbound_revision(self):
        with patch.object(self.bridge, 'active', side_effect=Problem('transcript_format_unsupported', 422)):
            self.assertTrue(self.bridge.tick())
        self.assertEqual(self.requests, [])
        with self.flow.connect() as db:
            row = db.execute('SELECT status,error FROM as_bindings').fetchone()
            self.assertEqual(tuple(row), ('failed', 'transcript_format_unsupported'))

    def test_invalid_transcript_is_terminal_instead_of_infinite_loading(self):
        with patch.object(self.bridge, 'snapshot', side_effect=Problem('transcript_format_unsupported', 422)):
            self.bridge.tick()
        with self.flow.connect() as db:
            result = jobs.read_candidate(db, self.person['id'], 'recording')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error'], 'transcript_format_unsupported')
        self.assertEqual(self.requests, [])

    def test_primary_change_invalidates_existing_assessment_without_interview_edit(self):
        env={'HR_CORE_CATALOG_ENABLED':'1','HR_MATERIALS_CORE_URL':'http://127.0.0.1:8093','HR_MATERIALS_CORE_KEY':'test-key'}
        ready={'status':'ready','selections':[{'recording_id':'recording','generation':1,
            'revision':1,'request_id':'one','transcript_id':'transcript-one','status':'ready'}]}
        with patch.dict('os.environ',env), patch('core_source_client.CoreClient.materials', return_value={'assets': []}), patch('core_source_client.CoreClient.primary_transcripts',return_value=ready) as primary:
            with self.flow.connect() as db:
                revision=jobs.candidate_revision(db,self.person['id'])
                db.execute("UPDATE as_bindings SET status='completed',source_revision=?,assessment=?",
                           (revision,json.dumps({'revisionId':'saved','criteria':[]})))
                self.assertIsNotNone(jobs.read_candidate(db,self.person['id'])['assessment'])
                primary.return_value={'status':'processing','selections':[{'recording_id':'recording',
                    'generation':1,'revision':2,'request_id':'two','transcript_id':None,'status':'pending'}]}
                self.assertEqual(jobs.read_candidate(db,self.person['id']),{'assessment':None,'status':'pending'})
                primary.return_value={**ready,'status':'processing','selections':[],
                    'transcript_selection_required_recording_ids':['recording']}
                self.assertIsNone(jobs.read_candidate(db,self.person['id'])['assessment'])

    def test_snapshot_records_exact_transcript_and_persists_poll_provenance(self):
        def selected(cid,*,access_check):
            data=self.materials(cid,access_check=access_check)
            data['transcripts'][0].update(id='transcript-exact',origin='asr',model='model-v1',selectionRevision=2)
            return data
        with patch.object(self.flow.portal,'materials',side_effect=selected):self.bridge.tick()
        with self.flow.connect() as db:
            source=json.loads(db.execute('SELECT source_material FROM as_bindings').fetchone()[0])
        self.assertEqual(source['transcriptId'],'transcript-exact')
        self.assertEqual(source['selectionRevision'],2)
        self.assertEqual(len(source['textSha256']),64)

    def test_multipart_snapshot_polls_pinned_parts_and_projects_recording_evidence(self):
        import test_transcript_bundle
        def selected(cid, *, access_check):
            with self.flow.connect() as db: access_check(db)
            return test_transcript_bundle.materials()
        with patch.object(self.flow.portal, 'materials', side_effect=selected): self.bridge.tick()
        submitted = self.requests[0][2]
        self.assertEqual([c['startMs'] for c in submitted['chunks']], [0, 0])
        self.assertEqual([c['recordingId'] for c in submitted['chunks']], ['part-a', 'part-b'])
        chunk = submitted['chunks'][1]
        evidence = {key: chunk[key] for key in ('id', 'text', 'speaker', 'recordingId', 'transcriptId')}
        evidence.update(seconds=0, endSeconds=2, time='00:00')
        result = {'id': 'a' * 64, 'status': 'completed', 'error': None,
            'assessment': {'revisionId': 'a' * 64, 'provenance': {'sourceHash': submitted['sourceHash']}, 'criteria': [
                {'id': 'skill', 'label': 'Навык', 'state': 'supported', 'level': 2, 'target': None,
                 'basis': 'narrated', 'evidence': [evidence]}]}}
        self.bridge.request = lambda *args: result
        with self.flow.connect() as db: db.execute('UPDATE as_bindings SET next_check=0')
        with patch.object(self.flow.portal, 'materials', side_effect=AssertionError('poll must use pinned input')):
            self.bridge.tick()
        with self.flow.connect() as db:
            value = jobs.read_candidate(db, self.person['id'], 'part-b')
            self.assertEqual(value['recordingIds'], ['part-a', 'part-b'])
            self.assertEqual(value['assessment']['criteria'][0]['evidence'][0]['recordingId'], 'part-b')
            self.assertEqual(len(value['assessment']['provenance']['sourceTranscripts']), 2)
            self.assertNotIn('sourceTranscript', value['assessment']['provenance'])
            self.assertIsNone(jobs.read_candidate(db, self.person['id'], 'foreign')['assessment'])

    def test_partial_multipart_never_submits_assessment(self):
        import test_transcript_bundle
        data = test_transcript_bundle.materials(); data['transcripts'].pop()
        with patch.object(self.flow.portal, 'materials', return_value=data): self.bridge.tick()
        self.assertEqual(self.requests, [])
        with self.flow.connect() as db:
            row = db.execute('SELECT error,assessment FROM as_bindings').fetchone()
            self.assertEqual(row[0], 'waiting_for_transcript')
            self.assertIsNone(row[1])

    def test_new_unselected_recording_invalidates_assessment_before_selection_exists(self):
        env = {'HR_CORE_CATALOG_ENABLED': '1', 'HR_MATERIALS_CORE_URL': 'http://127.0.0.1:8093',
               'HR_MATERIALS_CORE_KEY': 'test-key'}
        inventory = {'assets': [{'id': 'one', 'kind': 'video', 'state': 'ready'}]}
        primary = {'status': 'ready', 'selections': [{'recording_id': 'one', 'transcript_id': 'text-one',
                   'revision': 1, 'status': 'ready'}]}
        with patch.dict('os.environ', env), patch('core_source_client.CoreClient.primary_transcripts', return_value=primary), patch('core_source_client.CoreClient.materials', return_value=inventory):
            with self.flow.connect() as db:
                revision = jobs.candidate_revision(db, self.person['id'])
                db.execute("UPDATE as_bindings SET source_revision=?,status='completed',assessment=?",
                           (revision, json.dumps({'revisionId': 'saved', 'criteria': []})))
                self.assertIsNotNone(jobs.read_candidate(db, self.person['id'])['assessment'])
                inventory['assets'].append({'id': 'two', 'kind': 'video', 'state': 'pending'})
                self.assertIsNone(jobs.read_candidate(db, self.person['id'])['assessment'])
                inventory['assets'].reverse()
                next_revision = jobs.candidate_revision(db, self.person['id'])
                inventory['assets'].reverse()
                self.assertEqual(jobs.candidate_revision(db, self.person['id']), next_revision)

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
