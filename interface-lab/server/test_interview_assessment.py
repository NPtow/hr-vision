"""Real employer HTTP and temporary SQLite; no model or production data."""
import concurrent.futures
import contextlib
import hashlib
import json
import unittest
import uuid
from unittest.mock import patch

from domain import Problem
import employer_access as access
import interview_assessment as assessment
import test_employer_candidate_review_acceptance as fixture


def projection(revision='assessment-revision-1'):
    return {'revisionId': revision, 'provenance': {'method': 'synthetic-unit', 'productionCore': False},
      'criteria': [
        {'id': 'A01', 'label': 'Диалог', 'state': 'supported', 'level': 3, 'target': None,
         'basis': 'observed', 'summary': 'Разобрали задачу.', 'nextQuestion': '',
         'stateLabel': 'Показано в кейсе', 'limitation': 'Синтетический пример.',
         'evidence': [{'id': 'e1', 'text': 'Подтвердил следующий шаг.', 'seconds': 2,
                       'endSeconds': 6, 'sourceId': 'source-1', 'turnIds': [1], 'sourceKind': 'interview', 'availability': 'ready'}]},
        {'id': 'A02', 'label': 'Другое умение', 'state': 'unknown', 'level': None, 'target': None,
         'basis': None, 'summary': 'Не обсуждали.', 'nextQuestion': 'Что сделали?',
         'stateLabel': 'Недостаточно данных', 'limitation': '', 'evidence': []},
      ]}


class InterviewAssessmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixture.EmployerCandidateReviewAcceptanceTest.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): fixture.EmployerCandidateReviewAcceptanceTest.tearDownClass.__func__(cls)
    for _name in ('request', 'ok', 'employer', 'vacancy', 'state', 'imported'):
        locals()[_name] = getattr(fixture.EmployerCandidateReviewAcceptanceTest, _name)

    def setUp(self):
        fixture.EmployerCandidateReviewAcceptanceTest.setUp(self)
        self.owner = self.employer()
        self.job = self.vacancy(self.owner)
        self.person = self.imported(self.owner, self.job)
        self.scope = {'source': 'intake', 'id': self.person['id'], 'index': 0, 'vacancyId': self.job['id']}
        with self.app.connect() as db:
            person = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (self.person['id'],)).fetchone()[0])
            person['preparedProfile']['transcriptText'] = '[00:02-00:06] К: Подтвердил следующий шаг.'
            person['preparedProfile']['recordings'][0]['duration'] = 20
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(person), self.person['id']))
            self.binding, self.fingerprint, _ = assessment.intake_binding(db, self.owner['workspace']['id'], self.scope)
            assessment.import_assessment(db, self.owner['workspace']['id'], self.binding, self.fingerprint, projection())

    def read(self, **updates):
        return self.request('employer/interview-assessment', {**self.scope, **updates}, token=self.owner['token'])

    def feedback(self, **updates):
        body = {**self.scope, 'revisionId': 'assessment-revision-1', 'criterionId': 'A01', 'evidenceId': 'e1',
                'answer': 'yes', 'comment': 'Проверить на встрече.', 'expectedVersion': 0,
                'requestId': uuid.uuid4().hex, **updates}
        return self.request('employer/interview-assessment/feedback', body, token=self.owner['token'])

    def test_authorized_read_is_exact_and_provenance_is_preserved(self):
        status, result, headers = self.read()
        self.assertEqual(status, 200, result)
        self.assertEqual(result['assessment'], {**projection(), 'feedback': []})
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertIsNone(result['assessment']['criteria'][1]['level'])
        self.assertFalse(result['assessment']['provenance']['productionCore'])

    def test_idor_wrong_vacancy_index_and_missing_auth_are_rejected(self):
        other = self.employer('Other')
        other_job = self.vacancy(self.owner)
        self.assertEqual(self.request('employer/interview-assessment', self.scope, token=other['token'])[0], 404)
        self.assertEqual(self.request('employer/interview-assessment', self.scope)[0], 401)
        self.assertEqual(self.read(vacancyId=other_job['id'])[0], 404)
        self.assertEqual(self.read(index=1)[0], 404)
        self.assertEqual(self.read(index=False)[0], 400)
        self.assertEqual(self.read(workspaceId=other['workspace']['id'])[0], 400)
        self.assertEqual(self.request('employer/interview-assessment/feedback', {
            **self.scope, 'revisionId': 'assessment-revision-1', 'criterionId': 'A01', 'evidenceId': 'e1',
            'answer': 'yes', 'comment': '', 'expectedVersion': 0, 'requestId': 'foreign-request'}, token=other['token'])[0], 404)

    def test_fragment_feedback_is_persistent_versioned_audited_and_private_per_account(self):
        status, saved, _ = self.feedback()
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved['feedback']['version'], 1)
        self.assertEqual(self.read()[1]['assessment']['feedback'], [saved['feedback']])
        with self.app.connect() as db:
            assessment.init(db)
            principal = access.authenticate(db, {'Authorization': 'Bearer ' + self.owner['token']})
            restored = assessment.read(db, principal, self.binding, self.fingerprint)
            self.assertEqual(restored['assessment']['feedback'], [saved['feedback']])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ia_assessment_feedback_events WHERE source_key=?', (self.binding,)).fetchone()[0], 1)
        colleague = self.employer('Colleague')
        with self.app.connect() as db:
            identity = access.authenticate(db, {'Authorization': 'Bearer ' + colleague['token']})
            # Production currently allows one account per workspace. Exercise
            # the separate storage key without changing that authentication rule.
            principal = {'workspaceId': self.owner['workspace']['id'], 'accountId': identity['accountId']}
            self.assertEqual(assessment.read(db, principal, self.binding, self.fingerprint)['assessment']['feedback'], [])

    def test_idempotent_retry_is_before_version_check_and_key_changes_fail(self):
        rid = uuid.uuid4().hex
        first = self.feedback(requestId=rid)
        self.assertEqual(first[0], 200, first)
        self.assertEqual(self.feedback(requestId=rid)[1], first[1])
        self.assertEqual(self.feedback(requestId=rid, comment='Different')[0], 409)
        self.assertEqual(self.feedback(expectedVersion=0)[0], 409)
        second = self.feedback(expectedVersion=1, answer='no', comment='Уточнить контекст.')
        self.assertEqual(second[0], 200, second)
        self.assertEqual(second[1]['feedback']['version'], 2)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ia_assessment_feedback_events WHERE source_key=?', (self.binding,)).fetchone()[0], 2)

    def test_only_immutable_criterion_evidence_pairs_can_receive_feedback(self):
        for updates, expected in [({'criterionId': 'foreign'}, 404), ({'evidenceId': 'foreign'}, 404),
            ({'criterionId': 'A02'}, 404), ({'revisionId': 'foreign'}, 409),
            ({'answer': 'hire'}, 400), ({'answer': {'value': 'yes'}}, 400),
            ({'expectedVersion': True}, 400), ({'comment': 'secret\x00control'}, 400),
            ({'quote': 'client supplied'}, 400)]:
            with self.subTest(updates=updates): self.assertEqual(self.feedback(**updates)[0], expected)

    def test_source_replacement_and_vacancy_edits_hide_old_assessment_and_reject_feedback(self):
        self.assertEqual(self.feedback()[0], 200)
        with self.app.connect() as db:
            raw = db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (self.person['id'],)).fetchone()[0]
            person = json.loads(raw)
            person['preparedProfile']['transcriptText'] += '\n[00:07-00:08] К: Новый источник.'
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(person), self.person['id']))
        self.assertIsNone(self.read()[1]['assessment'])
        self.assertEqual(self.feedback(expectedVersion=1)[0], 409)
        with self.app.connect() as db:
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (raw, self.person['id']))
            row = db.execute('SELECT body FROM cp_vacancies WHERE id=?', (self.job['id'],)).fetchone()
            job = json.loads(row[0]); job['expectations'] = 'Иные требования вакансии'
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps(job), self.job['id']))
        self.assertIsNone(self.read()[1]['assessment'])
        self.assertEqual(self.feedback(expectedVersion=1)[0], 409)

    def test_source_url_replacement_cannot_reattach_assessment_or_idempotent_retry(self):
        rid = uuid.uuid4().hex
        self.assertEqual(self.feedback(requestId=rid)[0], 200)
        with self.app.connect() as db:
            raw = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (self.person['id'],)).fetchone()[0])
            raw['preparedProfile']['recordings'][0]['publicUrl'] = 'https://disk.yandex.ru/i/other_fixture'
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(raw), self.person['id']))
        self.assertIsNone(self.read()[1]['assessment'])
        self.assertEqual(self.feedback(requestId=rid)[0], 409)

    def test_missing_transcript_is_an_unavailable_assessment_not_a_broken_player(self):
        with self.app.connect() as db:
            raw = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (self.person['id'],)).fetchone()[0])
            raw['preparedProfile'].pop('transcriptText')
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(raw), self.person['id']))
        self.assertEqual(self.read()[0:2], (200, {'assessment': None}))
        self.assertEqual(self.feedback()[0], 409)

    def test_existing_multipart_recordings_read_null_without_binding_feedback(self):
        with self.app.connect() as db:
            raw = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (self.person['id'],)).fetchone()[0])
            raw['preparedProfile']['recordings'].append({
                'publicUrl': 'https://disk.yandex.ru/i/second_source', 'mime': 'video/mp4', 'duration': 20})
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(raw), self.person['id']))
        for index in (0, 1):
            with self.subTest(index=index):
                self.assertEqual(self.read(index=index)[0:2], (200, {'assessment': None}))
                self.assertEqual(self.feedback(index=index)[0], 409)
        self.assertEqual(self.read(index=2)[0], 404)
        with self.app.connect() as db:
            with self.assertRaises(Problem) as caught:
                assessment.intake_binding(db, self.owner['workspace']['id'], self.scope)
            self.assertEqual(caught.exception.status, 422)

    def test_simultaneous_first_saves_cannot_overwrite_each_other(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.feedback(), range(2)))
        self.assertEqual(sorted(item[0] for item in results), [200, 409], results)
        self.assertEqual(self.read()[1]['assessment']['feedback'][0]['version'], 1)

    def test_immutable_import_replay_and_revisions_do_not_reuse_feedback(self):
        self.assertEqual(self.feedback()[0], 200)
        with self.app.connect() as db:
            args = (db, self.owner['workspace']['id'], self.binding, self.fingerprint)
            self.assertTrue(assessment.import_assessment(*args, projection())['replayed'])
            changed = projection(); changed['criteria'][0]['summary'] = 'Другой вывод'
            with self.assertRaises(Problem) as caught: assessment.import_assessment(*args, changed)
            self.assertEqual(caught.exception.status, 409)
            assessment.import_assessment(*args, projection('assessment-revision-2'))
        value = self.read()[1]['assessment']
        self.assertEqual(value['revisionId'], 'assessment-revision-2')
        self.assertEqual(value['feedback'], [])
        self.assertEqual(self.feedback(expectedVersion=1)[0], 409)

    def test_revoked_session_cannot_read_or_save(self):
        with self.app.connect() as db:
            db.execute('DELETE FROM em_sessions WHERE token_hash=?', (hashlib.sha256(self.owner['token'].encode()).hexdigest(),))
        self.assertEqual(self.read()[0], 401)
        self.assertEqual(self.feedback()[0], 401)

    def test_source_is_rechecked_inside_write_transaction_after_authorization(self):
        original = assessment.store._atomic
        changed = False

        @contextlib.contextmanager
        def racing_source(db):
            nonlocal changed
            if not changed:
                changed = True
                with self.app.connect() as other:
                    raw = json.loads(other.execute('SELECT body FROM em_intake_candidates WHERE id=?', (self.person['id'],)).fetchone()[0])
                    raw['preparedProfile']['transcriptText'] += '\n[00:08-00:09] К: Новая версия.'
                    other.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(raw), self.person['id']))
            with original(db): yield

        with patch.object(assessment.store, '_atomic', racing_source):
            self.assertEqual(self.feedback()[0], 409)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ia_assessment_feedback_events WHERE source_key=?', (self.binding,)).fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
