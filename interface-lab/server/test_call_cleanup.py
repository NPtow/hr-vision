"""Cancelled provider rooms: real SQLite, synthetic provider, no actual people."""
import datetime as dt
import json
import threading
import unittest
from unittest.mock import patch

import call_cleanup
from candidate_portal import CandidatePortal
from domain import Problem, iso
import test_employer_candidate_bridge as fixture
from test_candidate_portal import FakeNotifications


class CallCleanupTest(unittest.TestCase):
    setUp = fixture.EmployerCandidateBridgeTest.setUp
    tearDown = fixture.EmployerCandidateBridgeTest.tearDown
    connect = fixture.EmployerCandidateBridgeTest.connect
    payload = fixture.EmployerCandidateBridgeTest.payload
    call = fixture.EmployerCandidateBridgeTest.call
    not_manager = staticmethod(fixture.EmployerCandidateBridgeTest.not_manager)
    register = fixture.EmployerCandidateBridgeTest.register
    slot = fixture.EmployerCandidateBridgeTest.slot
    completed_person = fixture.EmployerCandidateBridgeTest.completed_person
    vacancy = fixture.EmployerCandidateBridgeTest.vacancy
    propose = fixture.EmployerCandidateBridgeTest.propose
    application = fixture.EmployerCandidateBridgeTest.application
    change = fixture.EmployerCandidateBridgeTest.change
    scheduled = fixture.EmployerCandidateBridgeTest.scheduled
    exact = staticmethod(fixture.EmployerCandidateBridgeTest.exact)

    def joined(self):
        person, cookie, application = self.application()
        meeting = self.scheduled(application, minutes=5)
        body = self.exact(application, meeting)
        self.call('candidate/meeting/confirm', body, cookie)
        self.call('candidate/meeting/join', {**body, 'consent': True}, cookie)
        self.media.finished.clear()
        return person, cookie, application, meeting, body

    def jobs(self):
        with self.connect() as db:
            return db.execute('SELECT meeting_id,attempts,closed_at FROM cp_call_cleanup').fetchall()

    def test_all_cancellation_paths_close_the_issued_room(self):
        for action in ('cancel', 'reschedule', 'decline', 'search_stopped'):
            with self.subTest(action=action):
                person, cookie, app, meeting, body = self.joined()
                if action == 'cancel':
                    self.change('cancel_meeting', body)
                elif action == 'reschedule':
                    self.change('reschedule_meeting', {**body, 'start': iso(self.tick + dt.timedelta(minutes=45))})
                elif action == 'decline':
                    self.call('candidate/meeting/decline', body, cookie)
                else:
                    self.call('candidate/search', {'searching': False}, cookie)
                call_cleanup.process_pending(self.portal)
                self.assertEqual(self.media.finished, [meeting['id']])
                job = next(row for row in self.jobs() if row[0] == meeting['id'])
                self.assertIsNotNone(job[2])
                call_cleanup.process_pending(self.portal)
                self.assertEqual(self.media.finished, [meeting['id']])
                # Free the synthetic replacement slot before the next subcase.
                if action == 'reschedule':
                    self.call('candidate/search', {'searching': False}, cookie)

    def test_cancel_without_room_never_touches_provider(self):
        _, _, app = self.application()
        meeting = self.scheduled(app)
        self.change('cancel_meeting', self.exact(app, meeting))
        self.media.finished.clear()
        call_cleanup.process_pending(self.portal)
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.media.finished, [])

    def test_provider_failure_is_durable_and_retries_after_restart(self):
        _, _, _, meeting, body = self.joined()
        self.change('cancel_meeting', body)
        with patch.object(self.media, 'finish', side_effect=Problem('unavailable', 502)):
            call_cleanup.process_pending(self.portal)
        self.assertEqual(self.jobs()[0][1:], (1, None))
        # A new portal/process retains the job and its backoff.
        self.portal = CandidatePortal(self.connect, threading.RLock(), self.media,
                                      'https://portal.example', FakeNotifications)
        call_cleanup.process_pending(self.portal)
        self.assertEqual(self.media.finished, [])
        with self.connect() as db:
            db.execute('UPDATE cp_call_cleanup SET next_attempt=0')
        call_cleanup.process_pending(self.portal)
        self.assertEqual(self.media.finished, [meeting['id']])
        self.assertIsNotNone(self.jobs()[0][2])

    def test_cancel_rollback_does_not_close_a_valid_room(self):
        _, _, _, _, body = self.joined()
        with patch.object(self.portal, 'event', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                self.change('cancel_meeting', body)
        call_cleanup.process_pending(self.portal)
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.media.finished, [])

    def test_provider_call_holds_no_database_write_transaction(self):
        _, _, _, _, body = self.joined()
        self.change('cancel_meeting', body)
        original = self.media.finish
        def finish(room):
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE cp_call_cleanup SET next_attempt=0')
            original(room)
        with patch.object(self.media, 'finish', side_effect=finish):
            call_cleanup.process_pending(self.portal)
        self.assertIsNotNone(self.jobs()[0][2])

    def test_replaced_room_or_active_status_is_never_closed_by_stale_job(self):
        _, _, _, meeting, body = self.joined()
        self.change('cancel_meeting', body)
        with self.connect() as db:
            room = json.loads(db.execute('SELECT room FROM cp_meetings WHERE id=?', (meeting['id'],)).fetchone()[0])
            db.execute('UPDATE cp_meetings SET room=? WHERE id=?', (json.dumps({**room, 'id': 'new-room'}), meeting['id']))
        call_cleanup.process_pending(self.portal)
        self.assertEqual(self.media.finished, [])
        self.assertIsNone(self.jobs()[0][2])
        with self.connect() as db:
            db.execute("UPDATE cp_meetings SET status='live',room=? WHERE id=?", (json.dumps(room), meeting['id']))
            db.execute('UPDATE cp_call_cleanup SET next_attempt=0')
        call_cleanup.process_pending(self.portal)
        self.assertEqual(self.media.finished, [])

    def test_startup_backfills_existing_cancelled_room_once(self):
        _, _, _, meeting, body = self.joined()
        self.change('cancel_meeting', body)
        with self.connect() as db:
            db.execute('DELETE FROM cp_call_cleanup')
        self.portal = CandidatePortal(self.connect, threading.RLock(), self.media,
                                      'https://portal.example', FakeNotifications)
        call_cleanup.process_pending(self.portal)
        self.assertEqual(self.media.finished, [meeting['id']])
        with self.connect() as db:
            call_cleanup.init(db)
        call_cleanup.process_pending(self.portal)
        self.assertEqual(self.media.finished, [meeting['id']])
