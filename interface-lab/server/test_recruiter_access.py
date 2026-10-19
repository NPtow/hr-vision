"""Recruiter identity and assignment boundaries against isolated SQLite.

No real invitations, people, HH calls, or delivery providers are used.
"""
import datetime as dt
import base64
import hashlib
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from candidate_portal import CandidatePortal
from domain import Problem, iso
import recruiter_access as access
import employer_access
import test_candidate_portal as fixture
import test_candidate_portal_http as http_fixture


class RecruiterAccessTest(unittest.TestCase):
    setUp = fixture.CandidatePortalTest.setUp
    tearDown = fixture.CandidatePortalTest.tearDown
    connect = fixture.CandidatePortalTest.connect
    payload = fixture.CandidatePortalTest.payload
    register_candidate = fixture.CandidatePortalTest.register
    call_candidate = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)

    # The candidate fixture's registration helper uses self.call.
    call = fixture.CandidatePortalTest.call

    def request(self, path, body=None, auth=None, *, team=False, manager=False, method=None, **headers):
        if auth:
            headers.setdefault('Cookie', auth['cookie'])
        return self.portal.handle(method or ('GET' if body is None else 'POST'), '/api/hr/recruiter/' + path,
            {'Content-Type': 'application/json', **headers}, body, '127.0.0.1',
            lambda db: 'manager' if manager else self.not_manager(), team_grant=team)

    def provision(self, suffix='a', name='Одинаковое имя'):
        result, headers = self.request('accounts', {'name': name, 'email': suffix + '@example.test'}, team=True)
        self.assertNotIn('Set-Cookie', headers)
        return result, parse_qs(urlsplit(result['activationUrl']).query)['recruiterActivate'][0]

    def recruiter(self, suffix='a', name='Одинаковое имя'):
        prepared, token = self.provision(suffix, name)
        result, headers = self.request('auth/activate', {'token': token, 'password': 'recruiter-password-123'})
        self.assertEqual(prepared['recruiter']['id'], result['recruiter']['id'])
        return {**result, 'cookie': headers['Set-Cookie'].split(';')[0]}

    def expect(self, status, path, body=None, auth=None, **kwargs):
        with self.assertRaises(Problem) as raised:
            self.request(path, body, auth, **kwargs)
        self.assertEqual(raised.exception.status, status, raised.exception.message)

    def slot(self, auth, minute=0):
        result, _ = self.request('slots', {'start': iso(self.tick + dt.timedelta(minutes=minute)),
                                         'recruiterName': 'Неправильное имя'}, auth)
        return next(row for row in result['slots'] if row['start'] == iso(self.tick + dt.timedelta(minutes=minute)))

    def book(self, person, cookie, slot):
        return self.call('candidate/book', {'slotId': slot['id']}, cookie)

    def assign_fixture(self, person, slot):
        # Ownership/edit tests need a known historical assignment, independent
        # of public load balancing (covered by test_interview_scheduler).
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.portal.book(db, person['id'], {'slotId': slot['id']})

    def state(self, auth):
        return self.request('state', auth=auth)[0]

    def test_team_provisions_and_invitation_activates_without_creator_impersonation(self):
        self.expect(403, 'accounts', {'name': 'Рекрутер', 'email': 'nobody@example.test'})
        result, token = self.provision()
        self.assertEqual(result['recruiter']['email'], 'a@example.test')
        self.assertFalse(self.request('auth/session')[0]['authenticated'])
        self.assertTrue(self.request('auth/session', team=True)[0]['canManageAccounts'])
        self.expect(401, 'auth/login', {'email': 'a@example.test', 'password': 'recruiter-password-123'})
        with self.connect() as db:
            row = db.execute('SELECT active,password_hash FROM rc_accounts').fetchone()
            self.assertEqual(row, (0, ''))
            self.assertNotIn(token, str(db.execute('SELECT * FROM rc_activations').fetchall()))
        active, headers = self.request('auth/activate', {'token': token, 'password': 'recruiter-password-123'})
        self.assertTrue(active['authenticated'])
        self.assertFalse(active['canManageAccounts'])
        self.assertIn('Secure; HttpOnly; SameSite=Lax', headers['Set-Cookie'])
        self.assertNotEqual(active['token'], token)
        self.expect(404, 'auth/activate', {'token': token, 'password': 'another-password-123'})
        with self.connect() as db:
            row = db.execute('SELECT active,password_hash FROM rc_accounts').fetchone()
            self.assertEqual(row[0], 1)
            self.assertTrue(row[1].startswith('scrypt$'))
            self.assertNotIn(active['token'], str(db.execute('SELECT * FROM rc_sessions').fetchall()))

    def test_expired_reissued_or_other_role_invites_cannot_activate(self):
        _, old = self.provision()
        _, fresh = self.provision()
        self.expect(404, 'auth/activate', {'token': old, 'password': 'recruiter-password-123'})
        with self.connect() as db:
            db.execute('UPDATE rc_activations SET expires=?', (int(time.time()) - 1,))
        self.expect(404, 'auth/activate', {'token': fresh, 'password': 'recruiter-password-123'})
        self.expect(404, 'auth/activate', {'token': 'em_' + 'a' * 43, 'password': 'recruiter-password-123'})

    def test_cookie_bearer_login_restore_and_single_session_logout(self):
        first = self.recruiter()
        restored, _ = self.request('auth/session', auth=first)
        self.assertEqual(restored['token'], first['token'])
        bearer, _ = self.request('auth/session', Authorization='Bearer ' + first['token'])
        self.assertEqual(bearer['recruiter']['id'], first['recruiter']['id'])
        second, _ = self.request('auth/login', {'email': 'A@EXAMPLE.TEST', 'password': 'recruiter-password-123'})
        self.request('auth/logout', {}, first)
        self.assertFalse(self.request('auth/session', auth=first)[0]['authenticated'])
        self.assertTrue(self.request('auth/session', Authorization='Bearer ' + second['token'])[0]['authenticated'])
        self.expect(401, 'state', auth=first, manager=True)

    def test_candidate_employer_and_invalid_credentials_never_become_recruiter(self):
        recruiter = self.recruiter()
        _, candidate_cookie = self.register_candidate()
        self.assertFalse(self.request('auth/session', Cookie=candidate_cookie)[0]['authenticated'])
        self.expect(401, 'state', Cookie=candidate_cookie)
        self.expect(403, 'accounts', {'name': 'Hacker', 'email': 'hacker@example.test'}, Cookie=candidate_cookie)
        with self.connect() as db:
            employer_access.init(db)
            employer = employer_access.register(db, {'email': 'employer@example.test', 'name': 'Компания',
                                                    'password': 'employer-password-123'})
        self.expect(401, 'state', Authorization='Bearer ' + employer['token'])
        invalid, _ = self.request('auth/session', auth=recruiter, Authorization='Bearer ' + employer['token'])
        self.assertFalse(invalid['authenticated'])
        self.expect(401, 'auth/login', {'email': 'a@example.test', 'password': 'wrong-password-123'})
        self.expect(401, 'auth/login', {'email': 'unknown@example.test', 'password': 'wrong-password-123'})
        self.expect(400, 'auth/register', {'email': 'x@example.test', 'name': 'X', 'password': 'test-password-123',
                                         'role': 'manager'}, team=True)

    def test_auth_rate_counter_survives_failed_password_and_origin_rejected(self):
        self.recruiter()
        for _ in range(15):
            self.expect(401, 'auth/login', {'email': 'a@example.test', 'password': 'wrong-password-123'})
        self.expect(429, 'auth/login', {'email': 'a@example.test', 'password': 'recruiter-password-123'})
        self.expect(403, 'auth/login', {'email': 'a@example.test', 'password': 'recruiter-password-123'},
                    Origin='https://attacker.example')
        self.expect(403, 'accounts', {'name': 'X', 'email': 'x@example.test'}, team=True, **{'Sec-Fetch-Site': 'cross-site'})

    def test_same_name_accounts_keep_distinct_schedules_and_legacy_is_unclaimed(self):
        one, two = self.recruiter('one'), self.recruiter('two')
        first, second = self.slot(one), self.slot(two)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['recruiterName'], one['recruiter']['name'])
        self.call('recruiter/slots', {'recruiterName': 'Одинаковое имя',
                                    'start': iso(self.tick + dt.timedelta(hours=2))}, manager=True)
        self.assertEqual([slot['id'] for slot in self.state(one)['slots']], [first['id']])
        self.assertEqual([slot['id'] for slot in self.state(two)['slots']], [second['id']])
        with self.connect() as db:
            self.assertEqual(len(self.portal.slots(db)), 2)
        self.request('auth/profile', {'name': 'Новое имя'}, one)
        self.assertEqual(self.state(one)['slots'][0]['recruiterName'], 'Новое имя')
        self.assertEqual(self.state(two)['slots'][0]['recruiterName'], 'Одинаковое имя')

    def test_recurring_blocks_generate_owned_slots_and_ignore_client_name(self):
        one, two = self.recruiter('one'), self.recruiter('two')
        body = {'recruiterName': 'Чужое имя', 'startDate': (self.tick + dt.timedelta(days=1)).date().isoformat(),
                'weekdays': [0, 1, 2, 3, 4], 'startTime': '16:00', 'endTime': '17:00', 'timezone': 'Europe/Moscow'}
        first, _ = self.request('availability', body, one)
        second, _ = self.request('availability', body, two)
        self.assertEqual(len(first['availabilityBlocks']), 1)
        self.assertTrue(first['slots'])
        self.assertTrue(set(row['id'] for row in first['slots']).isdisjoint(row['id'] for row in second['slots']))
        self.assertEqual(first['availabilityBlocks'][0]['recruiterName'], one['recruiter']['name'])
        self.request('auth/profile', {'name': 'Переименован'}, one)
        self.expect(409, 'availability', {**body, 'startTime': '16:30', 'endTime': '17:30'}, one)

    def test_availability_edit_uses_account_owner_and_preserves_booked_interview(self):
        one, two = self.recruiter('one'), self.recruiter('two')
        body = {'recruiterName': 'Имя клиента не определяет владельца',
                'startDate': (self.tick + dt.timedelta(days=1)).date().isoformat(),
                'weekdays': list(range(7)), 'startTime': '16:00', 'endTime': '17:00',
                'timezone': 'Europe/Moscow'}
        initial, _ = self.request('availability', body, one)
        other, _ = self.request('availability', body, two)
        block = initial['availabilityBlocks'][0]
        booked = initial['slots'][0]
        person, cookie = self.register_candidate()
        self.assign_fixture(person, booked)
        update = {**body, 'blockId': block['id'], 'startTime': '18:00', 'endTime': '19:00'}
        self.expect(404, 'availability', update, two)
        self.expect(401, 'availability', update, manager=True)
        edited, _ = self.request('availability', update, one)
        self.assertEqual(edited['availabilityBlocks'][0]['id'], block['id'])
        self.assertEqual(len(edited['availabilityBlocks']), 1)
        self.assertEqual(edited['candidates'][0]['id'], person['id'])
        self.assertEqual(next(slot for slot in edited['slots'] if slot['id'] == booked['id'])['candidateId'], person['id'])
        self.assertEqual(self.state(two)['slots'], other['slots'])
        public, _ = self.call('candidate/slots', cookie=cookie)
        retired = {slot['id'] for slot in initial['slots']}
        self.assertTrue(retired.isdisjoint(slot['id'] for slot in public['slots']))
        repeated, _ = self.request('availability', update, one)
        self.assertEqual(repeated['slots'], edited['slots'])
        self.assertEqual(repeated['availabilityBlocks'], edited['availabilityBlocks'])
        # A second existing rule causes a 409 without a partial edit or slot loss.
        self.request('availability', {**body, 'startTime': '20:00', 'endTime': '21:00'}, one)
        before_conflict = self.state(one)
        self.expect(409, 'availability', {**update, 'startTime': '20:00', 'endTime': '21:00'}, one)
        self.assertEqual(self.state(one), before_conflict)

    def test_only_booked_interviews_appear_and_foreign_ids_are_rejected(self):
        one, two = self.recruiter('one'), self.recruiter('two')
        person, cookie = self.register_candidate()
        unscheduled, _ = self.register_candidate()
        mine, theirs = self.slot(one), self.slot(two)
        self.assertEqual(self.state(one)['candidates'], [])
        self.assign_fixture(person, mine)
        self.assertEqual([p['id'] for p in self.state(one)['candidates']], [person['id']])
        self.assertEqual(self.state(two)['candidates'], [])
        for route, extra in [('profile', {'summary': 'x'}), ('invitations', {'scope': 'claim'}),
                             ('interview/join', {'consent': True}), ('interview/materials', {}),
                             ('interview/complete', {}), ('interview/start', {'oralConsent': True})]:
            with self.subTest(route=route):
                self.expect(404, route, {'candidateId': person['id'], **extra}, two)
        self.expect(404, 'profile', {'candidateId': unscheduled['id'], 'summary': 'x'}, one)
        self.expect(403, 'vacancies', {'title': 'X'}, one)
        self.expect(403, 'meetings', {'applicationId': 'invented'}, one)
        self.assertFalse(self.media.rooms)
        self.assertEqual(self.state(one)['candidates'][0]['source']['kind'], 'registration')

    def test_personal_date_overrides_filter_pool_and_route_stale_time_to_available_owner(self):
        from recruiter_availability import MOSCOW
        one, two = self.recruiter('date-one'), self.recruiter('date-two')
        day = (self.tick + dt.timedelta(days=1)).astimezone(MOSCOW).date().isoformat()
        body = {'date': day, 'intervals': [{'startTime': '16:00', 'endTime': '17:00'}]}
        initial, _ = self.request('availability/date', body, one)
        other, _ = self.request('availability/date', body, two)
        self.assertEqual(len(initial['slots']), 2)
        self.assertEqual(initial['availabilityDates'], [body])
        person, cookie = self.register_candidate()
        booked, unused = initial['slots']
        self.assign_fixture(person, booked)
        closed, _ = self.request('availability/date', {'date': day, 'intervals': []}, one)
        self.assertEqual([slot['id'] for slot in closed['slots']], [booked['id']])
        self.assertEqual(closed['candidates'][0]['id'], person['id'])
        public, _ = self.call('candidate/slots', cookie=cookie)
        self.assertEqual({(slot['start'], slot['end']) for slot in public['slots']},
                         {(slot['start'], slot['end']) for slot in other['slots']})
        new_person, new_cookie = self.register_candidate()
        self.book(new_person, new_cookie, unused)
        self.assertEqual([p['id'] for p in self.state(two)['candidates']], [new_person['id']])
        self.book(person, cookie, booked)  # existing commitment remains idempotent
        self.expect(400, 'availability/date', {**body, 'recruiterId': two['recruiter']['id']}, one)
        self.expect(401, 'availability/date', body, manager=True)
        reopened, _ = self.request('availability/date', body, one)
        self.assertEqual(len(reopened['slots']), 2)
        self.book(new_person, new_cookie, unused)
        self.assertEqual(len(self.state(one)['candidates']), 1)
        self.assertEqual(len(self.state(two)['candidates']), 1)

    def test_session_revoked_during_provider_io_does_not_issue_room_token(self):
        recruiter = self.recruiter()
        person, cookie = self.register_candidate()
        self.book(person, cookie, self.slot(recruiter))
        original = self.media.room
        def revoke(mid, **kwargs):
            with self.connect() as db:
                access.logout(db, {'Cookie': recruiter['cookie']})
            return original(mid, **kwargs)
        with patch.object(self.media, 'room', side_effect=revoke):
            self.expect(401, 'interview/join', {'candidateId': person['id'], 'consent': True}, recruiter)
        self.assertEqual(self.media.tokens, [])
        with self.connect() as db:
            self.assertEqual(self.portal.interview(db, person['id'])['status'], 'booked')

    def test_disabled_recruiter_slots_leave_pool_and_booking_fails(self):
        recruiter = self.recruiter()
        person, cookie = self.register_candidate()
        slot = self.slot(recruiter)
        with self.connect() as db:
            db.execute('UPDATE rc_accounts SET active=0 WHERE id=?', (recruiter['recruiter']['id'],))
        slots, _ = self.call('candidate/slots', cookie=cookie)
        self.assertNotIn(slot['id'], [row['id'] for row in slots['slots']])
        with self.assertRaises(Problem) as raised:
            self.book(person, cookie, slot)
        self.assertEqual(raised.exception.status, 409)
        self.expect(401, 'state', auth=recruiter)

    def test_new_recruiter_cannot_create_accounts_without_team_authority(self):
        recruiter = self.recruiter()
        self.expect(403, 'accounts', {'name': 'X', 'email': 'x@example.test'}, recruiter)
        self.expect(403, 'auth/register', {'name': 'X', 'email': 'x@example.test', 'password': 'password-123'}, recruiter)
        self.expect(400, 'auth/profile', {'name': 'X', 'id': 'another'}, recruiter)

    def test_prepared_cv_belongs_to_creator_and_can_only_book_their_slot(self):
        one, two = self.recruiter('one'), self.recruiter('two')
        first, second = self.slot(one), self.slot(two)
        resume = 'Иван Тестовый\nМосква\nМенеджер по продажам\n' + 'Развивал продажи, готовил предложения и проводил переговоры. ' * 8
        result, _ = self.request('candidates', {'filename': 'resume.txt',
            'contentBase64': base64.b64encode(resume.encode()).decode()}, one)
        cid = result['candidate']['id']
        self.assertEqual(result['state']['candidates'], [])
        self.expect(404, 'interview/book', {'candidateId': cid, 'slotId': second['id']}, two)
        self.expect(404, 'interview/book', {'candidateId': cid, 'slotId': second['id']}, one)
        state, _ = self.request('interview/book', {'candidateId': cid, 'slotId': first['id']}, one)
        self.assertEqual([row['id'] for row in state['candidates']], [cid])
        self.assertEqual(self.state(two)['candidates'], [])

    def test_personal_projection_omits_employer_workflow_and_candidate_channels(self):
        recruiter = self.recruiter()
        person, cookie = self.register_candidate()
        self.book(person, cookie, self.slot(recruiter))
        # Employer/private projection must never even run when rendering a
        # personal recruiter's primary interview agenda.
        with patch('candidate_portal.employer_workflow.load', side_effect=AssertionError('private workflow read')):
            candidate = self.state(recruiter)['candidates'][0]
        self.assertEqual(candidate['applications'], [])
        self.assertEqual(candidate['channels'], {'email': False, 'telegram': False})

    def test_notification_status_is_for_current_identity_and_cannot_select_recipient(self):
        one, two = self.recruiter('one'), self.recruiter('two')
        with patch.dict('os.environ', {'HR_NOTIFICATIONS_ENABLED': '0'}):
            result, _ = self.request('notifications', auth=one)
            self.assertEqual(result['email']['address'], 'o***@example.test')
            self.expect(400, 'notifications/email/request', {'recruiterId': two['recruiter']['id']}, one)

    def test_telegram_disconnect_is_personal_and_rejects_recipient_override(self):
        one, two = self.recruiter('one'), self.recruiter('two')
        self.request('notifications', auth=one)
        self.request('notifications', auth=two)
        with self.connect() as db:
            db.execute("UPDATE rn_channels SET telegram_chat_id='101',telegram_verified_at=1 WHERE recruiter_id=?", (one['recruiter']['id'],))
            db.execute("UPDATE rn_channels SET telegram_chat_id='202',telegram_verified_at=1 WHERE recruiter_id=?", (two['recruiter']['id'],))
        self.expect(400, 'notifications/telegram/disconnect', {'recruiterId': two['recruiter']['id']}, one)
        self.expect(401, 'notifications/telegram/disconnect', {})
        response, _ = self.request('notifications/telegram/disconnect', {}, one)
        self.assertEqual(response, {'linked': False})
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT telegram_chat_id FROM rn_channels WHERE recruiter_id=?', (one['recruiter']['id'],)).fetchone()[0])
            self.assertEqual(db.execute('SELECT telegram_chat_id FROM rn_channels WHERE recruiter_id=?', (two['recruiter']['id'],)).fetchone()[0], '202')

    def test_unowned_legacy_slot_hidden_and_unbookable_without_erasing_history(self):
        person, cookie = self.register_candidate()
        state, _ = self.call('recruiter/slots', {'recruiterName': 'Старое расписание', 'start': iso(self.tick)}, manager=True)
        slot = state['slots'][0]
        self.assertEqual(self.call('candidate/slots', cookie=cookie)[0]['slots'], [])
        with self.assertRaises(Problem) as raised:
            self.book(person, cookie, slot)
        self.assertEqual(raised.exception.status, 409)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cp_interviews').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM cp_recruiter_slots').fetchone()[0], 1)
            db.execute('UPDATE cp_recruiter_slots SET candidate_id=? WHERE id=?', (person['id'], slot['id']))
            db.execute("INSERT INTO cp_interviews VALUES ('historical',?,?,'booked',NULL,?,NULL)",
                       (person['id'], slot['id'], iso(self.tick)))
        self.book(person, cookie, slot)  # existing legacy commitment remains idempotent
        self.assertEqual(self.call('candidate/state', cookie=cookie)[0]['interview']['id'], 'historical')


