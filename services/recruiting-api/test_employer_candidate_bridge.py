"""Real SQLite application/meeting boundaries; external media stays synthetic."""
import concurrent.futures
import datetime as dt
import json
import sqlite3
import threading
import unittest
from unittest.mock import patch

from candidate_portal import CandidatePortal, migrate_meetings
from domain import Problem, iso, require
import employer_workflow
import test_candidate_portal as fixture
import test_candidate_media as media_fixture


class EmployerCandidateBridgeTest(unittest.TestCase):
    setUp = fixture.CandidatePortalTest.setUp
    tearDown = fixture.CandidatePortalTest.tearDown
    connect = fixture.CandidatePortalTest.connect
    payload = fixture.CandidatePortalTest.payload
    call = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)
    register = fixture.CandidatePortalTest.register
    slot = fixture.CandidatePortalTest.slot
    completed_person = fixture.CandidatePortalTest.completed_person
    vacancy = fixture.CandidatePortalTest.vacancy
    propose = fixture.CandidatePortalTest.propose
    assert_problem = fixture.CandidatePortalTest.assert_problem

    def application(self):
        person, cookie = self.completed_person()
        application = self.propose(person, self.vacancy())
        self.call('candidate/application/decision', {'applicationId': application['id'], 'decision': 'accepted'}, cookie)
        return person, cookie, application

    def change(self, action, body, manager='workspace:one'):
        with self.portal.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return getattr(self.portal, action)(db, body, manager_id=manager)

    def scheduled(self, application, minutes=60, manager='workspace:one'):
        return self.change('propose_meeting', {'applicationId': application['id'],
            'start': iso(self.tick + dt.timedelta(minutes=minutes))}, manager)

    @staticmethod
    def exact(application, meeting):
        return {'applicationId': application['id'], 'meetingId': meeting['id'], 'revision': meeting['revision']}

    def test_acceptance_never_confirms_and_reschedule_retains_old_id(self):
        person, cookie, application = self.application()
        first = self.scheduled(application)
        self.assertEqual(first['status'], 'pending')
        self.call('candidate/meeting/confirm', self.exact(application, first), cookie)
        second = self.change('reschedule_meeting', {**self.exact(application, first),
            'start': iso(self.tick + dt.timedelta(minutes=100))})
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(second['revision'], first['revision'] + 1)
        self.assertEqual(second['status'], 'pending')
        self.assert_problem(409, 'candidate/meeting/confirm', self.exact(application, first), cookie)
        self.assert_problem(409, 'candidate/meeting/confirm', {**self.exact(application, second), 'revision': first['revision']}, cookie)
        state, _ = self.call('candidate/meeting/confirm', self.exact(application, second), cookie)
        self.assertEqual(state['applications'][0]['meeting']['id'], second['id'])
        self.assertEqual([m['status'] for m in state['applications'][0]['meetings']], ['confirmed', 'cancelled'])
        self.assertTrue(all('room' not in m for m in state['applications'][0]['meetings']))
        with self.connect() as db:
            self.assertEqual(self.portal.interview(db, person['id'])['status'], 'completed')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meetings').fetchone()[0], 2)
        restarted = CandidatePortal(self.connect, threading.RLock(), self.media, 'https://portal.example', fixture.FakeNotifications)
        with self.connect() as db:
            self.assertEqual(restarted.application(db, application['id'])['meeting']['id'], second['id'])

    def test_conflicts_scope_manager_but_preserve_candidate_global_calendar(self):
        person, cookie, application = self.application()
        self.scheduled(application)
        _, _, second = self.application()
        with self.assertRaises(Problem) as caught:
            self.scheduled(second)
        self.assertEqual(caught.exception.status, 409)
        self.scheduled(second, manager='workspace:two')
        third = self.propose(person, self.vacancy())
        self.call('candidate/application/decision', {'applicationId': third['id'], 'decision': 'accepted'}, cookie)
        with self.assertRaises(Problem) as caught:
            self.scheduled(third, manager='workspace:three')
        self.assertEqual(caught.exception.status, 409)

    def test_cancel_decline_release_time_without_deleting_application(self):
        person, cookie, application = self.application()
        first = self.scheduled(application)
        _, other_cookie = self.register()
        self.assert_problem(404, 'candidate/meeting/decline', self.exact(application, first), other_cookie)
        declined, _ = self.call('candidate/meeting/decline', self.exact(application, first), cookie)
        self.assertIsNone(declined['applications'][0]['meeting'])
        self.assertEqual(declined['applications'][0]['meetings'][0]['status'], 'declined')
        self.call('candidate/meeting/decline', self.exact(application, first), cookie)
        self.assert_problem(409, 'candidate/meeting/confirm', self.exact(application, first), cookie)
        second = self.scheduled(application)
        self.change('cancel_meeting', self.exact(application, second))
        self.change('cancel_meeting', self.exact(application, second))
        third = self.scheduled(application)
        self.assertEqual(third['revision'], 3)
        with self.connect() as db:
            self.assertEqual(self.portal.application(db, application['id'])['status'], 'accepted')
            self.assertEqual(db.execute('SELECT status FROM cp_meetings ORDER BY revision').fetchall(),
                             [('declined',), ('cancelled',), ('pending',)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_recruiter_slots WHERE candidate_id=?', (person['id'],)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fake_events WHERE type=?', ('meeting.declined',)).fetchone()[0], 1)

    def test_reschedule_atomic_and_wrong_manager_denied(self):
        _, _, application = self.application()
        first = self.scheduled(application)
        _, _, second = self.application()
        self.scheduled(second, minutes=120)
        with self.assertRaises(Problem):
            self.change('reschedule_meeting', {**self.exact(application, first),
                'start': iso(self.tick + dt.timedelta(minutes=120))})
        with self.assertRaises(Problem) as caught:
            self.change('cancel_meeting', self.exact(application, first), manager='workspace:two')
        self.assertEqual(caught.exception.status, 404)
        with self.connect() as db:
            self.assertEqual(self.portal.meeting(db, application['id'])['id'], first['id'])
            self.assertEqual(self.portal.meeting(db, application['id'])['status'], 'pending')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meetings WHERE application_id=?', (application['id'],)).fetchone()[0], 1)

    def test_candidate_after_no_blocks_new_meeting_without_resetting_history(self):
        person, cookie, application = self.application()
        first = self.scheduled(application)
        with self.connect() as db:
            db.execute("UPDATE cp_meetings SET status='completed',completed_at=? WHERE id=?", (iso(self.tick), first['id']))
        self.call('candidate/workflow', {'applicationId': application['id'], 'action': 'after',
            'meetingId': first['id'], 'value': 'no'}, cookie)
        with self.connect() as db:
            before_meetings = db.execute('SELECT * FROM cp_meetings WHERE application_id=?', (application['id'],)).fetchall()
            before_workflow = employer_workflow.load(db, application['id'], actor='candidate', candidate_id=person['id'])
            before_events = db.execute('SELECT * FROM fake_events').fetchall()
        with self.assertRaises(Problem) as caught:
            self.scheduled(application, minutes=120)
        self.assertEqual(caught.exception.status, 409)
        self.assertIn('Кандидат отказался', caught.exception.message)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT * FROM cp_meetings WHERE application_id=?', (application['id'],)).fetchall(), before_meetings)
            self.assertEqual(employer_workflow.load(db, application['id'], actor='candidate', candidate_id=person['id']), before_workflow)
            self.assertEqual(db.execute('SELECT * FROM fake_events').fetchall(), before_events)
            self.assertEqual(self.portal.candidate_state(db, person['id'])['applications'][0]['afterInterest'], 'no')
        # Only the candidate's explicit changed decision permits a new proposal.
        self.call('candidate/workflow', {'applicationId': application['id'], 'action': 'after',
            'meetingId': first['id'], 'value': 'yes'}, cookie)
        second = self.scheduled(application, minutes=120)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(second['status'], 'pending')

    def test_withdrawn_consent_blocks_proposal_and_confirmation(self):
        _, cookie, application = self.application()
        first = self.scheduled(application)
        self.call('candidate/profile', {'consentReuse': False, 'autoApprove': False}, cookie)
        self.assert_problem(409, 'candidate/meeting/confirm', self.exact(application, first), cookie)
        self.change('cancel_meeting', self.exact(application, first))
        with self.assertRaises(Problem) as caught:
            self.scheduled(application)
        self.assertEqual(caught.exception.status, 409)

    def test_concurrent_proposals_cannot_double_book(self):
        _, _, first = self.application()
        _, _, second = self.application()
        barrier = threading.Barrier(2)
        def propose(application):
            barrier.wait()
            try:
                self.scheduled(application)
                return 200
            except Problem as problem:
                return problem.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            calls = [pool.submit(propose, app) for app in (first, second)]
            self.assertEqual(sorted(call.result() for call in calls), [200, 409])

    def test_historical_materials_keep_identity_after_next_meeting(self):
        self.media = media_fixture.MaterialMedia()
        self.portal.media = self.media
        _, cookie, application = self.application()
        first = self.scheduled(application, minutes=5)
        body = self.exact(application, first)
        self.call('candidate/meeting/confirm', body, cookie)
        self.call('recruiter/meeting/join', body, manager=True)
        self.call('recruiter/meeting/complete', body, manager=True)
        with self.connect() as db:
            old = self.portal.meeting(db, application['id'], first['id'])
        self.media.ready(old['room'])
        second = self.scheduled(application, minutes=90)
        materials, _ = self.call('candidate/meeting/materials', body, cookie)
        self.assertEqual(materials['video']['status'], 'ready')
        self.assertEqual(materials['recordings'][0]['id'], old['room']['id'] + '-video')
        self.assert_problem(409, 'candidate/meeting/join', {**body, 'consent': True}, cookie)
        with self.connect() as db:
            self.assertEqual(self.portal.meeting(db, application['id'])['id'], second['id'])
            self.assertIsNotNone(db.execute('SELECT body FROM cp_media_state WHERE meeting_id=?', (first['id'],)).fetchone())

    def test_notification_failure_rolls_back_meeting(self):
        _, _, application = self.application()
        with patch.object(self.portal, 'event', side_effect=RuntimeError('isolated outbox failure')):
            with self.assertRaises(RuntimeError):
                self.scheduled(application)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meetings').fetchone()[0], 0)

    def test_cancel_during_provider_join_does_not_resurrect_meeting(self):
        _, cookie, application = self.application()
        meeting = self.scheduled(application, minutes=5)
        body = self.exact(application, meeting)
        self.call('candidate/meeting/confirm', body, cookie)
        entered, proceed = threading.Event(), threading.Event()
        original = self.media.request
        def slow(path, body=None):
            if path == '/meeting-tokens':
                entered.set()
                self.assertTrue(proceed.wait(3))
            return original(path, body)
        def join():
            try:
                self.call('candidate/meeting/join', {**body, 'consent': True}, cookie)
                return 200
            except Problem as problem:
                return problem.status
        with patch.object(self.media, 'request', side_effect=slow):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(join)
                self.assertTrue(entered.wait(3))
                self.change('cancel_meeting', body)
                proceed.set()
                self.assertEqual(pending.result(timeout=3), 409)
        with self.connect() as db:
            current = self.portal.meeting(db, application['id'])
            self.assertEqual(current['status'], 'cancelled')
            self.assertIsNone(current['room'])
        self.assertEqual(self.portal._media_locks, {})

    def test_employer_owner_identity_keeps_existing_transcript_filter(self):
        person, cookie, application = self.application()
        meeting = self.scheduled(application, minutes=5)
        self.call('candidate/meeting/confirm', self.exact(application, meeting), cookie)
        self.portal.join(person['id'], application['id'], True, owner=True,
                         meeting_id=meeting['id'], owner_name='Нанимающий', owner_id='manager')
        self.assertEqual(self.media.tokens[-1]['user_id'], 'manager')
        self.assertEqual(self.media.tokens[-1]['user_name'], 'Нанимающий')

    def test_revoked_manager_access_during_finish_does_not_commit_completed(self):
        person, cookie, application = self.application()
        meeting = self.scheduled(application, minutes=5)
        self.call('candidate/meeting/confirm', self.exact(application, meeting), cookie)
        self.portal.join(person['id'], application['id'], True, owner=True, meeting_id=meeting['id'])
        permitted = True
        original = self.media.finish
        def access(db):
            require(permitted, 'Session expired', 401)
        def finish(room):
            nonlocal permitted
            original(room)
            permitted = False
        with patch.object(self.media, 'finish', side_effect=finish):
            with self.assertRaises(Problem) as caught:
                self.portal.complete(person['id'], application['id'], meeting['id'], access_check=access)
        self.assertEqual(caught.exception.status, 401)
        with self.connect() as db:
            self.assertEqual(self.portal.meeting(db, application['id'])['status'], 'live')
        permitted = True
        self.portal.complete(person['id'], application['id'], meeting['id'], access_check=access)
        with self.connect() as db:
            self.assertEqual(self.portal.meeting(db, application['id'])['status'], 'completed')

    def test_revoked_candidate_session_during_join_cannot_receive_room(self):
        person, cookie, application = self.application()
        meeting = self.scheduled(application, minutes=5)
        body = self.exact(application, meeting)
        self.call('candidate/meeting/confirm', body, cookie)
        original = self.media.request
        def request(path, body=None):
            if path == '/meeting-tokens':
                with self.connect() as db:
                    db.execute('DELETE FROM cp_sessions WHERE candidate_id=?', (person['id'],))
            return original(path, body)
        with patch.object(self.media, 'request', side_effect=request):
            self.assert_problem(401, 'candidate/meeting/join', {**body, 'consent': True}, cookie)
        with self.connect() as db:
            self.assertIsNone(self.portal.meeting(db, application['id'])['room'])

    def test_workflow_event_failure_rolls_back_chat_and_request_receipt(self):
        _, cookie, application = self.application()
        with patch.object(self.portal, 'workflow_events', side_effect=RuntimeError('isolated durable event failure')):
            with self.assertRaises(RuntimeError):
                self.call('candidate/workflow', {'applicationId': application['id'], 'action': 'chat',
                    'text': 'Hello', 'requestId': 'isolated-idempotent-chat'}, cookie)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_workflow_messages').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_workflow_commands').fetchone()[0], 0)

    def manager_workflow(self, application, action, body):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            result = employer_workflow.manager_action(db, application['id'], action, body, manager_id='workspace:one')
            self.portal.workflow_events(db, result['events'])
            return result['state']

    def test_candidate_workflow_has_same_identity_and_versioned_offer(self):
        person, cookie, application = self.application()
        meeting = self.scheduled(application, minutes=5)
        body = self.exact(application, meeting)
        self.call('candidate/meeting/confirm', body, cookie)
        self.call('recruiter/meeting/join', body, manager=True)
        self.call('recruiter/meeting/complete', body, manager=True)
        questions = self.manager_workflow(application, 'feedback.questions', {
            'meetingId': meeting['id'], 'questions': 'Как вы возвращали потерянных клиентов?'})['meeting']['questionnaire']
        self.manager_workflow(application, 'feedback', {'meetingId': meeting['id'], 'questionnaireId': questions['id'],
            'answers': {item['id']: {'rating': 'clear', 'comment': 'Private manager assessment'} for item in questions['questions']}})
        self.manager_workflow(application, 'decision', {'value': 'pool'})
        draft = self.manager_workflow(application, 'offer.save', {'role': 'Менеджер', 'compensation': '180 000 ₽',
            'format': 'Гибрид', 'expectations': 'Развивать клиентов', 'startDate': (self.tick + dt.timedelta(days=7)).date().isoformat()})['offer']
        before = self.call('candidate/state', cookie=cookie)[0]['applications'][0]
        self.assertIsNone(before['offer'])
        self.assertNotIn('Private manager assessment', json.dumps(before))
        self.manager_workflow(application, 'offer.send', {'version': draft['version']})
        _, other_cookie = self.register()
        reply = {'applicationId': application['id'], 'action': 'offer.reply', 'version': draft['version'], 'value': 'accepted'}
        self.assert_problem(404, 'candidate/workflow', reply, other_cookie)
        self.assert_problem(403, 'candidate/workflow', {**reply, 'action': 'decision', 'value': 'pool', 'actor': 'manager'}, cookie)
        self.assert_problem(409, 'candidate/workflow', {**reply, 'version': draft['version'] + 1}, cookie)
        result, _ = self.call('candidate/workflow', reply, cookie)
        self.call('candidate/workflow', reply, cookie)
        self.assertEqual(result['id'], person['id'])
        self.assertEqual(result['applications'][0]['offer']['status'], 'accepted')
        self.assertEqual(result['applications'][0]['vacancy']['status'], 'closed')
        with self.connect() as db:
            events = [json.loads(row[0]) for row in db.execute('SELECT body FROM cp_workflow_events')]
            accepted = [event for event in events if event['type'] == 'offer.accepted']
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0]['audience'], 'employer')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM fake_events WHERE type='offer.accepted'").fetchone()[0], 0)


