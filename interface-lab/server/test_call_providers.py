"""Parallel providers: real SQLite/auth routes, no live Zoom or messages."""
import concurrent.futures
import json
import threading
import unittest
from unittest.mock import patch

from candidate_portal import CandidatePortal
from domain import Problem, iso, now
import test_candidate_portal as fixture


class FakeZoom:
    configured = True
    selection_enabled = True

    def __init__(self):
        self.created, self.finished = [], []
        self.failed = False
        self.session = 'first-session'

    def host_for(self, actor):
        return 'host@example.test' if actor == 'recruiter' else None

    def room(self, mid, host, request_id):
        self.created.append((mid, host, request_id))
        if self.failed:
            raise Problem('Creation outcome unknown', 502)
        return {'provider': 'zoom', 'id': '12345678901', 'name': '12345678901',
                'meetingNumber': '12345678901', 'meetingSessionId': self.session,
                'connectionId': 'zoom-test', 'zoomHostId': host, 'password': 'private-pass',
                'url': 'https://zoom.us/j/12345678901'}

    def credentials(self, room, user_name, owner):
        return {'provider': 'zoom', 'meetingNumber': room['id'], 'passWord': room['password'],
                'signature': 'synthetic-sdk-signature', 'userName': user_name,
                'role': int(owner), **({'zak': 'host-only-test'} if owner else {})}

    def get_state(self, room):
        return {'status': 'started', 'providerSessionId': self.session}

    def finish(self, room):
        self.finished.append(room['id'])

    def materials(self, room, known=None):
        return {'recordings': [], 'transcripts': [], 'video': {'status': 'processing', 'ids': []},
                'transcription': {'status': 'unavailable', 'ids': []}, 'checkedAt': iso(now())}


