"""Company identity, tenant boundaries and invitation replay checks, isolated DB."""
import concurrent.futures
import hashlib
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from candidate_portal import ClosingConnection
from domain import Problem
from employer_auth import COOKIE, EmployerAuth


class EmployerAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hr-employer-auth-')
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.auth = EmployerAuth(self.connect, threading.RLock(), 'https://hr.example')
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path, factory=ClosingConnection, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def call(self, action, body=None, cookie='', ip='127.0.0.1', method=None, **headers):
        return self.auth.handle(method or ('GET' if body is None else 'POST'),
            '/api/hr/employer/' + action,
            {'Content-Type': 'application/json', 'Cookie': cookie, **headers}, body, ip)

    def payload(self, **changes):
        self.counter += 1
        return {'companyName': 'Компания', 'name': 'Нанимающий',
                'email': f'employee{self.counter}@company.example',
                'password': 'test-passphrase-123', **changes}

    def register(self, **changes):
        response, headers = self.call('register', self.payload(**changes))
        return response['identity'], headers['Set-Cookie'].split(';')[0]

    def invite(self, cookie):
        invite, _ = self.call('team/invite', {}, cookie)
        return parse_qs(urlsplit(invite['url']).fragment)['employer-invite'][0], invite

    def join_body(self, token, **changes):
        self.counter += 1
        return {'token': token, 'name': 'Коллега', 'email': f'colleague{self.counter}@company.example',
                'password': 'colleague-password-123', **changes}

    def problem(self, status, *args, **kwargs):
        with self.assertRaises(Problem) as caught:
            self.call(*args, **kwargs)
        self.assertEqual(caught.exception.status, status, caught.exception.message)

    def test_personal_credentials_company_shared_only_by_invitation(self):
        first, _ = self.register()
        second, _ = self.register()
        self.assertNotEqual(first['companyId'], second['companyId'])
        self.assertEqual(first['company']['name'], second['company']['name'])
        self.assertNotIn('password', str(first))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ep_companies').fetchone()[0], 2)
            hashed = db.execute('SELECT password_hash FROM ep_members').fetchone()[0]
            self.assertTrue(hashed.startswith('scrypt$'))
            self.assertNotIn('test-passphrase', hashed)

    def test_session_cookie_is_hashed_scoped_and_rotated_on_login(self):
        payload = self.payload()
        result, headers = self.call('register', payload)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertTrue(cookie.startswith(COOKIE + '='))
        for flag in ('Path=/', 'Secure', 'HttpOnly', 'SameSite=Lax'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertNotIn('Domain=', headers['Set-Cookie'])
        token = cookie.split('=', 1)[1]
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT token_hash FROM ep_sessions').fetchone()[0],
                             hashlib.sha256(token.encode()).hexdigest())
            identity = self.auth.session(db, {'Cookie': cookie})
            self.assertEqual(identity['companyId'], result['identity']['companyId'])
        login, next_headers = self.call('login', {'email': payload['email'].upper(), 'password': payload['password']}, cookie)
        self.assertEqual(login['identity']['memberId'], result['identity']['memberId'])
        self.assertNotEqual(next_headers['Set-Cookie'], headers['Set-Cookie'])
        self.assertFalse(self.call('session', cookie=cookie)[0]['authenticated'])

    def test_logout_invalidates_only_current_session_and_is_idempotent(self):
        payload = self.payload()
        _, headers = self.call('register', payload)
        old = headers['Set-Cookie'].split(';')[0]
        _, headers = self.call('login', {'email': payload['email'], 'password': payload['password']})
        second = headers['Set-Cookie'].split(';')[0]
        _, cleared = self.call('logout', {}, old)
        self.assertIn('Max-Age=0', cleared['Set-Cookie'])
        self.assertFalse(self.call('session', cookie=old)[0]['authenticated'])
        self.assertTrue(self.call('session', cookie=second)[0]['authenticated'])
        self.problem(401, 'team', cookie=old)
        self.call('logout', {}, old)

    def test_expired_or_malformed_sessions_never_authenticate(self):
        _, cookie = self.register()
        with self.connect() as db:
            db.execute('UPDATE ep_sessions SET expires=?', (int(time.time()) - 1,))
        self.assertFalse(self.call('session', cookie=cookie)[0]['authenticated'])
        self.problem(401, 'team', cookie=COOKIE + '=not-valid')
        self.problem(401, 'team', cookie='__Host-hr_candidate=' + 'x' * 43)
        self.problem(401, 'team', Authorization='Bearer legacy-manager')

    def test_registration_rejects_tenant_injection_and_duplicate_email(self):
        first, _ = self.register(email='a@company.example')
        self.problem(409, 'register', self.payload(email='A@company.example'))
        self.problem(400, 'register', self.payload(companyId=first['companyId']))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ep_companies').fetchone()[0], 1)

    def test_invite_link_hash_only_and_new_member_has_same_company(self):
        owner, owner_cookie = self.register()
        foreign, foreign_cookie = self.register()
        token, invite = self.invite(owner_cookie)
        self.assertNotIn(token, urlsplit(invite['url']).query)
        self.assertTrue(urlsplit(invite['url']).fragment.startswith('employer-invite='))
        with self.connect() as db:
            saved = db.execute('SELECT token_hash,used_by FROM ep_invites').fetchone()
            self.assertEqual(saved[0], hashlib.sha256(token.encode()).hexdigest())
            self.assertIsNone(saved[1])
        preview = self.call('team/join/preview', {'token': token})[0]
        self.assertEqual(preview['company']['id'], owner['companyId'])
        joined, headers = self.call('team/join', self.join_body(token))
        self.assertEqual(joined['identity']['companyId'], owner['companyId'])
        member_cookie = headers['Set-Cookie'].split(';')[0]
        team = self.call('team', cookie=member_cookie)[0]
        self.assertEqual(len(team['members']), 2)
        self.assertNotIn(foreign['memberId'], str(team))
        foreign_team = self.call('team', cookie=foreign_cookie)[0]
        self.assertEqual(len(foreign_team['members']), 1)
        self.assertNotIn(owner['memberId'], str(foreign_team))
        self.problem(410, 'team/join/preview', {'token': token})
        self.problem(410, 'team/join', self.join_body(token))

    def test_expired_invite_never_creates_member(self):
        _, cookie = self.register()
        token, _ = self.invite(cookie)
        with self.connect() as db:
            db.execute('UPDATE ep_invites SET expires=?', (int(time.time()) - 1,))
        self.problem(410, 'team/join', self.join_body(token))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ep_members').fetchone()[0], 1)

    def test_invalid_invitation_never_consumes_valid_one(self):
        _, cookie = self.register()
        token, _ = self.invite(cookie)
        self.problem(410, 'team/join', self.join_body('x' * 43))
        self.problem(400, 'team/join', self.join_body(token, companyId='guessed-company'))
        self.assertEqual(self.call('team/join/preview', {'token': token})[0]['company']['name'], 'Компания')

    def test_existing_account_requires_password_and_cannot_change_company(self):
        _, owner_cookie = self.register()
        outsider, _ = self.register(email='outsider@company.example')
        token, _ = self.invite(owner_cookie)
        self.problem(401, 'team/join', self.join_body(token, email=outsider['email'], password='wrong-password-123'))
        self.problem(409, 'team/join', self.join_body(token, email=outsider['email'], password='test-passphrase-123'))
        self.call('team/join/preview', {'token': token})
        with self.connect() as db:
            self.assertEqual(self.auth.identity(db, outsider['memberId'])['companyId'], outsider['companyId'])

    def test_invitation_single_use_is_atomic_under_two_parallel_joins(self):
        _, cookie = self.register()
        token, _ = self.invite(cookie)
        barrier = threading.Barrier(2)
        bodies = [self.join_body(token), self.join_body(token)]
        def join(body):
            barrier.wait()
            try:
                self.call('team/join', body)
                return 200
            except Problem as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(join, bodies))
        self.assertEqual(sorted(results), [200, 410])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ep_members').fetchone()[0], 2)

    def test_csrf_content_type_methods_and_anonymous_invitation_guard(self):
        _, cookie = self.register()
        self.problem(403, 'team/invite', {}, cookie, Origin='https://other.example')
        self.problem(403, 'team/invite', {}, cookie, **{'Sec-Fetch-Site': 'cross-site'})
        self.problem(415, 'team/invite', {}, cookie, **{'Content-Type': 'text/plain'})
        self.problem(401, 'team/invite', {})
        self.problem(405, 'team/invite', cookie=cookie)
        self.problem(405, 'session', {})
        self.problem(404, 'not-a-route', {})
        self.assertFalse(self.auth.handles('/api/hr/candidate/session'))
        self.assertFalse(self.auth.handles('/api/hr/employer/vacancies'))

    def test_failed_login_attempts_persist_and_throttle_by_email(self):
        payload = self.payload(email='login@company.example')
        self.call('register', payload)
        for attempt in range(15):
            self.problem(401, 'login', {'email': payload['email'], 'password': 'wrong-password-123'}, ip=f'127.0.0.{attempt+1}')
        self.problem(429, 'login', {'email': payload['email'], 'password': payload['password']}, ip='127.0.1.1')
        with self.connect() as db:
            self.assertNotIn(payload['email'], str(db.execute('SELECT * FROM ep_auth_rate_limits').fetchall()))


if __name__ == '__main__':
    unittest.main()
