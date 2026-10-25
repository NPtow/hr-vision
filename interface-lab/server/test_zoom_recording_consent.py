"""Consent gates real portal/auth routes; Zoom and delivery are isolated fakes."""
import unittest
from unittest.mock import patch

from domain import Problem
import test_call_providers as calls
import test_candidate_invites as invited
import test_employer_call_providers as employers


class ZoomRecordingConsentTest(unittest.TestCase):
    setUp = calls.CallProvidersTest.setUp
    tearDown = calls.CallProvidersTest.tearDown
    connect = calls.CallProvidersTest.connect
    payload = calls.CallProvidersTest.payload
    call = calls.CallProvidersTest.call
    not_manager = staticmethod(calls.CallProvidersTest.not_manager)
    register = calls.CallProvidersTest.register
    slot = calls.CallProvidersTest.slot
    select = calls.CallProvidersTest.select
    prepared = invited.CandidateInvitationTest.prepared
    invitation = invited.CandidateInvitationTest.invitation

    def automatic(self):
        original = self.zoom.room
        patched = patch.object(self.zoom, 'room', side_effect=lambda *args: {
            **original(*args), 'recordingPolicy': 'automatic-cloud'})
        patched.start()
        self.addCleanup(patched.stop)

    def receipts(self):
        with self.connect() as db:
            return db.execute('SELECT meeting_id,actor_id,version FROM cp_media_consents ORDER BY actor_id').fetchall()

    def prepared_guest(self):
        person = self.prepared()
        self.call('recruiter/interview/book', {'candidateId': person['id'], 'slotId': self.slot(recruiter='Гостевой рекрутер')['id']}, manager=True)
        with self.connect() as db:
            mid = self.portal.interview(db, person['id'])['id']
        identity = {'candidateId': person['id'], 'interviewId': mid}
        self.call('recruiter/interview/provider', {**identity, 'provider': 'zoom'}, manager=True)
        token, _ = self.invitation(person, 'call')
        return person, mid, token

    def test_automatic_host_rejects_missing_false_and_truthy_non_boolean_consent(self):
        self.automatic()
        self.select()
        with patch.object(self.zoom, 'credentials', wraps=self.zoom.credentials) as issue:
            for consent in (None, False, 'true', 1):
                with self.subTest(consent=consent), self.assertRaises(Problem) as error:
                    self.call('recruiter/interview/join', {**self.identity, 'consent': consent}, manager=True)
                self.assertEqual(error.exception.status, 400)
            issue.assert_not_called()
        self.assertEqual(self.receipts(), [])
        with self.connect() as db:
            self.assertEqual(self.portal.interview(db, self.person['id'])['status'], 'booked')

    def test_automatic_host_and_candidate_save_separate_receipts_without_daily_commands(self):
        self.automatic()
        self.select()
        with patch.object(self.media, 'request', side_effect=AssertionError('Daily must not capture Zoom')):
            host, _ = self.call('recruiter/interview/join', {**self.identity, 'consent': True}, manager=True)
            guest, _ = self.call('candidate/interview/join', {'consent': True}, self.cookie)
        self.assertEqual((host['role'], guest['role']), (1, 0))
        self.assertEqual(set(self.receipts()), {
            (self.mid, 'recruiter', 'zoom-recording-v1'),
            (self.mid, self.person['id'], 'zoom-recording-v1')})
        # A previous receipt must not turn an unaffirmed new request into consent.
        with patch.object(self.zoom, 'credentials', wraps=self.zoom.credentials) as issue:
            with self.assertRaises(Problem):
                self.call('recruiter/interview/join', self.identity, manager=True)
            issue.assert_not_called()

    def test_automatic_capability_guest_requires_and_persists_own_consent(self):
        self.automatic()
        person, mid, token = self.prepared_guest()
        with patch.object(self.zoom, 'credentials', wraps=self.zoom.credentials) as issue:
            with self.assertRaises(Problem) as error:
                self.call('candidate/guest/join', {'token': token})
            self.assertEqual(error.exception.status, 400)
            issue.assert_not_called()
        room, _ = self.call('candidate/guest/join', {'token': token, 'consent': True})
        self.assertEqual(room['role'], 0)
        self.assertNotIn('zak', room)
        self.assertEqual(self.receipts(), [(mid, person['id'], 'zoom-recording-v1')])

    def test_old_manual_room_keeps_host_and_capability_guest_contract(self):
        self.select()
        room, _ = self.call('recruiter/interview/join', self.identity, manager=True)
        self.assertEqual(room['provider'], 'zoom')
        _, _, token = self.prepared_guest()
        room, _ = self.call('candidate/guest/join', {'token': token})
        self.assertEqual(room['provider'], 'zoom')
        self.assertEqual(self.receipts(), [])

    def test_daily_capability_guest_remains_without_implicit_consent_receipt(self):
        person = self.prepared()
        self.call('recruiter/interview/book', {'candidateId': person['id'], 'slotId': self.slot(recruiter='Гостевой рекрутер')['id']}, manager=True)
        token, _ = self.invitation(person, 'call')
        room, _ = self.call('candidate/guest/join', {'token': token, 'consent': True})
        self.assertEqual(room['provider'], 'daily')
        self.assertEqual(self.receipts(), [])


class EmployerZoomConsentTest(unittest.TestCase):
    # Reuse fixture helpers without inheriting unrelated test methods.
    setUpClass = classmethod(employers.EmployerCallProvidersTest.setUpClass.__func__)
    tearDownClass = classmethod(employers.EmployerCallProvidersTest.tearDownClass.__func__)
    setUp = employers.EmployerCallProvidersTest.setUp
    for _name in ('request', 'ok', 'employer', 'vacancy', 'state', 'candidate', 'batch',
                  'accepted', 'action_body', 'act', 'propose', 'body', 'map_host',
                  'select', 'join_host', 'join_candidate'):
        locals()[_name] = getattr(employers.EmployerCallProvidersTest, _name)
    exact = staticmethod(employers.EmployerCallProvidersTest.exact)

    def test_employer_auto_recording_receipt_identifies_account_without_changing_daily_actor(self):
        original = self.zoom.room
        with patch.object(self.zoom, 'room', side_effect=lambda *args: {
                **original(*args), 'recordingPolicy': 'automatic-cloud'}):
            self.map_host()
            self.select()
            with patch.object(self.zoom, 'credentials', wraps=self.zoom.credentials) as issue:
                status, _, _ = self.request('employer/room', self.body(consent=False), token=self.owner['token'])
                self.assertEqual(status, 400)
                issue.assert_not_called()
            self.join_host()
        with self.app.connect() as db:
            rows = db.execute('SELECT actor_id,version FROM cp_media_consents WHERE meeting_id=?',
                              (self.meeting['id'],)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][0].startswith('employer:'))
        self.assertNotEqual(rows[0][0], 'manager')
        self.assertEqual(rows[0][1], 'zoom-recording-v1')


if __name__ == '__main__':
    unittest.main()
