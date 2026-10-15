"""Private review history for prepared intake profiles in temporary SQLite."""
import concurrent.futures
import json
import sqlite3
import unittest
from unittest.mock import patch

from domain import Problem
import employer_intake as intake
import employer_store as store
import test_employer_intake as fixture
import test_employer_recordings as recording_fixture


class EmployerIntakeReviewTest(unittest.TestCase):
    setUp = fixture.EmployerIntakeTest.setUp
    tearDown = fixture.EmployerIntakeTest.tearDown
    connect = fixture.EmployerIntakeTest.connect
    assert_problem = fixture.EmployerIntakeTest.assert_problem

    def imported(self, db, *, wid='a', vid=None, batch='review-fixture-123', recordings=None):
        vid = vid or self.va
        person = intake.add(db, wid, vid, {'batch': batch, 'scope': 'sfera',
                                          'entries': [{'name': 'Подготовленный профиль'}]})['candidates'][0]
        person['preparedProfile'] = {'cvText': 'Исходное резюме', 'recordings': recordings if recordings is not None else [
            {'publicUrl': 'https://disk.yandex.ru/i/fixture_recording', 'mime': 'video/mp4',
             'name': 'Interview.mp4', 'size': 123, 'duration': 90},
            {'publicUrl': 'https://disk.yandex.ru/i/second_recording', 'mime': 'audio/mpeg',
             'name': 'Audio.mp3', 'size': 234, 'durationSeconds': 120},
        ]}
        self.save(db, person)
        return self.person(db, person['id'], wid=wid, vid=vid)

    @staticmethod
    def save(db, person):
        # Source replacement is an internal import, never part of a review command.
        clean = {key: value for key, value in person.items()
                 if key not in ('screeningComments', 'reviewRejection', 'reviewResponse', 'reviewRevision')}
        db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(clean), person['id']))

    def person(self, db, cid, *, wid='a', vid=None):
        return next(person for person in intake.list_candidates(db, wid, vid or self.va)['candidates']
                    if person['id'] == cid)

    def comment(self, person, **updates):
        return {'vacancyId': self.va, 'id': person['id'], 'recordingIndex': 0, 'seconds': 12.5,
                'chapterTitle': 'Опыт работы', 'text': 'Уточнить размер команды.',
                'requestId': 'comment-fixture-123', 'expectedRevision': person['reviewRevision'], **updates}

    def rejection(self, person, **updates):
        return {'vacancyId': self.va, 'id': person['id'], 'rejectionAnswers': {
            'reason': 'Не подходит опыт', 'missing': 'Нет продаж в B2B', 'improve': 'Искать B2B-опыт'},
                'requestId': 'rejection-fixture-123', 'expectedRevision': person['reviewRevision'], **updates}

    def test_comment_persists_after_restart_without_creating_public_candidate_records(self):
        with self.connect() as db:
            person = self.imported(db)
            before = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in
                      ('cp_candidates', 'cp_applications', 'cp_consent_log', 'cp_interviews', 'cp_meetings')}
            initial = person['reviewRevision']
            self.assertIsNone(person['reviewRejection'])
            self.assertEqual(person['screeningComments'], [])
            with patch.object(intake, 'resolve_recording', side_effect=AssertionError('No external resolution')):
                result = intake.screening_comment(db, 'a', self.va, self.comment(person))
            note = result['candidates'][0]['screeningComments'][0]
            self.assertEqual(set(note), {'id', 'recordingIndex', 'recordingId', 'seconds', 'chapterTitle', 'text', 'createdAt'})
            self.assertEqual(note['seconds'], 12.5)
            self.assertEqual(note['text'], 'Уточнить размер команды.')
            self.assertNotIn('publicUrl', note)
            self.assertNotEqual(initial, result['candidates'][0]['reviewRevision'])
            self.assertLess(result['candidates'][0]['reviewRevision'], 2 ** 53)
        with self.connect() as db:
            intake.init(db)
            restored = self.person(db, person['id'])
            self.assertEqual(restored['screeningComments'], [note])
            self.assertEqual(before, {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in before})
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_two_recordings_keep_their_own_comment_identity_and_insertion_history(self):
        with self.connect() as db:
            person = self.imported(db)
            intake.screening_comment(db, 'a', self.va, self.comment(person))
            person = self.person(db, person['id'])
            intake.screening_comment(db, 'a', self.va, self.comment(person, recordingIndex=1, seconds=3,
                chapterTitle='', text='Комментарий к текущему моменту.', requestId='second-comment'))
            comments = self.person(db, person['id'])['screeningComments']
            self.assertEqual([note['recordingIndex'] for note in comments], [0, 1])
            self.assertNotEqual(comments[0]['recordingId'], comments[1]['recordingId'])
            self.assertEqual(comments[1]['chapterTitle'], '')

    def test_rejection_saves_three_answers_and_archives_atomically_without_erasing_comments(self):
        with self.connect() as db:
            person = self.imported(db)
            intake.screening_comment(db, 'a', self.va, self.comment(person))
            person = self.person(db, person['id'])
            result = intake.review_reject(db, 'a', self.va, self.rejection(person))['candidates'][0]
            self.assertTrue(result['archived'])
            self.assertEqual(result['reviewRejection']['missing'], 'Нет продаж в B2B')
            self.assertEqual(len(result['screeningComments']), 1)
        with self.connect() as db:
            self.assertEqual(self.person(db, person['id']), result)
            self.assertEqual(db.execute('SELECT kind FROM em_intake_review_events ORDER BY rowid').fetchall(),
                             [('comment',), ('rejection',)])
            intake.archive(db, 'a', self.va, {'id': person['id'], 'archived': False})
            restored = self.person(db, person['id'])
            self.assertEqual(restored['reviewRejection'], result['reviewRejection'])
            self.assertEqual(restored['screeningComments'], result['screeningComments'])

    def test_rejection_requires_only_reason_and_restore_does_not_reset_saved_response(self):
        with self.connect() as db:
            person = self.imported(db)
            intake.review_reject(db, 'a', self.va, self.rejection(person, rejectionAnswers={'reason': 'Первый отказ'}))
            saved = self.person(db, person['id'])
            self.assertEqual(saved['reviewRejection']['missing'], '')
            self.assertEqual(saved['reviewRejection']['improve'], '')
            intake.archive(db, 'a', self.va, {'id': person['id'], 'archived': False})
            person = self.person(db, person['id'])
            self.assert_problem(409, intake.review_reject, db, 'a', self.va, self.rejection(person,
                requestId='second-rejection', rejectionAnswers={'reason': 'Повторный отказ', 'missing': '  ', 'improve': ''}))
            self.assertEqual(self.person(db, person['id'])['reviewRejection']['reason'], 'Первый отказ')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM em_intake_review_events WHERE kind='rejection'").fetchone()[0], 1)

    def test_cross_workspace_vacancy_and_candidate_never_share_comments_or_reasons(self):
        with self.connect() as db:
            person = self.imported(db)
            same_vacancy = self.imported(db, batch='another-person-123')
            own_other = self.imported(db, vid=self.va2)
            foreign = self.imported(db, wid='b', vid=self.vb)
            intake.screening_comment(db, 'a', self.va, self.comment(person))
            for wid, vid, other in (('a', self.va, same_vacancy), ('a', self.va2, own_other), ('b', self.vb, foreign)):
                self.assertEqual(self.person(db, other['id'], wid=wid, vid=vid)['screeningComments'], [])
            for fn, body in ((intake.screening_comment, self.comment(person)), (intake.review_reject, self.rejection(person))):
                self.assert_problem(404, fn, db, 'b', self.va, body)
                self.assert_problem(404, fn, db, 'b', self.vb, {**body, 'vacancyId': self.vb})
                self.assert_problem(404, fn, db, 'a', self.va2, {**body, 'vacancyId': self.va2})
                self.assert_problem(409, fn, db, 'a', self.va, {**body, 'vacancyId': self.va2})

    def test_stale_revision_and_reused_request_id_cannot_overwrite_or_double_append(self):
        with self.connect() as db:
            person = self.imported(db)
            command = self.comment(person)
            first = intake.screening_comment(db, 'a', self.va, command)
        with self.connect() as db:
            self.assertEqual(intake.screening_comment(db, 'a', self.va, command), first)
            self.assert_problem(409, intake.screening_comment, db, 'a', self.va, {**command, 'text': 'Другой текст'})
            self.assert_problem(409, intake.screening_comment, db, 'a', self.va, {**command, 'requestId': 'stale-comment'})
            self.assert_problem(409, intake.review_reject, db, 'a', self.va, self.rejection(person))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 1)

    def test_reject_retry_succeeds_after_archive_but_a_new_command_is_blocked(self):
        with self.connect() as db:
            person = self.imported(db)
            command = self.rejection(person)
            result = intake.review_reject(db, 'a', self.va, command)
        with self.connect() as db:
            self.assertEqual(intake.review_reject(db, 'a', self.va, command), result)
            archived = self.person(db, person['id'])
            self.assert_problem(409, intake.review_reject, db, 'a', self.va, self.rejection(archived, requestId='new-reject'))
            self.assert_problem(409, intake.screening_comment, db, 'a', self.va, self.comment(archived))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 1)

    def test_replacing_source_before_first_comment_invalidates_stale_index(self):
        with self.connect() as db:
            person = self.imported(db)
            command = self.comment(person)
            person['preparedProfile']['recordings'][0]['publicUrl'] = 'https://disk.yandex.ru/i/replaced_recording'
            self.save(db, person)
            updated = self.person(db, person['id'])
            self.assertNotEqual(updated['reviewRevision'], person['reviewRevision'])
            self.assert_problem(409, intake.screening_comment, db, 'a', self.va, command)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 0)

    def test_replacing_recording_hides_its_notes_and_retry_does_not_return_old_snapshot(self):
        with self.connect() as db:
            person = self.imported(db)
            command = self.comment(person)
            old = intake.screening_comment(db, 'a', self.va, command)['candidates'][0]['screeningComments'][0]
            person = self.person(db, person['id'])
            original = person['preparedProfile']['recordings'][0].copy()
            person['preparedProfile']['recordings'][0]['size'] = 456
            self.save(db, person)
            self.assertEqual(self.person(db, person['id'])['screeningComments'], [])
            self.assertEqual(intake.screening_comment(db, 'a', self.va, command)['candidates'][0]['screeningComments'], [])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 1)
            person['preparedProfile']['recordings'][0] = original
            self.save(db, person)
            self.assertEqual(self.person(db, person['id'])['screeningComments'], [old])

    def test_reordering_recordings_does_not_move_an_existing_note_to_the_new_index(self):
        with self.connect() as db:
            person = self.imported(db)
            intake.screening_comment(db, 'a', self.va, self.comment(person))
            person['preparedProfile']['recordings'].reverse()
            self.save(db, person)
            self.assertEqual(self.person(db, person['id'])['screeningComments'], [])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 1)

    def test_invalid_timing_source_and_payload_leave_no_review_events(self):
        with self.connect() as db:
            person = self.imported(db)
            for seconds in (-1, True, '12', None, float('nan'), float('inf'), 14401, 91, 10 ** 500):
                self.assert_problem(400, intake.screening_comment, db, 'a', self.va, self.comment(person, seconds=seconds))
            for index, status in ((-1, 400), (True, 400), (10, 400), (2, 404)):
                self.assert_problem(status, intake.screening_comment, db, 'a', self.va, self.comment(person, recordingIndex=index))
            for change in ({'text': ''}, {'text': 'x' * 4001}, {'text': 'a\x00b'}, {'chapterTitle': 'x' * 241},
                           {'expectedRevision': True}, {'expectedRevision': -1}, {'expectedRevision': 2 ** 53},
                           {'publicUrl': 'https://disk.yandex.ru/i/other_source'}, {'workspaceId': 'b'},
                           {'requestId': ''}):
                self.assert_problem(400, intake.screening_comment, db, 'a', self.va, self.comment(person, **change))
            for answers in ({}, {'reason': ''}, {'reason': None}, {'reason': 'x' * 4001},
                            {'reason': 'Причина', 'missing': None}, {'reason': 'Причина', 'unknown': ''}):
                self.assert_problem(400, intake.review_reject, db, 'a', self.va, self.rejection(person, rejectionAnswers=answers))
            self.assertFalse(self.person(db, person['id'])['archived'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 0)

    def test_missing_or_untrusted_stored_recording_cannot_be_invented_by_caller(self):
        with self.connect() as db:
            person = self.imported(db, recordings=[])
            self.assert_problem(404, intake.screening_comment, db, 'a', self.va, self.comment(person))
            person['preparedProfile']['recordings'] = [None]
            self.save(db, person)
            person = self.person(db, person['id'])
            self.assert_problem(404, intake.screening_comment, db, 'a', self.va, self.comment(person))
            for recording in ({'publicUrl': 'https://127.0.0.1/private', 'mime': 'video/mp4'},
                              {'publicUrl': 'https://disk.yandex.ru/i/fixture_recording', 'mime': 'text/plain'}):
                person['preparedProfile']['recordings'] = [recording]
                self.save(db, person)
                person = self.person(db, person['id'])
                self.assert_problem(422, intake.screening_comment, db, 'a', self.va, self.comment(person))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 0)

    def test_closed_vacancy_blocks_new_review_but_keeps_history_readable(self):
        with self.connect() as db:
            person = self.imported(db)
            command = self.comment(person)
            intake.screening_comment(db, 'a', self.va, command)
            row = db.execute('SELECT body FROM cp_vacancies WHERE id=?', (self.va,)).fetchone()
            vacancy = json.loads(row[0]); vacancy['status'] = 'closed'
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps(vacancy), self.va))
            current = self.person(db, person['id'])
            self.assertEqual(len(intake.screening_comment(db, 'a', self.va, command)['candidates'][0]['screeningComments']), 1)
            self.assert_problem(409, intake.screening_comment, db, 'a', self.va, self.comment(current, requestId='new-on-closed'))
            self.assert_problem(409, intake.review_reject, db, 'a', self.va, self.rejection(current))

    def test_rejection_failure_rolls_back_event_archive_and_command_together(self):
        with self.connect() as db:
            person = self.imported(db)
            db.execute('''CREATE TRIGGER fail_intake_archive BEFORE UPDATE ON em_intake_candidates
              WHEN json_extract(NEW.body,'$.archived')=1 BEGIN SELECT RAISE(ABORT,'synthetic'); END''')
            with self.assertRaises(sqlite3.IntegrityError):
                intake.review_reject(db, 'a', self.va, self.rejection(person))
            self.assertFalse(self.person(db, person['id'])['archived'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_store_commands').fetchone()[0], 0)

    def test_comment_rollback_preserves_caller_transaction_and_leaves_no_command(self):
        with self.connect() as db:
            person = self.imported(db)
        db = self.connect()
        try:
            intake.screening_comment(db, 'a', self.va, self.comment(person))
            self.assertTrue(db.in_transaction)
            db.rollback()
        finally:
            db.close()
        with self.connect() as db:
            self.assertEqual(self.person(db, person['id'])['screeningComments'], [])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_store_commands').fetchone()[0], 0)

    def test_concurrent_retry_creates_one_event_and_one_command(self):
        with self.connect() as db:
            person = self.imported(db)
        command = self.comment(person)
        def append(_):
            with self.connect() as db:
                return intake.screening_comment(db, 'a', self.va, command)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(append, range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_store_commands').fetchone()[0], 1)


class EmployerIntakeRecordingScopeTest(unittest.TestCase):
    setUpClass = classmethod(recording_fixture.ImportedRecordingTest.setUpClass.__func__)
    tearDownClass = classmethod(recording_fixture.ImportedRecordingTest.tearDownClass.__func__)
    setUp = recording_fixture.ImportedRecordingTest.setUp
    request = recording_fixture.ImportedRecordingTest.request
    ok = recording_fixture.ImportedRecordingTest.ok
    employer = recording_fixture.ImportedRecordingTest.employer
    vacancy = recording_fixture.ImportedRecordingTest.vacancy
    imported = recording_fixture.ImportedRecordingTest.imported

    def test_frontend_recording_payload_checks_optional_vacancy_and_keeps_legacy_request(self):
        owner = self.employer()
        person, vacancy = self.imported(owner)
        other_vacancy = self.vacancy(owner)
        command = {'id': person['id'], 'index': 0, 'vacancyId': vacancy['id']}
        with patch.object(intake, 'resolve_recording', return_value={
                'url': 'https://downloader.disk.yandex.ru/file', 'mime': 'video/mp4'}) as resolve:
            self.assertIn('url', self.ok('employer/intake/recording', command, token=owner['token']))
            self.assertIn('url', self.ok('employer/intake/recording',
                {'id': person['id'], 'index': 0}, token=owner['token']))
            self.assertEqual(resolve.call_count, 2)
            self.assertEqual(self.request('employer/intake/recording',
                {**command, 'vacancyId': other_vacancy['id']}, token=owner['token'])[0], 409)
            for invalid in (None, '', True, []):
                self.assertEqual(self.request('employer/intake/recording',
                    {**command, 'vacancyId': invalid}, token=owner['token'])[0], 400)
            self.assertEqual(resolve.call_count, 2)


if __name__ == '__main__':
    unittest.main()
