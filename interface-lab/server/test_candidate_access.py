"""Real booking/account integration, isolated people and fake SMTP only."""
import concurrent.futures
from dataclasses import replace
import json
import re
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import candidate_access as access
import candidate_booking as booking
import candidate_invites as invites
import candidate_notifications as delivery
from candidate_portal import password_hash, profile_value
from domain import Problem
import test_candidate_portal as fixture


class FakeTransport:
    def __init__(self):
        self.messages = []
        self.failure = None

    def send_email(self, recipient, subject, content, message_id):
        self.messages.append((recipient, subject, content, message_id))
        if self.failure:
            raise self.failure
        return 'smtp_accepted'


class CandidateAccessTest(unittest.TestCase):
    tearDown = fixture.CandidatePortalTest.tearDown
    connect = fixture.CandidatePortalTest.connect
    payload = fixture.CandidatePortalTest.payload
    call = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)
    register = fixture.CandidatePortalTest.register
    slot = fixture.CandidatePortalTest.slot

    def setUp(self):
        fixture.CandidatePortalTest.setUp(self)
        self.portal.notifications = delivery
        with self.connect() as db:
            delivery.init(db)
        self.booking = booking.CandidateBooking(self.portal)
        self.config = delivery.Settings(enabled=True, smtp_host='smtp.example.test',
                                       smtp_from='hr@example.test', public_url='https://portal.example/iframe.html?id=hr-vision-product--candidate&viewMode=story')
        self.transport = FakeTransport()
        self.access = access.CandidateAccess(self.portal, self.config, self.transport)
        for target in ('candidate_notifications.smtplib.SMTP', 'candidate_notifications.smtplib.SMTP_SSL',
                       'candidate_notifications.urllib.request.urlopen'):
            guard = patch(target, side_effect=AssertionError('Unexpected external call'))
            guard.start()
            self.addCleanup(guard.stop)

    def rows(self, query, values=()):
        with self.connect() as db:
            return db.execute(query, values).fetchall()

    def prepared(self, *, consent=True, source='hh', email=None):
        self.count += 1
        number = self.count
        resume = {'filename': 'resume.txt', 'mime': 'text/plain', 'content': b'synthetic CV',
                  'text': 'Synthetic CV for the exact candidate',
                  'fields': {'name': 'Candidate ' + str(number), 'city': 'Москва'}}
        with self.connect() as db:
            cid = invites.create_prepared(db, profile_value({'name': 'Candidate ' + str(number),
                'city': 'Москва', 'about': 'Kept candidate biography', 'consentReuse': False}),
                source=source, external_key='access-test:' + str(number), cv=resume)
        slot = self.slot(minutes=number * 30, recruiter='Access owner')
        if consent:
            with self.connect() as db:
                invitation = self.booking.issue(db, cid, 'campaign-access', 'recipient-' + cid)
            token = parse_qs(urlsplit(invitation['url']).fragment)['booking'][0]
            self.booking.handle('POST', '/api/hr/booking/book', {'Content-Type': 'application/json'},
                {'token': token, 'slotId': slot['id'], 'requestId': 'booking-request-' + str(number),
                 'email': email or 'candidate' + str(number) + '@example.test', 'consentProfile': True}, '127.0.0.1')
        else:
            with self.connect() as db:
                self.portal.book(db, cid, {'slotId': slot['id']}, prepared=True)
        with self.connect() as db:
            iid = db.execute('SELECT id FROM cp_interviews WHERE candidate_id=?', (cid,)).fetchone()[0]
            rid = db.execute('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?', (slot['id'],)).fetchone()[0]
        return cid, iid, {'id': rid, 'legacy': False}

    def state(self, candidate):
        cid, _, principal = candidate
        with self.connect() as db:
            return self.access.state(db, cid, principal=principal)

    def enqueue(self, candidate, request_id='access-request-1'):
        cid, iid, principal = candidate
        with self.portal.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self.access.enqueue(db, cid, iid, request_id, principal=principal)

    def password(self):
        return re.search(r'^Пароль: (.+)$', self.transport.messages[-1][2], re.M)[1]

    def test_booking_request_send_login_keeps_one_cv_profile_and_interview(self):
        candidate = self.prepared()
        cid, iid, _ = candidate
        before = self.rows('SELECT profile FROM cp_candidates WHERE id=?', (cid,))[0][0]
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)))
        self.assertEqual('not_requested', self.state(candidate)['status'])
        queued = self.enqueue(candidate)
        self.assertEqual('queued', queued['status'])
        self.assertEqual('credentials', queued['mode'])
        self.assertFalse(queued['canSend'])
        self.assertEqual([], self.transport.messages)
        self.assertEqual('sent', self.access.dispatch_once()['status'])
        password = self.password()
        login, _ = self.call('candidate/login', {'email': queued['recipientEmail'], 'password': password})
        self.assertEqual(cid, login['id'])
        self.assertEqual(iid, login['interview']['id'])
        self.assertEqual('resume.txt', login['resume']['filename'])
        self.assertEqual('complete', login['onboarding']['step'])
        self.assertEqual('Kept candidate biography', login['about'])
        self.assertFalse(login['consentReuse'])
        self.assertFalse(login['autoApprove'])
        self.assertEqual(before, self.rows('SELECT profile FROM cp_candidates WHERE id=?', (cid,))[0][0])
        self.assertEqual([(0,)], self.rows('SELECT email_verified FROM cn_recipients WHERE candidate_id=?', (cid,)))
        self.assertEqual(1, len(self.rows('SELECT id FROM cp_candidates')))
        self.assertEqual('smtp_acceptance', self.state(candidate)['deliveryMeaning'])
        with self.connect() as db:
            persisted = '\n'.join(db.iterdump())
        self.assertNotIn(password, persisted)
        self.assertNotIn(password, json.dumps(queued))
        self.assertNotIn(password, json.dumps(self.state(candidate)))
        self.assertIn('cp=login', self.transport.messages[0][2])

    def test_unconfigured_mail_never_activates_or_queues(self):
        candidate = self.prepared()
        self.access._config = replace(self.config, smtp_host='')
        self.assertEqual('email_not_configured', self.state(candidate)['blockedReason'])
        with self.assertRaises(Problem) as caught:
            self.enqueue(candidate)
        self.assertEqual(503, caught.exception.status)
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates'))
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual([], self.transport.messages)

    def test_no_booking_consent_manual_claim_path_is_not_replaced(self):
        for source in ('manual', 'hh'):
            candidate = self.prepared(consent=False, source=source)
            self.assertEqual('booking_consent_required', self.state(candidate)['blockedReason'])
            with self.assertRaises(Problem) as caught:
                self.enqueue(candidate)
            self.assertEqual(409, caught.exception.status)
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))

    def test_cv_is_required_for_prepared_access(self):
        candidate = self.prepared()
        with self.connect() as db:
            db.execute('DELETE FROM cp_resumes WHERE candidate_id=?', (candidate[0],))
        self.assertEqual('resume_required', self.state(candidate)['blockedReason'])
        with self.assertRaises(Problem):
            self.enqueue(candidate)

    def test_only_exact_active_recruiter_owns_interview(self):
        cid, iid, principal = self.prepared()
        for forged in ({'id': 'someone-else'}, {'id': principal['id'], 'legacy': True}, None):
            with self.assertRaises(Problem), self.connect() as db:
                self.access.state(db, cid, principal=forged)
            with self.assertRaises(Problem), self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.access.enqueue(db, cid, iid, 'access-request-1', principal=forged)
        with self.connect() as db:
            db.execute('UPDATE rc_accounts SET active=0 WHERE id=?', (principal['id'],))
        with self.assertRaises(Problem), self.connect() as db:
            self.access.state(db, cid, principal=principal)
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))

    def test_wrong_interview_or_extra_consent_email_fields_are_rejected(self):
        cid, iid, principal = self.prepared()
        for body in ({'candidateId': cid, 'requestId': 'access-request-1', 'email': 'another@example.test'},
                     {'requestId': 'access-request-1', 'consentConfirmed': True}):
            with self.assertRaises(Problem), self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.access.request(db, principal, cid, body)
        with self.assertRaises(Problem), self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.access.enqueue(db, cid, 'another-interview', 'access-request-1', principal=principal)
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))

    def test_email_collision_does_not_merge_or_reset_other_account(self):
        existing, _ = self.register(email='same@example.test')
        old_hash = self.rows('SELECT password_hash FROM cp_candidates WHERE id=?', (existing['id'],))[0][0]
        candidate = self.prepared(email='same@example.test')
        self.assertEqual('email_conflict', self.state(candidate)['blockedReason'])
        with self.assertRaises(Problem) as caught:
            self.enqueue(candidate)
        self.assertEqual(409, caught.exception.status)
        self.assertEqual(old_hash, self.rows('SELECT password_hash FROM cp_candidates WHERE id=?', (existing['id'],))[0][0])
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates WHERE id=?', (candidate[0],)))

    def test_double_click_distinct_requests_converge_without_reset(self):
        candidate = self.prepared()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            states = list(pool.map(lambda value: self.enqueue(candidate, value), ['access-request-1', 'access-request-2']))
        self.assertEqual(states[0]['deliveryId'], states[1]['deliveryId'])
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_access_outbox')))
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_access_issuances')))
        hashed = self.rows('SELECT password_hash FROM cp_candidates')[0][0]
        self.access.dispatch_once()
        self.enqueue(candidate, 'access-request-3')
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual(1, len(self.transport.messages))
        self.assertEqual(hashed, self.rows('SELECT password_hash FROM cp_candidates')[0][0])

    def test_request_id_cannot_be_replayed_for_another_candidate(self):
        first, second = self.prepared(), self.prepared()
        self.enqueue(first)
        with self.assertRaises(Problem) as caught:
            self.enqueue(second)
        self.assertEqual(409, caught.exception.status)
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_access_outbox')))

    def test_repeated_queued_click_is_bound_and_does_not_become_retry_after_failure(self):
        first, second = self.prepared(), self.prepared()
        self.enqueue(first, 'access-request-first')
        self.enqueue(first, 'access-request-second')
        self.transport.failure = delivery.DeliveryFailure('smtp_authentication')
        self.access.dispatch_once()
        self.assertEqual('failed', self.enqueue(first, 'access-request-second')['status'])
        with self.assertRaises(Problem) as caught:
            self.enqueue(second, 'access-request-second')
        self.assertEqual(409, caught.exception.status)
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual(1, len(self.transport.messages))

    def test_known_failure_requires_new_click_and_keeps_credentials_after_restart(self):
        candidate = self.prepared()
        self.enqueue(candidate)
        self.transport.failure = delivery.DeliveryFailure('smtp_authentication')
        self.assertEqual('failed', self.access.dispatch_once()['status'])
        first_password = self.password()
        first_id = self.transport.messages[-1][3]
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual('failed', self.enqueue(candidate)['status'])
        self.assertIsNone(self.access.dispatch_once())
        self.access = access.CandidateAccess(self.portal, self.config, self.transport)
        self.transport.failure = None
        self.assertEqual('queued', self.enqueue(candidate, 'access-request-2')['status'])
        self.assertEqual('sent', self.access.dispatch_once()['status'])
        self.assertEqual(first_password, self.password())
        self.assertEqual(first_id, self.transport.messages[-1][3])

    def test_unknown_provider_outcome_never_auto_or_new_click_resends(self):
        candidate = self.prepared()
        self.enqueue(candidate)
        self.transport.failure = delivery.DeliveryFailure('smtp_delivery_uncertain')
        self.assertEqual('unknown', self.access.dispatch_once()['status'])
        self.transport.failure = None
        self.assertEqual('unknown', self.enqueue(candidate, 'access-request-2')['status'])
        self.assertEqual('unknown_delivery', self.state(candidate)['blockedReason'])
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual(1, len(self.transport.messages))

    def test_unphased_smtp_rejection_may_be_after_data_and_is_unknown(self):
        candidate = self.prepared()
        self.enqueue(candidate)
        self.transport.failure = delivery.DeliveryFailure('smtp_rejected', retryable=True)
        self.assertEqual('unknown', self.access.dispatch_once()['status'])
        self.assertEqual('unknown', self.enqueue(candidate, 'access-request-2')['status'])
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual(1, len(self.transport.messages))

    def test_arbitrary_transport_exception_is_sanitized(self):
        candidate = self.prepared()
        self.enqueue(candidate)
        self.transport.failure = RuntimeError('smtp-password=never-store-this private@example.test')
        self.assertEqual('unknown', self.access.dispatch_once()['status'])
        self.assertEqual('smtp_delivery_uncertain', self.state(candidate)['lastError'])
        with self.connect() as db:
            persisted = '\n'.join(db.iterdump())
        self.assertNotIn('never-store-this', persisted)
        self.assertNotIn('private@example.test', persisted)

    def test_crash_after_durable_send_start_recovers_unknown_not_resend(self):
        class Crash(BaseException):
            pass
        candidate = self.prepared()
        self.enqueue(candidate)
        self.transport.failure = Crash()
        with self.assertRaises(Crash):
            self.access.dispatch_once()
        self.assertEqual('sending', self.state(candidate)['status'])
        self.access = access.CandidateAccess(self.portal, self.config, self.transport)
        with self.connect() as db:
            db.execute('UPDATE cp_access_outbox SET lease_until=?', (int(time.time()) - 1,))
        self.transport.failure = None
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual('unknown', self.state(candidate)['status'])
        self.assertEqual('delivery_interrupted', self.state(candidate)['lastError'])
        self.assertEqual(1, len(self.transport.messages))

    def test_two_workers_cannot_send_same_delivery(self):
        candidate = self.prepared()
        self.enqueue(candidate)
        second = access.CandidateAccess(self.portal, self.config, self.transport)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda worker: worker.dispatch_once(), [self.access, second]))
        self.assertEqual('sent', self.state(candidate)['status'])
        self.assertEqual(1, len(self.transport.messages))

    def test_changed_password_before_dispatch_is_not_emailed_or_reset(self):
        candidate = self.prepared()
        self.enqueue(candidate)
        with self.connect() as db:
            db.execute('UPDATE cp_candidates SET password_hash=? WHERE id=?',
                       (password_hash('candidate-changed-pass-123'), candidate[0]))
        self.assertEqual('failed', self.access.dispatch_once()['status'])
        self.assertEqual('credentials_changed', self.state(candidate)['blockedReason'])
        with self.assertRaises(Problem):
            self.enqueue(candidate, 'access-request-2')
        self.assertEqual([], self.transport.messages)

    def test_existing_active_account_sends_signin_notice_without_reset(self):
        person, cookie = self.register(email='existing@example.test')
        slot = self.slot(recruiter='Active account recruiter')
        self.call('candidate/book', {'slotId': slot['id']}, cookie)
        with self.connect() as db:
            rid = db.execute('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?', (slot['id'],)).fetchone()[0]
            iid = db.execute('SELECT id FROM cp_interviews WHERE candidate_id=?', (person['id'],)).fetchone()[0]
            old_hash = db.execute('SELECT password_hash FROM cp_candidates WHERE id=?', (person['id'],)).fetchone()[0]
        candidate = (person['id'], iid, {'id': rid})
        self.assertEqual('signin', self.enqueue(candidate)['mode'])
        self.access.dispatch_once()
        self.assertNotIn('Пароль:', self.transport.messages[0][2])
        self.assertIn('текущий пароль', self.transport.messages[0][2])
        self.assertEqual(old_hash, self.rows('SELECT password_hash FROM cp_candidates')[0][0])
        self.assertEqual([], self.rows('SELECT * FROM cp_access_issuances'))

    def test_init_and_enqueue_do_not_commit_callers_transaction(self):
        candidate = self.prepared()
        cid, iid, principal = candidate
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            access.init(db)
            self.access.enqueue(db, cid, iid, 'access-request-1', principal=principal)
            db.rollback()
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates'))

    def test_disabled_worker_and_temporarily_disabled_smtp_never_send(self):
        candidate = self.prepared()
        self.access._config = replace(self.config, enabled=False)
        self.assertIsNone(self.access.start_worker())
        self.access._config = self.config
        self.enqueue(candidate)
        self.access._config = replace(self.config, smtp_host='')
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual([], self.transport.messages)
        self.access._config = self.config
        self.access.dispatch_once()
        self.assertEqual(1, len(self.transport.messages))

    def test_shared_tls_smtp_transport_sends_only_explicit_access_email(self):
        candidate = self.prepared()
        self.enqueue(candidate)
        self.access._transport = None
        server = MagicMock()
        server.__enter__.return_value = server
        server.send_message.return_value = {}
        with patch('candidate_notifications.smtplib.SMTP', return_value=server):
            self.assertEqual('sent', self.access.dispatch_once()['status'])
        server.starttls.assert_called_once()
        message = server.send_message.call_args[0][0]
        self.assertEqual(self.state(candidate)['recipientEmail'], message['To'])
        self.assertIn('Пароль:', message.get_content())
        self.assertEqual('smtp_accepted', self.rows('SELECT provider_ref FROM cp_access_outbox')[0][0])


if __name__ == '__main__':
    unittest.main()
