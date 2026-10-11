"""Exercise the real API handler with a separate database and fake video provider."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

from domain import iso, now
from test_candidate_portal import FakeMedia


class CandidateHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory(prefix='hr-candidate-http-')
        cls.env = patch.dict(os.environ, {'HR_DATA_DIR': cls.data.name, 'HR_TEAM_KEY': 'http-test-team-only',
            'HR_VIDEO_ENABLED': '0', 'DAILY_API_KEY': '', 'HR_NOTIFICATIONS_ENABLED': '0'})
        cls.env.start()
        spec = importlib.util.spec_from_file_location('candidate_http_isolated_app', Path(__file__).with_name('app.py'))
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)
        cls.app.CANDIDATE_PORTAL.media = FakeMedia()
        cls.server = cls.app.ThreadingHTTPServer(('127.0.0.1', 0), cls.app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port) + '/api/hr/'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.env.stop()
        cls.data.cleanup()

    def request(self, path, body=None, cookie='', token='', extra_headers=None):
        headers = {'Cookie': cookie, **(extra_headers or {})}
        if body is not None:
            headers.setdefault('Content-Type', 'application/json')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response), response.headers
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error), error.headers

    def setUp(self):
        self.start = now().replace(microsecond=0) + dt.timedelta(minutes=5)
        self.payload = {'email': str(uuid.uuid4()) + '@example.test', 'password': 'correct-passphrase-123',
            'name': 'HTTP Test', 'city': 'Москва', 'desiredRole': 'Менеджер', 'geographies': ['Москва'],
            'availability': [{'start': iso(self.start), 'end': iso(self.start + dt.timedelta(hours=3))}], 'consentReuse': True}
        status, self.person, headers = self.request('candidate/register', self.payload)
        self.assertEqual(status, 200, self.person)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        status, session, _ = self.request('session', {'teamKey': 'http-test-team-only', 'actor': 'manager'})
        self.assertEqual(status, 200)
        self.manager = session['token']

    def test_real_http_personal_auth_cannot_select_legacy_roles(self):
        status, state, headers = self.request('candidate/state', cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(state['id'], self.person['id'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.request('state', cookie=self.cookie)[0], 401)
        self.assertEqual(self.request('session', {'actor': 'manager'}, cookie=self.cookie)[0], 403)
        self.assertEqual(self.request('recruiter/state', cookie=self.cookie)[0], 401)
        self.assertEqual(self.request('candidate/state', token=self.manager)[0], 401)
        self.assertEqual(self.request('candidate/state')[0], 401)
        self.assertEqual(self.request('candidate/session')[1]['authenticated'], False)

    def test_real_notification_hooks_are_durable_but_never_fake_delivery(self):
        state = self.request('candidate/notifications', cookie=self.cookie)[1]
        self.assertFalse(state['email']['verified'])
        self.assertFalse(state['email']['configured'])
        result = self.request('candidate/email/request', {}, cookie=self.cookie)
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]['status'], 'disabled')
        self.assertEqual(self.request('candidate/email/verify', {'token': 'invalid'}, cookie=self.cookie)[0], 400)
        with self.app.connect() as db:
            events = db.execute('SELECT status FROM cn_outbox WHERE candidate_id=?', (self.person['id'],)).fetchall()
        self.assertTrue(events)
        self.assertTrue(all(row[0] == 'disabled' for row in events))

    def test_actual_http_full_manual_interview_to_meeting_path(self):
        status, state, _ = self.request('recruiter/slots', {'start': iso(self.start), 'recruiterName': self.person['id']}, token=self.manager)
        self.assertEqual(status, 200, state)
        slot = next(s for s in state['slots'] if s['recruiterName'] == self.person['id'])
        self.assertEqual(self.request('candidate/book', {'slotId': slot['id']}, cookie=self.cookie)[0], 200)
        self.assertEqual(self.request('recruiter/interview/join', {'candidateId': self.person['id'], 'consent': True}, token=self.manager)[0], 200)
        self.assertEqual(self.request('candidate/interview/join', {'consent': True}, cookie=self.cookie)[0], 200)
        self.assertEqual(self.request('recruiter/interview/complete', {'candidateId': self.person['id']}, token=self.manager)[0], 200)
        self.assertEqual(self.request('candidate/state', cookie=self.cookie)[1]['interview']['status'], 'completed')
        self.assertEqual(self.request('candidate/state', cookie=self.cookie)[1]['applications'], [])
        status, state, _ = self.request('recruiter/vacancies', {'title': 'Менеджер', 'company': 'HTTP Company',
            'city': 'Москва', 'remote': False, 'description': 'Real test data'}, token=self.manager)
        self.assertEqual(status, 200, state)
        vid = state['vacancies'][0]['id']
        status, state, _ = self.request('recruiter/applications', {'candidateId': self.person['id'], 'vacancyId': vid}, token=self.manager)
        self.assertEqual(status, 200, state)
        application = self.request('candidate/state', cookie=self.cookie)[1]['applications'][0]
        self.assertEqual(application['status'], 'pending')
        self.assertEqual(self.request('candidate/application/decision', {'applicationId': application['id'], 'decision': 'accepted'}, cookie=self.cookie)[0], 200)
        status, state, _ = self.request('recruiter/meetings', {'applicationId': application['id'],
            'start': iso(self.start + dt.timedelta(minutes=60))}, token=self.manager)
        self.assertEqual(status, 200, state)
        meeting = self.request('candidate/state', cookie=self.cookie)[1]['applications'][0]['meeting']
        self.assertEqual(meeting['status'], 'pending')
        status, state, _ = self.request('candidate/meeting/confirm', {'applicationId': application['id'], 'meetingId': meeting['id']}, cookie=self.cookie)
        self.assertEqual(status, 200, state)
        self.assertEqual(state['applications'][0]['meeting']['status'], 'confirmed')

    def test_cross_origin_and_cross_account_requests_are_rejected(self):
        self.assertEqual(self.request('candidate/profile', {'name': 'Bad'}, cookie=self.cookie,
            extra_headers={'Origin': 'https://malicious.example'})[0], 403)
        self.assertEqual(self.request('candidate/profile', {'name': 'Bad'}, cookie=self.cookie,
            extra_headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('candidate/profile', {'id': 'anna'}, cookie=self.cookie)[0], 400)
        self.assertEqual(self.request('candidate/application/decision', {'applicationId': 'another-candidate-app', 'decision': 'accepted'}, cookie=self.cookie)[0], 404)
        self.assertEqual(self.request('candidate/telegram/webhook', {'update_id': 1})[0], 403)

    def test_forwarded_ip_requires_explicit_trust_and_loopback_peer(self):
        source = {'X-HR-Client-IP': '203.0.113.9', 'X-Forwarded-For': '198.51.100.9'}
        with patch.object(self.app, 'TRUST_CLIENT_IP', False):
            self.assertEqual(self.app.client_identity('127.0.0.1', source), '127.0.0.1')
        with patch.object(self.app, 'TRUST_CLIENT_IP', True):
            self.assertEqual(self.app.client_identity('198.51.100.4', source), '198.51.100.4')
            self.assertEqual(self.app.client_identity('127.0.0.1', source), '203.0.113.9')
            self.assertEqual(self.app.client_identity('::1', {'X-HR-Client-IP': '2001:db8::3'}), '2001:db8::3')
            for bad in ('203.0.113.9, 203.0.113.10', 'not-an-ip', '127.0.0.1:8080', 'fe80::1%lo0'):
                self.assertEqual(self.app.client_identity('127.0.0.1', {'X-HR-Client-IP': bad}), '127.0.0.1')
            self.assertEqual(self.app.client_identity('127.0.0.1', {'X-Forwarded-For': '203.0.113.9'}), '127.0.0.1')

    def test_trusted_ingress_auth_limits_one_ip_without_blocking_others(self):
        # Invalid short passwords avoid spending crypto work in this limiter test.
        source = {'X-HR-Client-IP': '203.0.113.120'}
        with patch.object(self.app, 'TRUST_CLIENT_IP', True):
            for index in range(40):
                code, _, _ = self.request('candidate/login', {'email': f'quota{index}@example.test', 'password': 'short'}, extra_headers=source)
                self.assertEqual(code, 401)
            self.assertEqual(self.request('candidate/login', {'email': 'quota-over@example.test', 'password': 'short'}, extra_headers=source)[0], 429)
            self.assertEqual(self.request('candidate/login', {'email': 'quota-other@example.test', 'password': 'short'},
                extra_headers={'X-HR-Client-IP': '203.0.113.121'})[0], 401)


if __name__ == '__main__':
    unittest.main()
