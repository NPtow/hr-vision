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

    def joined_zoom(self, *, state='started'):
        """A persisted issued Zoom room before the host's joined receipt arrives."""
        _, _, _, meeting, body = self.joined()
        room = {'provider': 'zoom', 'id': '12345678901', 'name': '12345678901',
                'meetingNumber': '12345678901', 'meetingSessionId': 'create-session',
                'connectionId': 'zoom'}
        raw = json.dumps(room, ensure_ascii=False, separators=(',', ':'))
        # Production reservations use json.dumps; portal rows use compact dump.
        reservation_raw = json.dumps(room)
        self.assertNotEqual(raw, reservation_raw)
        with self.connect() as db:
            db.execute('UPDATE cp_meetings SET room=? WHERE id=?', (raw, meeting['id']))
            db.execute("UPDATE cp_call_provider_choices SET provider='zoom',room=? WHERE meeting_id=?",
                       (reservation_raw, meeting['id']))
        class Zoom:
            configured = True
            selection_enabled = False
            finished = []
            def get_state(self, saved):
                return {'status': state, 'providerSessionId': 'actual-session'}
            def finish(self, saved):
                if not saved.get('sessionPinned') or saved['meetingSessionId'] != 'actual-session':
                    raise Problem('Wrong session', 409)
                self.finished.append(dict(saved))
        self.portal.calls.zoom = Zoom()
        self.change('cancel_meeting', body)
        return meeting, room

    def test_cancelled_zoom_pins_proved_actual_session_before_finish(self):
        for state in ('started', 'ended'):
            with self.subTest(state=state):
                meeting, original = self.joined_zoom(state=state)
                zoom = self.portal.calls.zoom
                original_get = zoom.get_state
                def get_state(saved):
                    # Read-only provider I/O must not retain a SQLite writer.
                    with self.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        db.execute('UPDATE cp_call_cleanup SET next_attempt=0')
                    return original_get(saved)
                with patch.object(zoom, 'get_state', side_effect=get_state):
                    call_cleanup.process_pending(self.portal)
                self.assertEqual(len(zoom.finished), 1)
                self.assertEqual(zoom.finished[0]['meetingSessionId'], 'actual-session')
                self.assertTrue(zoom.finished[0]['sessionPinned'])
                with self.connect() as db:
                    current, job, choice = [db.execute(sql, (meeting['id'],)).fetchone()[0] for sql in (
                        'SELECT room FROM cp_meetings WHERE id=?',
                        'SELECT room FROM cp_call_cleanup WHERE meeting_id=?',
                        'SELECT room FROM cp_call_provider_choices WHERE meeting_id=?')]
                self.assertEqual(current, job)
                self.assertEqual(current, choice)
                self.assertIsNotNone(next(row[2] for row in self.jobs() if row[0] == meeting['id']))

    def test_cancelled_zoom_does_not_pin_waiting_or_unknown_session(self):
        for state in ('waiting', 'unknown'):
            with self.subTest(state=state):
                meeting, original = self.joined_zoom(state=state)
                call_cleanup.process_pending(self.portal)
                self.assertFalse(self.portal.calls.zoom.finished)
                with self.connect() as db:
                    current = json.loads(db.execute('SELECT room FROM cp_meetings WHERE id=?',
                                                    (meeting['id'],)).fetchone()[0])
                self.assertEqual(current, original)
                self.assertIsNone(next(row[2] for row in self.jobs() if row[0] == meeting['id']))

    def test_cancelled_zoom_provider_read_cannot_rebind_replaced_room(self):
        meeting, original = self.joined_zoom()
        zoom = self.portal.calls.zoom
        def changed_during_read(saved):
            with self.connect() as db:
                db.execute('UPDATE cp_meetings SET room=? WHERE id=?',
                           (json.dumps({**original, 'meetingSessionId': 'replacement'}), meeting['id']))
            return {'status': 'started', 'providerSessionId': 'actual-session'}
        with patch.object(zoom, 'get_state', side_effect=changed_during_read):
            call_cleanup.process_pending(self.portal)
        self.assertFalse(zoom.finished)
        with self.connect() as db:
            saved_job = json.loads(db.execute('SELECT room FROM cp_call_cleanup WHERE meeting_id=?',
                                             (meeting['id'],)).fetchone()[0])
        self.assertEqual(saved_job, original)
        self.assertIsNone(self.jobs()[0][2])

    def test_cancelled_zoom_refuses_changed_choice_with_same_meeting_number(self):
        meeting, original = self.joined_zoom()
        with self.connect() as db:
            db.execute('UPDATE cp_call_provider_choices SET room=? WHERE meeting_id=?',
                       (json.dumps({**original, 'meetingSessionId': 'different-session'}), meeting['id']))
        call_cleanup.process_pending(self.portal)
        self.assertFalse(self.portal.calls.zoom.finished)
        with self.connect() as db:
            current = json.loads(db.execute('SELECT room FROM cp_meetings WHERE id=?',
                                            (meeting['id'],)).fetchone()[0])
            saved_job = json.loads(db.execute('SELECT room FROM cp_call_cleanup WHERE meeting_id=?',
                                             (meeting['id'],)).fetchone()[0])
        self.assertEqual(current, original)
        self.assertEqual(saved_job, original)
        self.assertIsNone(self.jobs()[0][2])

    def test_cancelled_zoom_pin_survives_failed_finish_and_restart(self):
        meeting, _ = self.joined_zoom()
        zoom = self.portal.calls.zoom
        with patch.object(zoom, 'finish', side_effect=Problem('unavailable', 502)):
            call_cleanup.process_pending(self.portal)
        self.assertIsNone(self.jobs()[0][2])
        self.portal = CandidatePortal(self.connect, threading.RLock(), self.media,
                                      'https://portal.example', FakeNotifications, zoom=zoom)
        with self.connect() as db:
            db.execute('UPDATE cp_call_cleanup SET next_attempt=0')
        with patch.object(zoom, 'get_state', side_effect=AssertionError('Already pinned')):
            call_cleanup.process_pending(self.portal)
        self.assertEqual(len(zoom.finished), 1)
        self.assertEqual(zoom.finished[0]['meetingSessionId'], 'actual-session')
        self.assertIsNotNone(self.jobs()[0][2])

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
