"""Local-CV operator script through real booking/access, isolated SQLite/fake SMTP."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from domain import Problem
import test_candidate_access_flow as flow
from test_candidate_resume import TEXT

spec = importlib.util.spec_from_file_location('candidate_outreach_cli',
    Path(__file__).resolve().parent / 'scripts' / 'candidate-outreach.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class ManualOutreachTest(unittest.TestCase):
    setUp = flow.CandidateAccessFlowTest.setUp
    tearDown = flow.CandidateAccessFlowTest.tearDown
    connect = flow.CandidateAccessFlowTest.connect
    call = flow.CandidateAccessFlowTest.call
    not_manager = staticmethod(flow.CandidateAccessFlowTest.not_manager)
    request = flow.CandidateAccessFlowTest.request
    provision = flow.CandidateAccessFlowTest.provision
    recruiter = flow.CandidateAccessFlowTest.recruiter
    slot = flow.CandidateAccessFlowTest.slot
    rows = flow.CandidateAccessFlowTest.rows
    booked = flow.CandidateAccessFlowTest.booked
    assert_error = flow.CandidateAccessFlowTest.assert_error

    def resume(self):
        return {'filename': 'resume.txt', 'mime': 'text/plain', 'content': TEXT.encode(),
                'text': TEXT, 'fields': {'name': 'Анна Тестовая', 'city': 'Казань',
                    'desiredRole': 'Менеджер по продажам', 'residence': 'Центр',
                    'salaryMin': 100000, 'salaryMax': 150000,
                    'about': 'Работаю с корпоративными клиентами, готовлю предложения и сопровождаю крупные проекты.'}}

    def imported(self):
        self.bookings.event_sink = Mock(side_effect=AssertionError('Manual entry must not use HH transport'))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            issued = self.bookings.issue_manual(db, self.resume(), 'personal-test', 'recipient-test')
        self.assertEqual('personal', parse_qs(urlsplit(issued['url']).query)['utm_source'][0])
        return issued['candidateId'], parse_qs(urlsplit(issued['url']).fragment)['booking'][0]

    test_booking_to_mail_to_same_account = flow.CandidateAccessFlowTest.test_hh_booking_to_explicit_recruiter_email_and_existing_candidate_login
    test_owner_only = flow.CandidateAccessFlowTest.test_only_assigned_recruiter_can_view_or_send_access
    test_consent_required = flow.CandidateAccessFlowTest.test_prepared_cv_without_candidate_booking_consent_cannot_issue_password

    def test_view_and_booking_are_counted_once_without_creating_account_early(self):
        cid, token = self.imported()
        for _ in range(2):
            state, _ = self.bookings.handle('POST', '/api/hr/booking/state',
                {'Content-Type': 'application/json'}, {'token': token}, '127.0.0.1')
        self.assertEqual('available', state['status'])
        self.assertNotIn('resume', state)
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)))
        self.assertEqual([], self.rows('SELECT * FROM cp_interviews'))
        self.assertEqual([], self.rows('SELECT * FROM cp_booking_profile_consents'))
        self.assertEqual([('booking.page_loaded',)], self.rows('SELECT kind FROM cp_booking_manual_events'))
        body = {'token': token, 'slotId': self.owner_slot['id'], 'requestId': 'book-one',
                'email': 'candidate@example.test', 'consentProfile': True}
        for _ in range(2):
            self.bookings.handle('POST', '/api/hr/booking/book', {'Content-Type': 'application/json'}, body, '127.0.0.1')
        self.assertEqual(2, len(self.rows('SELECT * FROM cp_booking_manual_events')))
        self.assertEqual([('manual-cv-profile-v1',)], self.rows('SELECT consent_version FROM cp_booking_profile_consents'))
        self.bookings.event_sink.assert_not_called()

    def test_repeat_same_cv_same_link_changed_cv_rejected(self):
        cid, token = self.imported()
        with self.connect() as db:
            same = self.bookings.issue_manual(db, self.resume(), 'personal-test', 'recipient-test')
            self.assertEqual(cid, same['candidateId'])
            self.assertEqual(token, parse_qs(urlsplit(same['url']).fragment)['booking'][0])
        with self.assertRaises(Problem), self.connect() as db:
            self.bookings.issue_manual(db, {**self.resume(), 'content': b'changed'}, 'personal-test', 'recipient-test')
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_candidates')))
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_booking_invitations')))

    def test_existing_login_email_does_not_block_contact_booking_or_change_account(self):
        self.call('candidate/register', {'email': 'taken@example.test', 'password': 'Example-password-12',
                                        'name': 'Existing Candidate', 'consentReuse': True})
        cid, token = self.imported()
        before = self.rows("SELECT * FROM cp_candidates WHERE email='taken@example.test'")
        result, _ = self.bookings.handle('POST', '/api/hr/booking/book', {'Content-Type': 'application/json'},
            {'token': token, 'slotId': self.owner_slot['id'], 'requestId': 'book-one',
             'email': 'taken@example.test', 'consentProfile': True}, '127.0.0.1')
        self.assertEqual('booked', result['status'])
        self.assertEqual(before, self.rows("SELECT * FROM cp_candidates WHERE email='taken@example.test'"))
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)))
        self.assertEqual([(cid,)], self.rows('SELECT candidate_id FROM cp_interviews'))
        self.assertEqual(2, len(self.rows('SELECT * FROM rn_outbox')))

    def test_compound_identifiers_cannot_merge_two_people(self):
        with self.connect() as db:
            first = self.bookings.issue_manual(db, self.resume(), 'campaign:a', 'b')
            second = self.bookings.issue_manual(db, self.resume(), 'campaign', 'a:b')
        self.assertNotEqual(first['candidateId'], second['candidateId'])
        self.assertNotEqual(first['url'], second['url'])

    def test_actual_cli_applies_and_recovers_without_second_model_run(self):
        import tempfile
        service = SimpleNamespace(CANDIDATE_PORTAL=self.portal, BOOKING=self.bookings, connect=self.connect)
        model = self.portal.resume_model
        with tempfile.TemporaryDirectory() as root:
            source, output = Path(root) / 'resume.txt', Path(root) / 'result.json'
            source.write_text(TEXT)
            args = ['prepare', '--resume', str(source), '--campaign', 'cli-test', '--recipient', 'one',
                    '--output', str(output)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, cli.main(args))
            self.assertEqual([], self.rows('SELECT * FROM cp_candidates'))
            with patch.dict('sys.modules', {'app': service}), \
                    patch.object(model, 'generate', return_value={**self.resume(), 'runId': 'validated-run'}) as generate, \
                    patch.object(model, 'assert_current') as current:
                for _ in range(2):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        self.assertEqual(0, cli.main([*args, '--apply']))
                    result = json.loads(output.read_text())
                    self.assertNotIn('#booking=', stdout.getvalue())
                    self.assertEqual(0o600, output.stat().st_mode & 0o777)
                self.assertTrue(result['reused'])
                generate.assert_called_once()
                current.assert_called_once()
            self.assertEqual([], self.rows('SELECT * FROM cp_interviews'))
            self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))
            self.assertEqual(1, len(self.rows('SELECT * FROM cp_candidates')))
            self.assertEqual('external_not_observed', cli.inspect(service, result['invitationId'])['messageDelivery'])

    def test_model_failure_cannot_issue_candidate_or_link(self):
        service = SimpleNamespace(CANDIDATE_PORTAL=self.portal, BOOKING=self.bookings, connect=self.connect)
        with patch.object(self.portal.resume_model, 'generate', side_effect=Problem('model_failed', 503)):
            with self.assertRaises(Problem):
                cli.prepare(service, self.resume(), 'failure-test', 'one')
        self.assertEqual([], self.rows('SELECT * FROM cp_candidates'))
        self.assertEqual([], self.rows('SELECT * FROM cp_booking_invitations'))


if __name__ == '__main__':
    unittest.main()