class RecruiterHTTPTest(unittest.TestCase):
    setUpClass = classmethod(http_fixture.CandidateHTTPTest.setUpClass.__func__)
    tearDownClass = classmethod(http_fixture.CandidateHTTPTest.tearDownClass.__func__)
    request = http_fixture.CandidateHTTPTest.request

    def test_verified_grant_provisions_activation_cookie_restores_and_logout_revokes(self):
        payload = {'name': 'HTTP Recruiter', 'email': 'http-recruiter@example.test'}
        self.assertEqual(self.request('recruiter/accounts', {**payload, 'team_grant': True})[0], 403)
        status, _, headers = self.request('access', {'teamKey': 'http-test-team-only'})
        self.assertEqual(status, 200)
        team_cookie = headers['Set-Cookie'].split(';')[0]
        status, prepared, headers = self.request('recruiter/accounts', payload, cookie=team_cookie)
        self.assertEqual(status, 200, prepared)
        self.assertNotIn('Set-Cookie', headers)
        token = parse_qs(urlsplit(prepared['activationUrl']).query)['recruiterActivate'][0]
        status, active, headers = self.request('recruiter/auth/activate', {'token': token, 'password': 'http-password-123'})
        self.assertEqual(status, 200, active)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertEqual(self.request('recruiter/auth/session', cookie=cookie)[1]['recruiter'], active['recruiter'])
        self.assertEqual(self.request('recruiter/state', cookie=cookie)[1]['candidates'], [])
        self.assertEqual(self.request('recruiter/hh/state', cookie=cookie)[0], 401)
        self.assertEqual(self.request('employer/session?personal=1', cookie=cookie)[0], 401)
        status, _, _ = self.request('recruiter/auth/logout', {}, cookie=cookie,
                                    extra_headers={'Origin': 'https://attacker.example'})
        self.assertEqual(status, 403)
        self.assertEqual(self.request('recruiter/auth/logout', {}, cookie=cookie)[0], 200)
        self.assertFalse(self.request('recruiter/auth/session', cookie=cookie)[1]['authenticated'])
        self.assertEqual(self.request('recruiter/state', token=active['token'])[0], 401)


if __name__ == '__main__':
    unittest.main()