class MeetingMigrationTest(unittest.TestCase):
    def old_db(self):
        db = sqlite3.connect(':memory:')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
          CREATE TABLE cp_applications(id TEXT PRIMARY KEY);
          CREATE TABLE cp_meetings(id TEXT PRIMARY KEY,application_id TEXT UNIQUE NOT NULL REFERENCES cp_applications(id),
            start TEXT NOT NULL,end TEXT NOT NULL,status TEXT NOT NULL,room TEXT,confirmed_at TEXT,completed_at TEXT);
          CREATE TABLE external_feedback(meeting_id TEXT PRIMARY KEY REFERENCES cp_meetings(id),body TEXT NOT NULL);
          INSERT INTO cp_applications VALUES ('application');
          INSERT INTO cp_meetings VALUES ('old','application','2026-10-12T13:00:00Z','2026-10-12T13:30:00Z','completed',
            '{"id":"daily-old","name":"daily-old"}','confirmed','completed');
          INSERT INTO external_feedback VALUES ('old','preserved');
        ''')
        return db

    def test_rebuild_preserves_ids_rooms_and_foreign_keys(self):
        db = self.old_db()
        try:
            migrate_meetings(db)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('SELECT meeting_id,body FROM external_feedback').fetchone(), ('old', 'preserved'))
            self.assertEqual(json.loads(db.execute('SELECT room FROM cp_meetings').fetchone()[0])['id'], 'daily-old')
            db.execute("INSERT INTO cp_meetings(id,application_id,start,end,status,revision,created_at) VALUES ('new','application','start','end','pending',2,'created')")
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meetings').fetchone()[0], 2)
            self.assertEqual(db.execute('PRAGMA foreign_key_list(external_feedback)').fetchone()[2], 'cp_meetings')
        finally:
            db.close()

    def test_failed_rebuild_rolls_back_original_table_and_fk_enforcement(self):
        db = self.old_db()
        try:
            def authorizer(action, arg1, arg2, database, trigger):
                return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE else sqlite3.SQLITE_OK
            db.set_authorizer(authorizer)
            with self.assertRaises(sqlite3.DatabaseError):
                migrate_meetings(db)
            db.set_authorizer(None)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertNotIn('manager_id', {row[1] for row in db.execute('PRAGMA table_info(cp_meetings)')})
            self.assertEqual(db.execute('SELECT id FROM cp_meetings').fetchone()[0], 'old')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='cp_meetings_next'").fetchone()[0], 0)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
