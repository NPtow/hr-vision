"""HH CV -> consent/slot -> own recruiter mail -> canonical candidate login.

Real domain/HTTP dispatch and SQLite, synthetic CVs and a fake SMTP transport.
No external mail, HH calls or real accounts.
"""
import base64
from dataclasses import replace
import json
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from candidate_access import CandidateAccess
from candidate_booking import CandidateBooking
from candidate_notifications import Settings
from domain import Problem
from hh_routes import HHRecruiterRoutes
import test_candidate_portal as fixture
import test_recruiter_access as recruiter_fixture
from test_candidate_notifications import FakeTransport


class CandidateAccessFlowTest(unittest.TestCase):
    tearDown = fixture.CandidatePortalTest.tearDown
    connect = fixture.CandidatePortalTest.connect
    call = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)
    request = recruiter_fixture.RecruiterAccessTest.request
    provision = recruiter_fixture.RecruiterAccessTest.provision
    recruiter = recruiter_fixture.RecruiterAccessTest.recruiter
    slot = recruiter_fixture.RecruiterAccessTest.slot

    def setUp(self):
        fixture.CandidatePortalTest.setUp(self)
        self.bookings = CandidateBooking(self.portal)
        self.hh = HHRecruiterRoutes(self.connect, self.portal)
        self.config = Settings(enabled=True, public_url=self.portal.origin + '/iframe.html?id=hr-vision-product--candidate&viewMode=story',
                               smtp_host='smtp.example.test', smtp_from='hr@example.test',
                               smtp_user='test-user', smtp_password='test-only')
        self.smtp = FakeTransport()
        self.access = CandidateAccess(self.portal, self.config, self.smtp)
        self.portal.candidate_access = self.access
        # Unexpected provider access must fail instead of escaping the fixture.
        for target in ('candidate_notifications.urllib.request.urlopen', 'candidate_notifications.smtplib.SMTP',
                       'candidate_notifications.smtplib.SMTP_SSL'):
            blocker = patch(target, side_effect=AssertionError('Unexpected external provider call'))
            blocker.start()
            self.addCleanup(blocker.stop)
        self.owner = self.recruiter('owner', 'Рекрутер Владелец')
        self.other = self.recruiter('other', 'Другой Рекрутер')
        self.owner_slot = self.slot(self.owner)

    def rows(self, sql, params=()):
        with self.connect() as db:
            return db.execute(sql, params).fetchall()

    def imported(self):
        cv = ('Имя: Анна Тестовая\nГород: Казань\nМесто проживания: Центр\n'
              'Желаемая должность: Менеджер по продажам\n'
              'Тип занятости: полная занятость\nЖелаемая зарплата: 100–150 тыс. рублей\n'
              'О себе: Работаю с корпоративными клиентами, готовлю предложения и сопровождаю крупные проекты.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cid = self.hh.prepare_candidate(db, 'hh-browser:synthetic-resume-1', {'name': 'Анна Тестовая'},
                {'resumeId': 'synthetic-resume-1', 'cvAvailable': True, 'cvText': cv, 'contactEmail': 'old-cv@example.test'})
            issued = self.bookings.issue(db, cid, 'campaign-synthetic', 'recipient-synthetic')
        token = parse_qs(urlsplit(issued['url']).fragment)['booking'][0]
        return cid, token

    def booked(self):
        cid, token = self.imported()
        state, _ = self.bookings.handle('POST', '/api/hr/booking/book', {'Content-Type': 'application/json'},
            {'token': token, 'slotId': self.owner_slot['id'], 'requestId': 'booking-request-one',
             'email': 'candidate@example.test', 'consentProfile': True}, '127.0.0.1')
        self.assertTrue(state['profileReady'])
        return cid, state['booking']['id'], token

    def assert_error(self, status, route, body=None, auth=None, **kwargs):
        with self.assertRaises(Problem) as raised:
            self.request(route, body, auth, **kwargs)
        self.assertEqual(status, raised.exception.status, raised.exception.message)

    def test_hh_booking_to_explicit_recruiter_email_and_existing_candidate_login(self):
        cid, iid, booking_token = self.booked()
        snapshot = self.rows('SELECT profile FROM cp_candidates WHERE id=?', (cid,))[0][0]
        resume = self.rows('SELECT filename,mime,content,text_content FROM cp_resumes WHERE candidate_id=?', (cid,))[0]
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)))
        self.assertEqual([], self.smtp.email)
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))
        state, headers = self.request('candidate-access/state', {'candidateId': cid}, self.owner)
        self.assertTrue(state['canSend'])
        self.assertEqual('candidate@example.test', state['recipientEmail'])
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))
        body = {'candidateId': cid, 'requestId': 'access-request-001'}
        queued, headers = self.request('candidate-access/send', body, self.owner)
        self.assertEqual('queued', queued['status'])
        self.assertEqual(iid, queued['interviewId'])
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertNotIn('password', json.dumps(queued).lower())
        self.assertEqual([], self.smtp.email)
        self.assertEqual(queued, self.request('candidate-access/send', body, self.owner)[0])
        self.assertEqual(queued, self.request('candidate-access/send', {**body, 'requestId': 'access-request-002'}, self.owner)[0])
        self.assertEqual('sent', self.access.dispatch_once()['status'])
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual(1, len(self.smtp.email))
        mail = self.smtp.email[0]
        self.assertEqual('candidate@example.test', mail['recipient'])
        password = re.search(r'^Пароль: (.+)$', mail['content'], re.M)[1]
        login = re.search(r'^Логин: (.+)$', mail['content'], re.M)[1]
        url = re.search(r'^https://\S+$', mail['content'], re.M)[0]
        self.assertEqual('login', parse_qs(urlsplit(url).query)['cp'][0])
        self.assertNotIn(password, url)
        self.assertNotIn(booking_token, url)
        self.assertNotIn('claim', url)
        person, login_headers = self.call('candidate/login', {'email': login, 'password': password})
        self.assertEqual(cid, person['id'])
        self.assertTrue(person['accountActive'])
        self.assertEqual(iid, person['interview']['id'])
        self.assertEqual('Казань', person['city'])
        self.assertEqual('Менеджер по продажам', person['desiredRole'])
        self.assertEqual('Центр', person['residence'])
        self.assertEqual((100000, 150000), (person['salaryMin'], person['salaryMax']))
        self.assertFalse(person['consentReuse'])
        self.assertFalse(person['autoApprove'])
        cookie = login_headers['Set-Cookie'].split(';')[0]
        downloaded, _ = self.call('candidate/resume', cookie=cookie)
        self.assertEqual(resume[2], base64.b64decode(downloaded['contentBase64']))
        self.assertEqual(resume, self.rows('SELECT filename,mime,content,text_content FROM cp_resumes WHERE candidate_id=?', (cid,))[0])
        self.assertEqual(snapshot, self.rows('SELECT profile FROM cp_candidates WHERE id=?', (cid,))[0][0])
        self.assertEqual([(cid,)], self.rows('SELECT id FROM cp_candidates'))
        self.assertEqual([(iid, cid)], self.rows('SELECT id,candidate_id FROM cp_interviews'))
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_booking_profile_consents')))
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_access_issuances')))
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_access_outbox')))
        self.assertEqual([], self.rows('PRAGMA foreign_key_check'))
        self.assertNotIn(password, str(self.rows('SELECT * FROM cp_access_outbox')))
        self.assertNotIn(password, str(self.rows('SELECT * FROM cp_access_issuances')))
        self.assertNotIn(password, str(self.rows('SELECT * FROM cp_candidates')))
        state, _ = self.request('candidate-access/state', {'candidateId': cid}, self.owner)
        self.assertEqual(('sent', False, 'smtp_acceptance'), (state['status'], state['canSend'], state['deliveryMeaning']))
        self.assertEqual(1, len(self.smtp.email))

    def test_only_assigned_recruiter_can_view_or_send_access(self):
        cid, _, _ = self.booked()
        for action, body in (('state', {'candidateId': cid}),
                             ('send', {'candidateId': cid, 'requestId': 'access-request-001'})):
            self.assert_error(404, 'candidate-access/' + action, body, self.other)
            self.assert_error(401, 'candidate-access/' + action, body)
            self.assert_error(401, 'candidate-access/' + action, body, manager=True)
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)))
        self.assertEqual([], self.smtp.email)

    def test_access_routes_require_post_same_origin_and_exact_allowed_fields(self):
        cid, _, _ = self.booked()
        for action, body in (('state', {'candidateId': cid}),
                             ('send', {'candidateId': cid, 'requestId': 'access-request-001'})):
            route = 'candidate-access/' + action
            self.assert_error(405, route, auth=self.owner, method='GET')
            self.assert_error(403, route, body, self.owner, Origin='https://foreign.example.test')
            self.assert_error(403, route, body, self.owner, Origin=self.portal.origin, **{'Sec-Fetch-Site': 'cross-site'})
            self.assert_error(400, route, {**body, 'email': 'attacker@example.test'}, self.owner)
            self.assert_error(400, route, {**body, 'recruiterId': self.owner['recruiter']['id']}, self.owner)
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))
        self.assertEqual([], self.smtp.email)

    def test_missing_smtp_cannot_activate_or_queue_candidate(self):
        cid, _, _ = self.booked()
        self.access._config = replace(self.config, smtp_host='')
        state, _ = self.request('candidate-access/state', {'candidateId': cid}, self.owner)
        self.assertFalse(state['canSend'])
        self.assertEqual('email_not_configured', state['blockedReason'])
        self.assert_error(503, 'candidate-access/send', {'candidateId': cid, 'requestId': 'access-request-001'}, self.owner)
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)))
        for table in ('cp_access_outbox', 'cp_access_issuances', 'cp_access_requests'):
            self.assertEqual([], self.rows('SELECT * FROM ' + table))
        self.assertIsNone(self.access.dispatch_once())
        self.assertEqual([], self.smtp.email)

    def test_prepared_cv_without_candidate_booking_consent_cannot_issue_password(self):
        cid, _ = self.imported()
        self.call('recruiter/interview/book', {'candidateId': cid, 'slotId': self.owner_slot['id']}, manager=True)
        state, _ = self.request('candidate-access/state', {'candidateId': cid}, self.owner)
        self.assertFalse(state['canSend'])
        self.assertEqual('booking_consent_required', state['blockedReason'])
        self.assert_error(409, 'candidate-access/send', {'candidateId': cid, 'requestId': 'access-request-001'}, self.owner)
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)))
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))
        self.assertEqual([], self.smtp.email)

    def test_legacy_booking_collects_consent_from_public_state_then_issues_same_profile_access(self):
        cid, token = self.imported()
        self.call('recruiter/interview/book', {'candidateId': cid, 'slotId': self.owner_slot['id']}, manager=True)
        before = self.rows('SELECT id,slot_id,candidate_id FROM cp_interviews')
        notifications_before = self.rows('SELECT id FROM rn_outbox')
        public, _ = self.bookings.handle('POST', '/api/hr/booking/state',
                                        {'Content-Type': 'application/json'}, {'token': token}, '127.0.0.1')
        self.assertFalse(public['profileReady'])
        self.assertEqual('booked', public['status'])
        self.assertEqual([], public['slots'])
        self.assertEqual(self.owner_slot['id'], public['booking']['slotId'])
        self.assertEqual('old-cv@example.test', public['contactEmail'])
        self.assertEqual([], self.rows('SELECT * FROM cp_booking_profile_consents'))
        completion = {'token': token, 'slotId': public['booking']['slotId'],
                      'requestId': 'legacy-profile-consent-1', 'email': 'candidate@example.test', 'consentProfile': True}
        for _ in range(2):
            ready, _ = self.bookings.handle('POST', '/api/hr/booking/book',
                                           {'Content-Type': 'application/json'}, completion, '127.0.0.1')
            self.assertTrue(ready['profileReady'])
            self.assertEqual(before[0][0], ready['booking']['id'])
        self.assertEqual(before, self.rows('SELECT id,slot_id,candidate_id FROM cp_interviews'))
        self.assertEqual(notifications_before, self.rows('SELECT id FROM rn_outbox'))
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_booking_profile_consents')))
        self.assertEqual([], self.rows('SELECT * FROM cp_booking_attribution'))
        self.assertEqual([], self.rows('SELECT * FROM cp_access_outbox'))
        state, _ = self.request('candidate-access/state', {'candidateId': cid}, self.owner)
        self.assertTrue(state['canSend'])
        queued, _ = self.request('candidate-access/send',
                                 {'candidateId': cid, 'requestId': 'legacy-access-mail-1'}, self.owner)
        self.assertEqual('queued', queued['status'])
        self.assertEqual('sent', self.access.dispatch_once()['status'])
        password = re.search(r'^Пароль: (.+)$', self.smtp.email[0]['content'], re.M)[1]
        person, _ = self.call('candidate/login', {'email': 'candidate@example.test', 'password': password})
        self.assertEqual(cid, person['id'])
        self.assertEqual(before[0][0], person['interview']['id'])
        self.assertEqual('Казань', person['city'])
        self.assertEqual('HH-synthetic-resume-1.txt', person['resume']['filename'])
        self.assertEqual(1, len(self.smtp.email))


if __name__ == '__main__':
    unittest.main()
