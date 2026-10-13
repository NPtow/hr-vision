"""Private screening annotations on temporary canonical records, no network."""
import concurrent.futures
import json
import unittest
import uuid

from domain import Problem, iso, now
import employer_projection
import employer_store as store
import employer_workflow as wf
import test_employer_store as fixture


class ScreeningWorkflowTest(unittest.TestCase):
    tearDown = fixture.EmployerStoreTest.tearDown
    connect = fixture.EmployerStoreTest.connect
    person = fixture.EmployerStoreTest.person
    batch = staticmethod(fixture.EmployerStoreTest.batch)
    import_people = fixture.EmployerStoreTest.import_people
    accepted = fixture.EmployerStoreTest.accepted
    assert_problem = fixture.EmployerStoreTest.assert_problem

    def setUp(self):
        fixture.EmployerStoreTest.setUp(self)
        self.cid, self.other_cid = self.person(), self.person()
        self.aid, self.other_aid = self.accepted([self.cid, self.other_cid])
        self.interview_id, self.recording_id = 'interview-' + self.cid, 'recording-one'
        self.room = {'id': 'screening-room-one', 'name': 'screening-room-name'}
        with self.connect() as db:
            db.execute('UPDATE cp_interviews SET room=? WHERE id=?', (json.dumps(self.room), self.interview_id))
            db.execute('INSERT INTO cp_media_state VALUES (?,?,?,?,?)',
                (self.interview_id, 'interview', json.dumps(self.room),
                 json.dumps({'recordings': [{'id': self.recording_id, 'duration': 30}]}), iso(now())))

    def state(self, aid=None, *, actor='manager'):
        with self.connect() as db:
            return wf.load(db, aid or self.aid, actor=actor,
                           candidate_id=self.cid if actor == 'candidate' else None)

    def command(self, **changes):
        return {'interviewId': self.interview_id, 'recordingId': self.recording_id,
                'seconds': 12.5, 'chapterTitle': 'Опыт работы', 'text': 'Чёткий пример задачи',
                'requestId': uuid.uuid4().hex, 'expectedRevision': self.state()['revision'], **changes}

    def act(self, action, body, *, aid=None, vacancy_id=None):
        with self.connect() as db:
            return wf.manager_action(db, aid or self.aid, action, body,
                                     manager_id='workspace:a', vacancy_id=vacancy_id)

    def comment(self, body=None):
        return self.act('screening.comment.add', body if body is not None else self.command())

    def test_append_history_idempotency_and_private_scope_survive_reopen(self):
        first_body = self.command()
        first = self.comment(first_body)
        first_comment = first['state']['screeningComments'][0]
        self.assertEqual(first['events'], [])
        self.comment(self.command(text='Второй комментарий к тому же моменту'))
        before = self.state()
        replayed = self.comment(first_body)
        self.assertTrue(replayed['replayed'])
        self.assertEqual(replayed['state'], before)
        self.assertEqual(before['screeningComments'][0], first_comment)
        self.assertEqual(len(before['screeningComments']), 2)
        self.assert_problem(409, self.comment, {**first_body, 'text': 'Замена истории'})
        self.assertEqual(self.state(self.other_aid)['screeningComments'], [])
        with self.connect() as db:
            # Same canonical person, another employer application, remains private.
            vacancy = store.create_vacancy(db, 'b', {'title': 'Другая вакансия'})
            batch = store.import_batch(db, 'b', vacancy['id'], self.batch([self.cid]), self.portal)
            second_aid = batch['applicationIds'][0]
            self.portal.decide_application(db, self.cid, {'applicationId': second_aid, 'decision': 'accepted'})
            self.assertEqual(wf.load(db, second_aid)['screeningComments'], [])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meeting_feedback').fetchone()[0], 0)
        candidate = self.state(actor='candidate')
        self.assertNotIn('screeningComments', candidate)
        self.assertNotIn('reviewRejection', candidate)
        self.assertNotIn(first_comment['text'], json.dumps(candidate, ensure_ascii=False))

    def test_source_timing_fields_and_command_validation_are_atomic(self):
        cases = [(404, {'interviewId': 'interview-' + self.other_cid}),
                 (409, {'recordingId': 'foreign-recording'}), (400, {'recordingId': None}),
                 (400, {'seconds': -1}), (400, {'seconds': 30.01}), (400, {'seconds': True}),
                 (400, {'seconds': float('nan')}), (400, {'seconds': float('inf')}),
                 (400, {'seconds': '12'}), (400, {'text': ''}),
                 (400, {'text': 'x' * 4001}), (400, {'chapterTitle': 'x' * 241}),
                 (400, {'requestId': None}), (409, {'expectedRevision': None})]
        for status, changes in cases:
            with self.subTest(changes=changes):
                self.assert_problem(status, self.comment, self.command(**changes))
        missing = self.command()
        del missing['expectedRevision']
        self.assert_problem(409, self.comment, missing)
        self.assert_problem(404, self.act, 'screening.comment.add', self.command(), vacancy_id='other')
        self.assertEqual(self.state()['revision'], 0)
        self.assertEqual(self.state()['screeningComments'], [])

    def test_recording_cache_must_match_current_room_and_source_must_be_completed(self):
        with self.connect() as db:
            db.execute('UPDATE cp_interviews SET room=? WHERE id=?',
                       (json.dumps({'id': 'replaced', 'name': 'replaced'}), self.interview_id))
        self.assert_problem(409, self.comment, self.command())
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET room=?,status='booked' WHERE id=?",
                       (json.dumps(self.room), self.interview_id))
        self.assert_problem(409, self.comment, self.command())
        self.assertEqual(self.state()['screeningComments'], [])

    def test_transcript_only_comment_keeps_exact_source_without_inventing_recording(self):
        with self.connect() as db:
            db.execute('DELETE FROM cp_media_state WHERE meeting_id=?', (self.interview_id,))
        result = self.comment(self.command(recordingId=None))
        self.assertIsNone(result['state']['screeningComments'][0]['recordingId'])
        self.assertEqual(result['state']['screeningComments'][0]['interviewId'], self.interview_id)

    def test_stale_and_concurrent_comments_do_not_lose_or_duplicate_history(self):
        shared = self.command()
        def run(body):
            try:
                return self.comment(body)
            except Problem as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [shared, shared]))
        self.assertEqual(sum(result['replayed'] for result in results), 1)
        self.assertEqual(len(self.state()['screeningComments']), 1)
        old = {**shared, 'requestId': uuid.uuid4().hex}
        self.assert_problem(409, self.comment, old)
        commands = [self.command(text=words) for words in ('Первый', 'Второй')]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, commands))
        self.assertEqual(results.count(409), 1)
        self.assertEqual(len(self.state()['screeningComments']), 2)

    def test_closed_archived_unapproved_and_rejected_applications_block_new_comments(self):
        with self.connect() as db:
            db.execute("UPDATE cp_applications SET status='pending' WHERE id=?", (self.aid,))
        self.assert_problem(409, self.comment, self.command())
        with self.connect() as db:
            db.execute("UPDATE cp_applications SET status='accepted',archived_at=? WHERE id=?", (iso(now()), self.aid))
        self.assert_problem(409, self.comment, self.command())
        with self.connect() as db:
            db.execute('UPDATE cp_applications SET archived_at=NULL WHERE id=?', (self.aid,))
            saved = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (self.vacancy['id'],)).fetchone()[0])
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?',
                       (json.dumps({**saved, 'status': 'closed'}), self.vacancy['id']))
        self.assert_problem(409, self.comment, self.command())
        with self.connect() as db:
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps(saved), self.vacancy['id']))
        self.act('reject', {})
        self.assert_problem(409, self.comment, self.command())

    def test_rejection_answers_are_private_immutable_and_separate_from_feedback(self):
        answers = {'reason': 'Опыт в другом сегменте', 'missing': 'Крупные сделки', 'improve': 'Примеры переговоров'}
        body = {'rejectionAnswers': answers, 'requestId': 'reject-once', 'expectedRevision': 0}
        result = self.act('reject', body)
        self.assertEqual([event['type'] for event in result['events']], ['employer.declined'])
        self.assertEqual({key: result['state']['reviewRejection'][key] for key in answers}, answers)
        retry = self.act('reject', body)
        self.assertTrue(retry['replayed'])
        self.assertEqual(retry['events'], [])
        self.assertEqual(retry['state'], self.state())
        self.assert_problem(409, self.act, 'reject', {**body, 'requestId': 'replace',
            'expectedRevision': self.state()['revision'], 'rejectionAnswers': {**answers, 'reason': 'Подмена'}})
        candidate = self.state(actor='candidate')
        self.assertNotIn('reviewRejection', candidate)
        self.assertNotIn(answers['reason'], json.dumps(candidate, ensure_ascii=False))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_review_rejections').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meeting_feedback').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_employer_stages').fetchone()[0], 0)

    def test_rejection_invalid_answers_do_not_decline_or_send_event_and_legacy_still_works(self):
        for answers in ({'reason': ''}, {'reason': 'x' * 4001}, {'reason': 'ok', 'other': 'field'}, None):
            self.assert_problem(400, self.act, 'reject', {'rejectionAnswers': answers,
                'requestId': uuid.uuid4().hex, 'expectedRevision': 0})
        self.assertEqual(self.state()['decision'], 'review')
        self.assertEqual(self.state()['messages'], [])
        self.assert_problem(409, self.act, 'reject', {'rejectionAnswers': {'reason': 'Опыт'}, 'requestId': 'no-revision'})
        result = self.act('reject', {})
        self.assertEqual(result['state']['decision'], 'declined')
        self.assertIsNone(result['state']['reviewRejection'])
        self.assertEqual([event['type'] for event in result['events']], ['employer.declined'])

    def test_source_profile_uses_real_contact_values_and_stored_resume_metadata(self):
        with self.connect() as db:
            profile = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (self.cid,)).fetchone()[0])
            profile.update(phone='+7 999 000 00 00', telegram='@fixture_candidate')
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (json.dumps(profile), self.cid))
            content = 'Настоящий тестовый файл'.encode()
            db.execute('INSERT INTO cp_resumes VALUES (?,?,?,?,?,?,?)',
                (self.cid, 'candidate.txt', 'text/plain', content, content.decode(), '{}', iso(now())))
            source = employer_projection._profile(db, self.cid, profile, self.portal)
            self.assertEqual(source['resume']['size'], len(content))
            self.assertEqual(source['resume']['filename'], 'candidate.txt')
            self.assertEqual(source['contacts'], [{'kind': 'email', 'value': self.cid + '@example.test'},
                {'kind': 'phone', 'value': '+7 999 000 00 00'}, {'kind': 'telegram', 'value': '@fixture_candidate'}])
            self.assertNotIn('secretToken', json.dumps(source))


if __name__ == '__main__':
    unittest.main()
