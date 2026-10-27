"""Isolated employer access checks; no real identities, messages or providers."""
import concurrent.futures
import hashlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import employer_access as access
from domain import Problem


class EmployerAccessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hr-employer-access-')
        self.db_path = Path(self.tmp.name) / 'state.sqlite3'
        with self.connect() as db:
            access.init(db)

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        self.addCleanup(db.close)
        return db

    def payload(self, suffix='one', **changes):
        return {'email': suffix + '@example.test', 'password': 'test-passphrase-123',
                'name': 'Нанимающий ' + suffix, 'workspaceName': 'Компания ' + suffix, **changes}

    @staticmethod
    def headers(result):
        return {'Cookie': access.session_cookie(result['token']).split(';')[0]}

    def register(self, suffix='one', **changes):
        with self.connect() as db:
            return access.register(db, self.payload(suffix, **changes))

    def problem(self, code, fn, *args, **kwargs):
        with self.assertRaises(Problem) as raised:
            fn(*args, **kwargs)
        self.assertEqual(raised.exception.status, code)
        return raised.exception

    def test_two_employers_get_distinct_server_owned_workspaces(self):
        one, two = self.register('one'), self.register('two')
        with self.connect() as db:
            left = access.authenticate(db, self.headers(one))
            right = access.authenticate(db, self.headers(two))
            self.assertEqual(left['workspaceId'], one['workspace']['id'])
            self.assertEqual(right['workspaceId'], two['workspace']['id'])
            self.assertNotEqual(left['workspaceId'], right['workspaceId'])
            self.assertNotEqual(left['accountId'], right['accountId'])
            self.assertEqual(left['role'], 'employer')
            self.assertFalse(left['team'])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_shared_account_multiple_sessions_and_logout_only_one(self):
        first = self.register()
        with self.connect() as db:
            second = access.login(db, {'email': 'ONE@example.test', 'password': 'test-passphrase-123'})
            self.assertEqual(first['workspace'], second['workspace'])
            self.assertEqual(first['accountId'], second['accountId'])
            self.assertNotEqual(first['token'], second['token'])
            self.assertEqual(access.authenticate(db, self.headers(first)), access.authenticate(db, self.headers(second)))
            cookie = access.logout(db, self.headers(first))
            self.assertIn('Max-Age=0', cookie)
            self.problem(401, access.authenticate, db, self.headers(first))
            self.assertEqual(access.authenticate(db, self.headers(second))['accountId'], second['accountId'])
            self.assertEqual(access.logout(db, self.headers(first)), cookie)

    def test_password_and_session_tokens_are_not_stored_plaintext(self):
        account = self.register()
        with self.connect() as db:
            stored = db.execute('SELECT password_hash FROM em_accounts WHERE id=?', (account['accountId'],)).fetchone()[0]
            self.assertTrue(stored.startswith('scrypt$16384$8$1$'))
            tokens = db.execute('SELECT token_hash FROM em_sessions').fetchall()
            self.assertEqual(tokens, [(hashlib.sha256(account['token'].encode()).hexdigest(),)])
            dump = '\n'.join(db.iterdump())
            self.assertNotIn(account['token'], dump)
            self.assertNotIn('test-passphrase-123', dump)

    def test_same_password_uses_distinct_salts(self):
        one, two = self.register('one'), self.register('two')
        with self.connect() as db:
            hashes = [db.execute('SELECT password_hash FROM em_accounts WHERE id=?', (entry['accountId'],)).fetchone()[0]
                      for entry in (one, two)]
            self.assertNotEqual(hashes[0], hashes[1])

    def test_wrong_and_unknown_credentials_fail_equally(self):
        self.register()
        with self.connect() as db:
            errors = [self.problem(401, access.login, db, {'email': email, 'password': 'wrong-password-123'}).message
                      for email in ('one@example.test', 'missing@example.test')]
            self.assertEqual(errors[0], errors[1])
            self.assertEqual(db.execute('SELECT count(*) FROM em_sessions').fetchone()[0], 1)

    def test_unknown_identity_still_runs_scrypt(self):
        with self.connect() as db, patch.object(access.hashlib, 'scrypt', wraps=hashlib.scrypt) as scrypt:
            self.problem(401, access.login, db, {'email': 'missing@example.test', 'password': 'wrong-password-123'})
            scrypt.assert_called_once()

    def test_expired_session_and_disabled_account_fail(self):
        with patch.object(access.time, 'time', return_value=1000000):
            first = self.register()
        with self.connect() as db, patch.object(access.time, 'time', return_value=1000000 + access.SESSION_SECONDS):
            self.problem(401, access.authenticate, db, self.headers(first))
        with self.connect() as db:
            current = access.login(db, {'email': 'one@example.test', 'password': 'test-passphrase-123'})
            self.assertEqual(db.execute('SELECT count(*) FROM em_sessions').fetchone()[0], 1)
            db.execute('UPDATE em_accounts SET active=0 WHERE id=?', (current['accountId'],))
            self.problem(401, access.authenticate, db, self.headers(current))
            self.problem(401, access.login, db, {'email': 'one@example.test', 'password': 'test-passphrase-123'})

    def test_candidate_and_legacy_cookies_never_grant_employer_access(self):
        valid = self.register()
        headers = [
            {'Cookie': '__Host-hr_candidate=' + valid['token']},
            {'Cookie': '__Host-hr-vision-access=' + valid['token']},
            {'Authorization': 'Bearer manager-test-token'},
            {'Cookie': access.COOKIE + '=' + 'a' * 43},
            {'Cookie': 'malformed=\x00; ' + access.COOKIE + '="unterminated'},
            {'Cookie': 'x=' + 'a' * 9000},
            {},
        ]
        with self.connect() as db:
            for header in headers:
                with self.subTest(header_type=list(header)):
                    self.assertIsNone(access.authenticate(db, header, required=False))
                    self.problem(401, access.authenticate, db, header)

    def test_bearer_is_looked_up_in_employer_store_and_is_case_insensitive(self):
        account = self.register()
        with self.connect() as db:
            auth = access.authenticate(db, {'authorization': 'bearer ' + account['token']})
            self.assertEqual(auth['accountId'], account['accountId'])
            self.problem(401, access.authenticate, db,
                         {**self.headers(account), 'Authorization': 'Bearer invalid-explicit-token'})
            access.logout(db, {'AUTHORIZATION': 'Bearer ' + account['token']})
            self.problem(401, access.authenticate, db, self.headers(account))

    def test_client_cannot_choose_existing_workspace_or_role(self):
        original = self.register()
        for field, value in [('workspaceId', original['workspace']['id']), ('accountId', original['accountId']),
                             ('role', 'manager'), ('team', True)]:
            with self.connect() as db:
                self.problem(400, access.register, db, self.payload('two', **{field: value}))
                self.problem(400, access.login, db, {'email': 'one@example.test',
                                                     'password': 'test-passphrase-123', field: value})
                self.assertEqual(db.execute("SELECT count(*) FROM em_workspaces WHERE kind='employer'").fetchone()[0], 1)

    def test_team_access_is_explicit_not_issued_by_init_or_login(self):
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM em_sessions').fetchone()[0], 0)
            # The adapter alone authorizes this entry after checking its private grant.
            result = access.team_session(db)
            self.assertEqual(result['workspace'], {'id': 'hr-vision-team', 'name': 'HR Vision'})
            self.assertEqual(result['actorName'], 'Команда HR Vision')
            principal = access.authenticate(db, self.headers(result))
            self.assertTrue(principal['team'])
            self.assertEqual(principal['workspaceId'], access.TEAM_WORKSPACE_ID)
            self.problem(401, access.login, db, {'email': 'team@example.test', 'password': 'test-passphrase-123'})

    def test_registration_rolls_back_on_duplicate_without_orphan_workspace(self):
        original = self.register(email='  EMPLOYER@example.test  ')
        with self.connect() as db:
            self.problem(409, access.register, db, self.payload('two', email='employer@EXAMPLE.test'))
            self.assertEqual(db.execute("SELECT count(*) FROM em_workspaces WHERE kind='employer'").fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM em_sessions').fetchone()[0], 1)
            self.assertEqual(access.authenticate(db, self.headers(original))['accountId'], original['accountId'])

    def test_concurrent_registration_has_one_winner_and_no_orphan(self):
        barrier = threading.Barrier(2)
        def attempt():
            # Own connection in the worker, closed before the TemporaryDirectory.
            db = sqlite3.connect(self.db_path, timeout=10)
            db.execute('PRAGMA foreign_keys=ON')
            try:
                barrier.wait()
                with db:
                    access.register(db, self.payload())
                return 200
            except Problem as error:
                return error.status
            finally:
                db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(codes), [200, 409])
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM em_workspaces WHERE kind='employer'").fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM em_sessions').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_init_and_register_never_commit_callers_transaction(self):
        with self.connect() as db:
            db.execute('CREATE TABLE caller_changes(value TEXT)')
        db = self.connect()
        db.execute('BEGIN')
        db.execute("INSERT INTO caller_changes VALUES ('uncommitted')")
        access.init(db)
        access.register(db, self.payload())
        db.rollback()
        with self.connect() as check:
            self.assertEqual(check.execute('SELECT count(*) FROM caller_changes').fetchone()[0], 0)
            self.assertEqual(check.execute("SELECT count(*) FROM em_workspaces WHERE kind='employer'").fetchone()[0], 0)

    def test_fresh_schema_rollback_also_preserves_transaction_boundary(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('BEGIN')
        access.init(db)
        self.assertTrue(db.in_transaction)
        db.rollback()
        self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='em_workspaces'").fetchone())

    def test_idempotent_init_preserves_existing_sessions_and_disabled_state(self):
        result = self.register()
        with self.connect() as db:
            db.execute('UPDATE em_accounts SET active=0 WHERE id=?', (access.TEAM_ACCOUNT_ID,))
            before = db.execute('SELECT * FROM em_accounts ORDER BY id').fetchall()
            access.init(db)
            access.init(db)
            self.assertEqual(db.execute('SELECT * FROM em_accounts ORDER BY id').fetchall(), before)
            self.assertEqual(access.authenticate(db, self.headers(result))['accountId'], result['accountId'])
            self.problem(401, access.team_session, db)

    def test_password_and_input_bounds_do_not_create_accounts(self):
        invalid = [None, 'short', 'x' * 129, 123]
        with self.connect() as db:
            for password in invalid:
                self.problem(400, access.register, db, self.payload(password=password))
            self.problem(400, access.register, db, self.payload(email='not-email'))
            self.problem(400, access.register, db, self.payload(name=''))
            self.problem(400, access.register, db, self.payload(workspaceName=' '))
            self.problem(400, access.register, db, [])
            self.assertEqual(db.execute("SELECT count(*) FROM em_workspaces WHERE kind='employer'").fetchone()[0], 0)

    def test_cookie_has_host_bound_secure_http_only_attributes(self):
        result = self.register()
        cookie = access.session_cookie(result['token'])
        for part in ('Path=/', 'Secure', 'HttpOnly', 'SameSite=Lax', 'Max-Age=' + str(access.SESSION_SECONDS)):
            self.assertIn(part, cookie)
        self.assertNotIn('Domain=', cookie)
        self.problem(400, access.session_cookie, 'invalid\r\nSet-Cookie: injected')

    def test_failed_login_attempts_survive_credential_transaction_rollback(self):
        body = {'email': 'missing@example.test', 'password': 'wrong-password-123'}
        with patch.object(access.time, 'time', return_value=1000000):
            for _ in range(15):
                # Integration contract: durable limit transaction is separate.
                with self.connect() as db:
                    access.consume_auth_limits(db, 'login', body, '127.0.0.1')
                try:
                    with self.connect() as db:
                        access.login(db, body)
                except Problem as error:
                    self.assertEqual(error.status, 401)
            with self.connect() as db:
                self.problem(429, access.consume_auth_limits, db, 'login', body, '127.0.0.1')
                counts = db.execute('SELECT count FROM em_auth_limits').fetchall()
                self.assertEqual(counts, [(15,), (15,)])
                dump = '\n'.join(db.iterdump())
                self.assertNotIn('missing@example.test', dump)
                self.assertNotIn('127.0.0.1', dump)
                self.assertNotIn('wrong-password-123', dump)
        with self.connect() as db, patch.object(access.time, 'time', return_value=1000900):
            access.consume_auth_limits(db, 'login', body, '127.0.0.1')
            self.assertEqual(db.execute('SELECT count FROM em_auth_limits').fetchall(), [(1,), (1,)])

    def test_registration_limited_by_identity_even_when_ip_changes(self):
        with patch.object(access.time, 'time', return_value=1000000):
            for n in range(5):
                with self.connect() as db:
                    access.consume_auth_limits(db, 'register', self.payload(), '192.0.2.' + str(n))
            with self.connect() as db:
                self.problem(429, access.consume_auth_limits, db, 'register', self.payload(email='ONE@EXAMPLE.TEST'), '192.0.2.200')

    def test_ip_limit_covers_many_different_identities(self):
        with patch.object(access.time, 'time', return_value=1000000):
            for n in range(40):
                with self.connect() as db:
                    access.consume_auth_limits(db, 'login', self.payload(str(n)), '192.0.2.1')
            with self.connect() as db:
                self.problem(429, access.consume_auth_limits, db, 'login', self.payload('extra'), '192.0.2.1')

    def test_concurrent_rate_limit_never_exceeds_identity_limit(self):
        with self.connect() as db:
            for _ in range(4):
                access.consume_auth_limits(db, 'register', self.payload(), '192.0.2.1')
        barrier = threading.Barrier(2)
        def attempt():
            db = sqlite3.connect(self.db_path, timeout=10)
            try:
                barrier.wait()
                with db:
                    access.consume_auth_limits(db, 'register', self.payload(), '192.0.2.1')
                return 200
            except Problem as error:
                return error.status
            finally:
                db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(codes), [200, 429])
        with self.connect() as db:
            self.assertEqual(sorted(row[0] for row in db.execute('SELECT count FROM em_auth_limits')), [5, 5])


if __name__ == '__main__':
    unittest.main()
