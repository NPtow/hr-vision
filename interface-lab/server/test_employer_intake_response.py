"""Employer invitation intent stays private and separate from scheduling/account creation."""
import concurrent.futures
import datetime as dt
import json
import sqlite3
import unittest
from unittest.mock import patch

from domain import Problem, iso, now
import employer_intake as intake
import employer_workflow
import test_employer_intake_review as fixture
from test_employer_dsa_examples import example_payload


class EmployerIntakeResponseTest(unittest.TestCase):
    setUp = fixture.EmployerIntakeReviewTest.setUp
    tearDown = fixture.EmployerIntakeReviewTest.tearDown
    connect = fixture.EmployerIntakeReviewTest.connect
    assert_problem = fixture.EmployerIntakeReviewTest.assert_problem
    imported = fixture.EmployerIntakeReviewTest.imported
    save = staticmethod(fixture.EmployerIntakeReviewTest.save)
    person = fixture.EmployerIntakeReviewTest.person
    comment = fixture.EmployerIntakeReviewTest.comment
    rejection = fixture.EmployerIntakeReviewTest.rejection

    def response(self, person, *, decision='invite', **updates):
        command = {'id': person['id'], 'vacancyId': self.va, 'decision': decision,
                   'expectedRevision': person['reviewRevision'], 'requestId': 'response-fixture-123'}
        if decision == 'reject':
            command['rejectionAnswers'] = {'reason': 'Не подходит опыт', 'missing': '', 'improve': ''}
        return {**command, **updates}

    @staticmethod
    def public_snapshot(db):
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if row[0].startswith('cp_') or 'outbox' in row[0] or 'feedback' in row[0]]
        return {table: db.execute('SELECT * FROM "' + table + '"').fetchall() for table in tables}

    def test_four_examples_accept_private_invite_without_accounts_consents_meetings_or_sends(self):
        with self.connect() as db:
            before = self.public_snapshot(db)
            expected = {}
            for profile in example_payload()['profiles']:
                source_id = profile['sourceCandidateId']
                person = self.imported(db, batch='source-example-' + source_id)
                person['preparedProfile'] = profile['preparedProfile']; self.save(db, person)
                person = self.person(db, person['id'])
                self.assertIsNone(person['reviewResponse'])
                raw_before = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (person['id'],)).fetchone()[0])
                command = self.response(person, requestId='invite-' + source_id)
                result = intake.review_respond(db, 'a', self.va, command)
                current = self.person(db, person['id'])
                self.assertEqual(result['response'], {'decision': 'invite', 'createdAt': current['invitationRequestedAt']})
                self.assertIsNone(result['reviewRejection'])
                self.assertEqual(current['reviewResponse'], result['response'])
                self.assertNotEqual(current['reviewRevision'], person['reviewRevision'])
                self.assertFalse(current['archived'])
                raw_after = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (person['id'],)).fetchone()[0])
                self.assertEqual(raw_after.pop('invitationRequestedAt'), result['response']['createdAt'])
                self.assertEqual(raw_after, raw_before)
                expected[person['id']] = (command, result['response'])
            self.assertEqual(before, self.public_snapshot(db))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 0)
        with self.connect() as db:
            for cid, (command, response) in expected.items():
                self.assertEqual(self.person(db, cid)['reviewResponse'], response)
                self.assertEqual(intake.review_respond(db, 'a', self.va, command)['response'], response)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM em_store_commands WHERE kind='intake.invite'").fetchone()[0], 4)
            self.assertEqual(before, self.public_snapshot(db))

    def test_response_is_once_only_but_invited_profile_still_accepts_comments(self):
        with self.connect() as db:
            person = self.imported(db); command = self.response(person)
            first = intake.review_respond(db, 'a', self.va, command)
            current = self.person(db, person['id'])
            for command2 in (self.response(current, requestId='second-invite'),
                             self.response(current, decision='reject', requestId='second-reject')):
                self.assert_problem(409, intake.review_respond, db, 'a', self.va, command2)
            # The old direct rejection route shares the same once-only guard.
            self.assert_problem(409, intake.review_reject, db, 'a', self.va, self.rejection(current))
            # Reusing a command ID for another intent never changes its meaning.
            self.assert_problem(409, intake.review_respond, db, 'a', self.va,
                                self.response(person, decision='reject'))
            intake.screening_comment(db, 'a', self.va, self.comment(current))
            retry = intake.review_respond(db, 'a', self.va, command)
            self.assertEqual(retry['response'], first['response'])
            self.assertEqual(len(retry['candidates'][0]['screeningComments']), 1)
            self.assertFalse(retry['candidates'][0]['archived'])

    def test_reject_response_and_legacy_retry_share_history_and_survive_reload(self):
        with self.connect() as db:
            person = self.imported(db)
            command = self.response(person, decision='reject', rejectionAnswers={
                'reason': '  Не хватает опыта  ', 'missing': 'Работы с enterprise', 'improve': 'Больше B2B'})
            result = intake.review_respond(db, 'a', self.va, command)
            self.assertEqual(result['response']['decision'], 'reject')
            self.assertEqual(result['reviewRejection']['reason'], 'Не хватает опыта')
            self.assertEqual(result['reviewRejection']['missing'], 'Работы с enterprise')
            self.assertEqual(result['reviewRejection']['improve'], 'Больше B2B')
            self.assertTrue(result['candidates'][0]['archived'])
        with self.connect() as db:
            self.assertEqual(intake.review_respond(db, 'a', self.va, command), result)
            legacy = {key: value for key, value in command.items() if key != 'decision'}
            self.assertEqual(intake.review_reject(db, 'a', self.va, legacy)['candidates'], result['candidates'])
            self.assert_problem(409, intake.review_respond, db, 'a', self.va,
                                {**command, 'rejectionAnswers': {'reason': 'Другая причина'}})
            intake.archive(db, 'a', self.va, {'id': person['id'], 'archived': False})
            restored = self.person(db, person['id'])
            self.assert_problem(409, intake.review_respond, db, 'a', self.va,
                                self.response(restored, requestId='after-restore'))
            self.assertEqual(restored['reviewResponse'], result['response'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM em_intake_review_events WHERE kind='rejection'").fetchone()[0], 1)

    def test_scope_guards_and_invalid_commands_leave_no_responses(self):
        with self.connect() as db:
            person = self.imported(db); command = self.response(person)
            for wid, vid, body, status in (
                ('b', self.va, command, 404),
                ('b', self.vb, {**command, 'vacancyId': self.vb}, 404),
                ('a', self.va2, {**command, 'vacancyId': self.va2}, 404),
                ('a', self.va, {**command, 'vacancyId': self.va2}, 409),
            ):
                self.assert_problem(status, intake.review_respond, db, wid, vid, body)
            for changes in ({'decision': 'accepted'}, {'decision': None}, {'expectedRevision': True},
                            {'expectedRevision': -1}, {'requestId': ''}, {'requestId': 'bad\x00id'},
                            {'source': 'application'}, {'generation': 'forged'}, {'workspaceId': 'b'},
                            {'start': '2030-01-01T13:00:00Z'}, {'rejectionAnswers': {'reason': 'Unused'}}):
                self.assert_problem(400, intake.review_respond, db, 'a', self.va, {**command, **changes})
            for answers in ({}, {'reason': ''}, {'reason': 'x' * 4001}, {'reason': 'a\0b'},
                            {'reason': 'Valid', 'missing': None}, {'reason': 'Valid', 'improve': 'a\x7fb'}):
                self.assert_problem(400, intake.review_respond, db, 'a', self.va,
                                    self.response(person, decision='reject', rejectionAnswers=answers))
            self.assertIsNone(self.person(db, person['id'])['reviewResponse'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_store_commands').fetchone()[0], 0)

    def test_stale_source_archived_and_closed_vacancy_block_new_response(self):
        with self.connect() as db:
            person = self.imported(db); stale = self.response(person)
            person['preparedProfile']['recordings'][0]['publicUrl'] = 'https://disk.yandex.ru/i/replaced'
            self.save(db, person)
            self.assert_problem(409, intake.review_respond, db, 'a', self.va, stale)
            current = self.person(db, person['id'])
            intake.archive(db, 'a', self.va, {'id': person['id'], 'archived': True})
            archived = self.person(db, person['id'])
            self.assert_problem(409, intake.review_respond, db, 'a', self.va, self.response(archived))
            intake.archive(db, 'a', self.va, {'id': person['id'], 'archived': False})
            current = self.person(db, person['id'])
            command = self.response(current)
            intake.review_respond(db, 'a', self.va, command)
            other = self.imported(db, batch='unanswered-other-123')
            vacancy = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (self.va,)).fetchone()[0])
            vacancy['status'] = 'closed'
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps(vacancy), self.va))
            self.assert_problem(409, intake.review_respond, db, 'a', self.va,
                                self.response(other, requestId='closed-invite'))
            self.assertEqual(intake.review_respond(db, 'a', self.va, command)['response']['decision'], 'invite')

    def test_invite_transaction_failure_rolls_back_timestamp_and_ledger(self):
        with self.connect() as db:
            person = self.imported(db); command = self.response(person)
            db.execute('''CREATE TRIGGER fail_invite BEFORE INSERT ON em_store_commands
              WHEN NEW.kind='intake.invite' BEGIN SELECT RAISE(ABORT,'synthetic'); END''')
            with self.assertRaises(sqlite3.IntegrityError):
                intake.review_respond(db, 'a', self.va, command)
            unchanged = self.person(db, person['id'])
            self.assertIsNone(unchanged['reviewResponse'])
            self.assertNotIn('invitationRequestedAt', unchanged)
            self.assertEqual(unchanged['reviewRevision'], person['reviewRevision'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_store_commands').fetchone()[0], 0)
            db.execute('DROP TRIGGER fail_invite')
            self.assertEqual(intake.review_respond(db, 'a', self.va, command)['response']['decision'], 'invite')

    def test_concurrent_invite_and_reject_save_exactly_one_answer(self):
        with self.connect() as db:
            person = self.imported(db)
        def attempt(decision):
            with self.connect() as db:
                try:
                    return intake.review_respond(db, 'a', self.va,
                        self.response(person, decision=decision, requestId='race-' + decision))['response']['decision']
                except Problem as error:
                    return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ('invite', 'reject')))
        self.assertEqual(results.count(409), 1)
        self.assertEqual(sum(result in ('invite', 'reject') for result in results), 1)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM em_store_commands WHERE kind IN ('intake.invite','intake.reject')").fetchone()[0], 1)
            self.assertEqual(self.person(db, person['id'])['reviewResponse']['decision'], next(r for r in results if r != 409))

    def test_get_is_read_only_and_invite_does_not_require_a_recording_or_candidate_account(self):
        with self.connect() as db:
            person = self.imported(db, recordings=[])
            before = db.total_changes
            for _ in range(3):
                self.assertIsNone(self.person(db, person['id'])['reviewResponse'])
            self.assertEqual(db.total_changes, before)
            intake.review_respond(db, 'a', self.va, self.response(person))
            before = db.total_changes
            self.assertEqual(self.person(db, person['id'])['reviewResponse']['decision'], 'invite')
            self.assertEqual(db.total_changes, before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0], 0)

    def test_archived_answer_stays_readable_and_is_distinct_from_an_unrelated_archive(self):
        with self.connect() as db:
            rejected = self.imported(db, batch='rejected-history-123')
            hidden = self.imported(db, batch='unrelated-archive-123')
            foreign = self.imported(db, wid='b', vid=self.vb, batch='foreign-response-123')
            result = intake.review_respond(db, 'a', self.va, self.response(rejected, decision='reject'))
            intake.archive(db, 'a', self.va, {'id': hidden['id'], 'archived': True})
            intake.review_respond(db, 'b', self.vb, self.response(foreign, vacancyId=self.vb))
        with self.connect() as db:
            before = db.total_changes
            people = {person['id']: person for person in intake.list_candidates(db, 'a', self.va)['candidates']}
            self.assertEqual(set(people), {rejected['id'], hidden['id']})
            self.assertTrue(people[rejected['id']]['archived'])
            self.assertEqual(people[rejected['id']]['reviewResponse'], result['response'])
            self.assertEqual(people[rejected['id']]['reviewRejection'], result['reviewRejection'])
            self.assertTrue(people[hidden['id']]['archived'])
            self.assertIsNone(people[hidden['id']]['reviewResponse'])
            self.assertIsNone(people[hidden['id']]['reviewRejection'])
            self.assertEqual(db.total_changes, before)

    def test_dated_private_invite_survives_reload_without_any_candidate_meeting_or_notification(self):
        start = now().replace(hour=13, minute=30, second=0, microsecond=0) + dt.timedelta(days=1)
        with self.connect() as db:
            person = self.imported(db)
            before = self.public_snapshot(db)
            command = self.response(person, meetingStart=start.astimezone(dt.timezone(dt.timedelta(hours=3))).isoformat())
            result = intake.review_respond(db, 'a', self.va, command)
            response = result['response']
            self.assertEqual(response['meetingStart'], iso(start))
            self.assertEqual(response['meetingStatus'], 'draft')
            self.assertNotEqual(result['candidates'][0]['reviewRevision'], person['reviewRevision'])
            self.assertEqual(before, self.public_snapshot(db))
            self.assertEqual(result['candidates'][0]['preparedProfile'], person['preparedProfile'])
        with self.connect() as db:
            current = self.person(db, person['id'])
            self.assertEqual(current['reviewResponse'], response)
            self.assertEqual(current['invitationMeeting']['start'], iso(start))
            before_changes = db.total_changes
            self.person(db, person['id'])
            self.assertEqual(db.total_changes, before_changes)
            with patch.object(employer_workflow, 'now', return_value=start + dt.timedelta(days=1)):
                self.assertEqual(intake.review_respond(db, 'a', self.va, command)['response'], response)
            self.assertEqual(before, self.public_snapshot(db))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM em_store_commands WHERE kind='intake.invite'").fetchone()[0], 1)
            stored = json.loads(db.execute("SELECT result FROM em_store_commands WHERE kind='intake.invite'").fetchone()[0])
            self.assertEqual(stored['meetingStart'], iso(start))

    def test_existing_private_invite_accepts_one_date_using_fresh_revision(self):
        start = iso(now().replace(hour=13, minute=0, second=0, microsecond=0) + dt.timedelta(days=1))
        with self.connect() as db:
            person = self.imported(db)
            old_command = self.response(person)
            first = intake.review_respond(db, 'a', self.va, old_command)
            person = self.person(db, person['id'])
            stale = self.response(person, meetingStart=start, requestId='add-date')
            intake.screening_comment(db, 'a', self.va, self.comment(person))
            self.assert_problem(409, intake.review_respond, db, 'a', self.va, stale)
            person = self.person(db, person['id'])
            result = intake.review_respond(db, 'a', self.va, {**stale, 'expectedRevision': person['reviewRevision']})
            self.assertEqual(result['response']['createdAt'], first['response']['createdAt'])
            self.assertEqual(len(result['candidates'][0]['screeningComments']), 1)
            current = self.person(db, person['id'])
            self.assert_problem(409, intake.review_respond, db, 'a', self.va,
                                self.response(current, meetingStart=start, requestId='second-date'))
            self.assertEqual(intake.review_respond(db, 'a', self.va, old_command)['response'], result['response'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM em_store_commands WHERE kind='intake.invite'").fetchone()[0], 2)

    def test_private_time_validation_and_failed_write_leave_no_partial_date(self):
        start = now().replace(hour=13, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
        with self.connect() as db:
            person = self.imported(db)
            command = self.response(person, meetingStart=iso(start))
            for value in (None, 'invalid', start.replace(tzinfo=None).isoformat(),
                          iso(start + dt.timedelta(minutes=15)), iso(start + dt.timedelta(seconds=1)),
                          iso(now() - dt.timedelta(days=1)), iso(start + dt.timedelta(days=181))):
                with self.subTest(value=value):
                    self.assert_problem(400, intake.review_respond, db, 'a', self.va, {**command, 'meetingStart': value})
            self.assert_problem(400, intake.review_respond, db, 'a', self.va,
                                self.response(person, decision='reject', meetingStart=iso(start)))
            db.execute('''CREATE TRIGGER fail_dated_invite BEFORE INSERT ON em_store_commands
              WHEN NEW.kind='intake.invite' BEGIN SELECT RAISE(ABORT,'synthetic'); END''')
            with self.assertRaises(sqlite3.IntegrityError):
                intake.review_respond(db, 'a', self.va, command)
            current = self.person(db, person['id'])
            self.assertIsNone(current['reviewResponse'])
            self.assertNotIn('invitationRequestedAt', current)
            self.assertNotIn('invitationMeeting', current)
            self.assertEqual(current['reviewRevision'], person['reviewRevision'])

    def test_parallel_private_date_choices_keep_one_immutable_proposal(self):
        start = now().replace(hour=13, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
        with self.connect() as db:
            person = self.imported(db)
        def attempt(minutes):
            command = self.response(person, meetingStart=iso(start + dt.timedelta(minutes=minutes)), requestId='date-' + str(minutes))
            with self.connect() as db:
                try:
                    return intake.review_respond(db, 'a', self.va, command)['response']['meetingStart']
                except Problem as error:
                    return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, (0, 30)))
        self.assertEqual(results.count(409), 1)
        with self.connect() as db:
            current = self.person(db, person['id'])
            self.assertEqual(current['reviewResponse']['meetingStart'], next(value for value in results if value != 409))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM em_store_commands WHERE kind='intake.invite'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