class CallProvidersTest(unittest.TestCase):
    tearDown = fixture.CandidatePortalTest.tearDown
    connect = fixture.CandidatePortalTest.connect
    payload = fixture.CandidatePortalTest.payload
    call = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)
    register = fixture.CandidatePortalTest.register
    slot = fixture.CandidatePortalTest.slot

    def setUp(self):
        fixture.CandidatePortalTest.setUp(self)
        self.zoom = FakeZoom()
        self.portal.calls.zoom = self.zoom
        self.person, self.cookie = self.register()
        slot = self.slot()
        state, _ = self.call('candidate/book', {'slotId': slot['id']}, self.cookie)
        self.mid = state['interview']['id']
        self.identity = {'candidateId': self.person['id'], 'interviewId': self.mid}

    def select(self, provider='zoom'):
        return self.call('recruiter/interview/provider', {**self.identity, 'provider': provider}, manager=True)[0]

    def join(self, owner=False):
        return self.call('recruiter/interview/join' if owner else 'candidate/interview/join',
            {**self.identity, 'consent': True}, self.cookie if not owner else '', manager=owner)[0]

    def test_daily_is_default_and_old_calls_remain_daily(self):
        room = self.join(True)
        self.assertEqual(room['provider'], 'daily')
        self.assertIn('token', room)
        self.assertFalse(self.zoom.created)
        with self.assertRaises(Problem) as error:
            self.select()
        self.assertEqual(error.exception.status, 409)

    def test_zoom_default_candidate_first_creates_one_host_bound_room(self):
        self.portal.calls.default_provider = 'zoom'
        with patch.object(self.zoom, 'host_for', return_value='host@example.test'):
            guest, host = self.join(), self.join(True)
        self.assertEqual((guest['provider'], guest['role'], host['role']), ('zoom', 0, 1))
        self.assertEqual(len(self.zoom.created), 1)
        self.assertFalse(self.media.tokens)

    def test_zoom_default_does_not_change_existing_daily_room_without_choice(self):
        self.join(True)
        with self.connect() as db:
            db.execute('DELETE FROM cp_call_provider_choices WHERE meeting_id=?', (self.mid,))
        self.portal.calls.default_provider = 'zoom'
        self.assertEqual(self.join()['provider'], 'daily')
        self.assertFalse(self.zoom.created)

    def test_zoom_default_unconfigured_fails_without_daily_fallback(self):
        self.portal.calls.default_provider = 'zoom'
        self.zoom.selection_enabled = False
        with self.assertRaises(Problem) as caught:
            self.join()
        self.assertEqual(caught.exception.status, 503)
        self.assertFalse(self.media.tokens)

    def test_explicit_daily_reserve_overrides_zoom_default(self):
        self.portal.calls.default_provider = 'zoom'
        self.select('daily')
        self.assertEqual(self.join()['provider'], 'daily')
        self.assertFalse(self.zoom.created)

    def test_switch_saved_for_both_participants_with_host_secret_separation(self):
        state = self.select()
        meeting = next(p for p in state['candidates'] if p['id'] == self.person['id'])['interview']
        self.assertEqual(meeting['callProvider'], 'zoom')
        self.assertNotIn('private-pass', json.dumps(state))
        guest, host = self.join(), self.join(True)
        self.assertEqual(guest['meetingNumber'], host['meetingNumber'])
        self.assertEqual((guest['role'], host['role']), (0, 1))
        self.assertNotIn('zak', guest)
        self.assertIn('zak', host)
        self.assertEqual(len(self.zoom.created), 1)
        self.assertFalse(self.media.tokens)

    def test_candidate_cannot_select(self):
        with self.assertRaises(Problem):
            self.call('candidate/interview/provider', {'provider': 'zoom'}, self.cookie)
        with self.assertRaises(Problem):
            self.call('recruiter/interview/provider', {**self.identity, 'provider': 'zoom'}, self.cookie)

    def test_disabled_pilot_rejects_new_choice_but_keeps_existing_room(self):
        self.select()
        self.join()
        self.zoom.selection_enabled = False
        self.assertEqual(self.join(True)['provider'], 'zoom')
        self.assertEqual(len(self.zoom.created), 1)

    def test_unmapped_host_cannot_select(self):
        with patch.object(self.zoom, 'host_for', return_value=None):
            with self.assertRaises(Problem) as error:
                self.select()
        self.assertEqual(error.exception.status, 409)

    def test_selection_disabled_does_not_create(self):
        self.zoom.selection_enabled = False
        with self.assertRaises(Problem) as error:
            self.select()
        self.assertEqual(error.exception.status, 503)
        self.assertFalse(self.zoom.created)

    def test_uncertain_create_is_not_retried_or_switched(self):
        self.select()
        self.zoom.failed = True
        with self.assertRaises(Problem):
            self.join()
        self.zoom.failed = False
        for action in (self.join, lambda: self.select('daily')):
            with self.assertRaises(Problem) as error:
                action()
            self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(self.zoom.created), 1)

    def test_simultaneous_join_creates_only_one_room(self):
        self.select()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            a, b = list(pool.map(self.join, (True, False)))
        self.assertEqual(a['meetingNumber'], b['meetingNumber'])
        self.assertEqual(len(self.zoom.created), 1)

    def test_restart_keeps_exact_room(self):
        self.select()
        first = self.join()
        self.portal = CandidatePortal(self.connect, threading.RLock(), self.media,
            'https://portal.example', fixture.FakeNotifications, zoom=self.zoom)
        self.assertEqual(first['meetingNumber'], self.join(True)['meetingNumber'])
        self.assertEqual(len(self.zoom.created), 1)

    def test_zoom_join_is_independent_of_coach(self):
        self.select()
        with patch('candidate_portal.recruiter_coach.pin', side_effect=RuntimeError('script failed')):
            self.assertEqual(self.join(True)['provider'], 'zoom')
            result, _ = self.call('recruiter/interview/connected', self.identity, manager=True)
        self.assertTrue(result['ok'])
        self.assertFalse(result['scriptReady'])

    def test_connected_pins_actual_session_once(self):
        self.select()
        self.join(True)
        self.zoom.session = 'actual-started-session'
        self.call('recruiter/interview/connected', self.identity, manager=True)
        with self.connect() as db:
            room = self.portal.interview(db, self.person['id'])['room']
        self.assertTrue(room['sessionPinned'])
        self.assertEqual(room['meetingSessionId'], self.zoom.session)
        self.zoom.session = 'different-restarted-session'
        with self.assertRaises(Problem) as error:
            self.call('recruiter/interview/connected', self.identity, manager=True)
        self.assertEqual(error.exception.status, 409)

    def test_zoom_consent_never_sends_daily_recording_commands(self):
        self.select()
        self.join(True)
        with patch.object(self.media, 'request', side_effect=AssertionError('Daily called')):
            self.call('recruiter/interview/start', {**self.identity, 'oralConsent': True}, manager=True)

    def test_completion_recovers_lost_connected_receipt(self):
        self.select()
        self.join(True)
        self.zoom.session = 'actual-session-with-lost-receipt'
        def finish(room):
            self.assertTrue(room['sessionPinned'])
            self.assertEqual(room['meetingSessionId'], self.zoom.session)
        with patch.object(self.zoom, 'finish', side_effect=finish):
            self.call('recruiter/interview/complete', self.identity, manager=True)
        with self.connect() as db:
            meeting = self.portal.interview(db, self.person['id'])
        self.assertEqual(meeting['status'], 'completed')
        self.assertEqual(meeting['room']['meetingSessionId'], self.zoom.session)

    def test_native_end_recovers_lost_connected_receipt(self):
        self.select()
        self.join(True)
        state = {'status': 'ended', 'providerSessionId': 'ended-actual-session'}
        with patch.object(self.zoom, 'get_state', return_value=state):
            self.call('recruiter/interview/complete', self.identity, manager=True)
        with self.connect() as db:
            room = self.portal.interview(db, self.person['id'])['room']
        self.assertTrue(room['sessionPinned'])
        self.assertEqual(room['meetingSessionId'], state['providerSessionId'])

    def test_zoom_completion_uses_zoom_and_materials_failure_does_not_block(self):
        self.select()
        self.join(True)
        class FailedCatalog:
            def register_owner(self, *args):
                raise RuntimeError('storage unavailable')
        self.portal.materials_catalog = FailedCatalog()
        self.call('recruiter/interview/complete', self.identity, manager=True)
        with self.connect() as db:
            meeting = self.portal.interview(db, self.person['id'])
        self.assertEqual(meeting['status'], 'completed')
        self.assertNotIn('captureNeverStarted', meeting['room'])
        self.assertEqual(self.zoom.finished, ['12345678901'])
        self.assertFalse(self.media.finished)

    def test_daily_can_be_selected_back_before_creation(self):
        self.select()
        self.select('daily')
        self.assertEqual(self.join(True)['provider'], 'daily')
        self.assertFalse(self.zoom.created)

    def test_native_zoom_recording_registers_even_without_legacy_start_command(self):
        self.select()
        self.join(True)
        self.call('recruiter/interview/complete', self.identity, manager=True)
        captured = []
        class Catalog:
            def register_owner(self, kind, meeting, policy=None):
                captured.append(meeting)

            def legacy_materials(self, *args, **kwargs):
                return {'recordings': [], 'transcripts': []}
        self.portal.materials_catalog = Catalog()
        with patch('candidate_portal.invites.intake', return_value={'prepared': True}):
            self.portal.materials(self.person['id'])
        self.assertEqual(len(captured), 1)
        self.assertNotIn('captureNeverStarted', captured[0]['room'])


if __name__ == '__main__':
    unittest.main()
