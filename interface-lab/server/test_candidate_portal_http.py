"""Exercise the real API handler with a separate database and fake video provider."""
import base64
import datetime as dt
import http.client
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
            'HR_VIDEO_ENABLED': '0', 'DAILY_API_KEY': '', 'HR_NOTIFICATIONS_ENABLED': '0',
            'HR_HH_ENABLED': '0', 'HR_HH_SENDING_ENABLED': '0',
            'HR_ADDITIONAL_ORIGINS': 'https://legacy.example.test',
            'HR_RECRUITER_ORIGIN': 'https://legacy.example.test'})
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

    def test_recruiter_activation_stays_on_explicit_legacy_origin(self):
        status, result, _ = self.request('recruiter/accounts', {
            'email': str(uuid.uuid4()) + '@example.test', 'name': 'Recruiter migration test'
        }, token=self.manager)
        self.assertEqual(status, 200, result)
        self.assertTrue(result['activationUrl'].startswith('https://legacy.example.test/iframe.html?'))
        self.assertIn('workspace=recruiter', result['activationUrl'])

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

    def test_hh_routes_are_manager_only_and_disabled_without_configuration(self):
        self.assertEqual(self.request('recruiter/hh/state')[0], 401)
        self.assertEqual(self.request('recruiter/hh/state', cookie=self.cookie)[0], 401)
        status, state, headers = self.request('recruiter/hh/state', token=self.manager)
        self.assertEqual(status, 200, state)
        self.assertFalse(state['configured'])
        self.assertFalse(state['sendingEnabled'])
        self.assertEqual(state['connections'], [])
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        status, result, _ = self.request('recruiter/hh/oauth/start', {}, token=self.manager)
        self.assertEqual(status, 503, result)
        self.assertEqual(self.request('recruiter/hh/oauth/start', {}, token=self.manager,
            extra_headers={'Origin': 'https://untrusted.example'})[0], 403)

    def test_actual_http_full_manual_interview_to_meeting_path(self):
        resume = ('Имя: HTTP Test\nГород: Москва\nЖелаемая должность: Менеджер\n'
                  'Опыт работы: ведение клиентов, подготовка коммерческих предложений, '
                  'работа с командой, переговоры и сопровождение договоров. '
                  'Образование: высшее. Навыки: продажи и деловая переписка.')
        code, body, _ = self.request('candidate/resume', {'filename': 'resume.txt',
            'contentBase64': base64.b64encode(resume.encode()).decode()}, cookie=self.cookie)
        self.assertEqual(code, 200, body)
        code, body, _ = self.request('candidate/profile', {'salaryMin': 150000, 'salaryMax': 200000,
            'latestStartDate': (now() + dt.timedelta(days=30)).date().isoformat(), 'resumeReviewed': True}, cookie=self.cookie)
        self.assertEqual(code, 200, body)
        status, state, _ = self.request('recruiter/slots', {'start': iso(self.start), 'recruiterName': self.person['id']}, token=self.manager)
        self.assertEqual(status, 200, state)
        slot = next(s for s in state['slots'] if s['recruiterName'] == self.person['id'])
        from test_candidate_portal import assign_fixture_slot
        with self.app.connect() as db:
            assign_fixture_slot(db, slot)
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

    def test_resume_upload_requires_owner_session_and_has_separate_body_limit(self):
        resume = ('Имя: HTTP Test\nГород: Москва\nЖелаемая должность: Менеджер\n'
                  + 'Опыт работы с клиентами и подготовка предложений. ' * 850)
        payload = {'filename': 'resume.txt', 'contentBase64': base64.b64encode(resume.encode()).decode()}
        self.assertGreater(len(json.dumps(payload)), 32768)
        self.assertEqual(self.request('candidate/resume', payload)[0], 401)
        self.assertEqual(self.request('candidate/resume', payload, token=self.manager)[0], 401)
        self.assertEqual(self.request('candidate/resume', payload, cookie=self.cookie,
            extra_headers={'Origin': 'https://malicious.example'})[0], 403)
        code, state, _ = self.request('candidate/resume', payload, cookie=self.cookie)
        self.assertEqual(code, 200, state)
        self.assertEqual(self.request('candidate/profile', {'about': 'x' * 40000}, cookie=self.cookie)[0], 413)
        # The handler must reject the advertised size before reading the body.
        # Sending the body at once can race the intentional early connection close.
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.putrequest('POST', '/api/hr/candidate/resume')
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Cookie', self.cookie)
            connection.putheader('Content-Length', str(7 * 1024 * 1024 + 1))
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 413)
        finally:
            connection.close()

    def test_recruiter_cv_upload_authenticates_before_body_and_accepts_large_cv(self):
        resume = ('Имя: Подготовленный кандидат\nГород: Москва\nЖелаемая должность: Менеджер\n'
                  + 'Опыт работы с клиентами и подготовка предложений. ' * 850)
        payload = {'filename': 'prepared.txt', 'contentBase64': base64.b64encode(resume.encode()).decode()}
        self.assertGreater(len(json.dumps(payload)), 32768)
        self.assertEqual(self.request('recruiter/candidates', payload)[0], 401)
        self.assertEqual(self.request('recruiter/candidates', payload, cookie=self.cookie)[0], 401)
        self.assertEqual(self.request('recruiter/candidates', payload, token=self.manager,
            extra_headers={'Origin': 'https://malicious.example'})[0], 403)
        code, result, _ = self.request('recruiter/candidates', payload, token=self.manager)
        self.assertEqual(code, 200, result)
        self.assertFalse(result['candidate']['accountActive'])
        self.assertFalse(result['candidate']['consentReuse'])
        self.assertEqual(result['candidate']['resume']['filename'], 'prepared.txt')
        with self.app.connect() as db:
            stored = db.execute('SELECT content FROM cp_resumes WHERE candidate_id=?',
                                (result['candidate']['id'],)).fetchone()[0]
        self.assertEqual(stored, resume.encode())
        # No body is sent: rejection must happen before the server attempts to read it.
        for path, token, length, expected in (
            ('recruiter/candidates', '', 100000, 401),
            ('recruiter/candidates?test=1', '', 100000, 401),
            ('recruiter/candidates', self.manager, 7 * 1024 * 1024 + 1, 413),
        ):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
            try:
                connection.putrequest('POST', '/api/hr/' + path)
                connection.putheader('Content-Type', 'application/json')
                if token:
                    connection.putheader('Authorization', 'Bearer ' + token)
                connection.putheader('Content-Length', str(length))
                connection.endheaders()
                self.assertEqual(connection.getresponse().status, expected)
            finally:
                connection.close()

    def test_sparse_registration_cv_review_and_owner_download_survive_reload(self):
        status, person, headers = self.request('candidate/register', {
            'email': str(uuid.uuid4()) + '@example.test', 'password': 'correct-passphrase-123',
            'consentReuse': True, 'autoApprove': False})
        self.assertEqual(status, 200, person)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertEqual(person['onboarding']['step'], 'resume')
        resumed = self.request('candidate/session', cookie=cookie)[1]['candidate']
        self.assertEqual(resumed['id'], person['id'])
        self.assertEqual(resumed['onboarding']['step'], 'resume')
        start = (now() + dt.timedelta(days=30)).date().isoformat()
        self.assertEqual(self.request('candidate/profile', {'salaryMin': 150000, 'salaryMax': 180000,
            'latestStartDate': start, 'resumeReviewed': True}, cookie=cookie)[0], 409)
        resume = ('Имя: Анна Тестовая\nГород: Москва\nЖелаемая должность: Менеджер\n'
                  'Желаемая зарплата: 150–180 тыс. рублей\nВыйти на работу не позднее: ' + start + '\n'
                  'Опыт работы: пять лет работала с клиентами, готовила коммерческие предложения, '
                  'проводила переговоры и организовывала сопровождение договоров. '
                  'Занималась планированием продаж и деловой перепиской с партнёрами.')
        payload = {'filename': 'anna.txt', 'contentBase64': base64.b64encode(resume.encode()).decode()}
        status, uploaded, _ = self.request('candidate/resume', payload, cookie=cookie)
        self.assertEqual(status, 200, uploaded)
        self.assertEqual(uploaded['onboarding'], {'step': 'preferences', 'missingFields': []})
        self.assertNotIn('contentBase64', json.dumps(uploaded))
        self.assertEqual(self.request('candidate/resume', cookie=cookie)[1],
            {**payload, 'mime': 'text/plain'})
        self.assertEqual(self.request('candidate/resume', cookie=self.cookie)[0], 404)
        self.assertEqual(self.request('candidate/resume', token=self.manager)[0], 401)
        changes = {'city': 'Санкт-Петербург', 'residence': 'Петроградский район',
            'employmentType': 'full-time', 'about': 'Работала с клиентами пять лет.', 'resumeReviewed': True}
        status, reviewed, _ = self.request('candidate/profile', changes, cookie=cookie)
        self.assertEqual(status, 200, reviewed)
        persisted = self.request('candidate/state', cookie=cookie)[1]
        self.assertEqual(persisted['onboarding']['step'], 'complete')
        for key, value in changes.items():
            self.assertEqual(persisted[key], value)
        self.assertFalse(persisted['autoApprove'])

    def test_cross_origin_and_cross_account_requests_are_rejected(self):
        self.assertEqual(self.request('candidate/profile', {'name': 'Bad'}, cookie=self.cookie,
            extra_headers={'Origin': 'https://malicious.example'})[0], 403)
        self.assertEqual(self.request('candidate/profile', {'name': 'Bad'}, cookie=self.cookie,
            extra_headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('candidate/profile', {'id': 'anna'}, cookie=self.cookie)[0], 400)
        self.assertEqual(self.request('candidate/application/decision', {'applicationId': 'another-candidate-app', 'decision': 'accepted'}, cookie=self.cookie)[0], 404)
        self.assertEqual(self.request('candidate/telegram/webhook', {'update_id': 1})[0], 403)

    def test_explicit_legacy_origin_keeps_candidate_login_working(self):
        for origin in (self.app.ORIGIN, 'https://legacy.example.test'):
            status, person, _ = self.request('candidate/login', {
                'email': self.payload['email'], 'password': self.payload['password']},
                extra_headers={'Origin': origin, 'Sec-Fetch-Site': 'same-origin'})
            self.assertEqual(status, 200, person)
            self.assertEqual(person['id'], self.person['id'])
        for origin in ('https://legacy.example.test.attacker.test', 'https://unlisted.example.test'):
            self.assertEqual(self.request('candidate/login', self.payload,
                extra_headers={'Origin': origin})[0], 403)
        self.assertEqual(self.request('candidate/login', self.payload,
            extra_headers={'Origin': 'https://legacy.example.test', 'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_explicit_legacy_origin_preserves_employer_csrf_guard(self):
        for origin in (self.app.ORIGIN, 'https://legacy.example.test'):
            status, response, _ = self.request('employer/login', {
                'email': self.payload['email'], 'password': self.payload['password']},
                extra_headers={'Origin': origin, 'Sec-Fetch-Site': 'same-origin'})
            self.assertEqual(status, 401, response)
        self.assertEqual(self.request('employer/login', {},
            extra_headers={'Origin': 'https://legacy.example.test', 'Sec-Fetch-Site': 'cross-site'})[0], 403)

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
