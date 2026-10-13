"""Isolated identity/flow/concurrency checks. No provider calls or real recipients."""
import concurrent.futures
import base64
import datetime as dt
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from candidate_portal import CandidatePortal, COOKIE, ClosingConnection, dump
from domain import Problem, iso, now
from daily_media import DailyMedia
import recruiter_access


def assign_fixture_slot(db, slot):
    """Give synthetic public-booking slots an explicit test recruiter owner."""
    identifier = 'fixture-recruiter-' + hashlib.sha256(slot['recruiterName'].encode()).hexdigest()[:16]
    db.execute('''INSERT OR IGNORE INTO rc_accounts VALUES (?,?,?,?,1,?,?)''',
               (identifier, identifier + '@example.test', recruiter_access._DUMMY_HASH,
                slot['recruiterName'], iso(now()), iso(now())))
    db.execute('INSERT OR IGNORE INTO cp_recruiter_slot_owners VALUES (?,?)', (slot['id'], identifier))


class FakeMedia(DailyMedia):
    configured = True

    def __init__(self):
        self.tokens, self.finished = [], []
        self.rooms = []

    def room(self, mid, *, recordable=True):
        self.rooms.append(mid)
        return {'id': mid, 'name': 'hrv-' + mid, 'url': 'https://test.daily.co/hrv-' + mid}

    def request(self, path, body=None):
        if path == '/meeting-tokens':
            self.tokens.append(body['properties'])
            return {'token': 'test-only-provider-token'}
        return {'data': []}

    def finish(self, room):
        self.finished.append(room['id'])


class FakeNotifications:
    @staticmethod
    def init(db):
        db.execute('CREATE TABLE IF NOT EXISTS fake_events (cid TEXT,key TEXT UNIQUE,type TEXT)')

    @staticmethod
    def set_email(db, cid, email, verified=False):
        assert not verified

    @staticmethod
    def enqueue(db, cid, key, event_type):
        db.execute('INSERT OR IGNORE INTO fake_events VALUES (?,?,?)', (cid, key, event_type))

    @staticmethod
    def configuration_public():
        return {'enabled': False, 'emailConfigured': False, 'telegramConfigured': False}

    @staticmethod
    def public_status(db, cid):
        return {'email': {'configured': False, 'verified': False}, 'telegram': {'configured': False, 'linked': False}}


class CandidatePortalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hr-candidate-unit-')
        self.db_path = Path(self.tmp.name) / 'state.sqlite3'
        self.media = FakeMedia()
        self.portal = CandidatePortal(self.connect, threading.RLock(), self.media,
                                      'https://portal.example', FakeNotifications)
        self.tick = now().replace(microsecond=0) + dt.timedelta(minutes=5)
        self.count = 0

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10, factory=ClosingConnection)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        return db

    def payload(self, **changes):
        self.count += 1
        return {'email': f'person{self.count}@example.test', 'password': 'test-passphrase-123',
                'name': 'Кандидат', 'city': 'Москва', 'desiredRole': 'Менеджер',
                'geographies': ['Москва'], 'availability': [{'start': iso(self.tick),
                  'end': iso(self.tick + dt.timedelta(hours=3))}], 'consentReuse': True, **changes}

    def call(self, path, body=None, cookie='', manager=False, method=None, **headers):
        return self.portal.handle(method or ('GET' if body is None else 'POST'), '/api/hr/' + path,
            {'Content-Type': 'application/json', 'Cookie': cookie, **headers}, body, '127.0.0.1',
            lambda db: 'manager' if manager else self.not_manager())

    @staticmethod
    def not_manager():
        raise Problem('Нет доступа.', 401)

    def register(self, **changes):
        result, headers = self.call('candidate/register', self.payload(**changes))
        cookie = headers['Set-Cookie'].split(';')[0]
        resume = ('Желаемая должность: Менеджер\nГород: Москва\n'
                  'Опыт работы: пять лет развивал продажи и поддерживал отношения с клиентами. '
                  'Проводил переговоры, готовил коммерческие предложения, обучал новых коллег и работал в CRM. '
                  'Образование: высшее экономическое. Навыки: планирование, коммуникация и анализ данных.')
        result, _ = self.call('candidate/resume', {'filename': 'resume.txt',
            'contentBase64': base64.b64encode(resume.encode()).decode()}, cookie)
        result, _ = self.call('candidate/profile', {'salaryMin': 100000, 'salaryMax': 150000,
            'latestStartDate': (self.tick + dt.timedelta(days=14)).date().isoformat(), 'resumeReviewed': True}, cookie)
        return result, cookie

    def slot(self, minutes=0, recruiter='Рекрутер'):
        state, _ = self.call('recruiter/slots', {'start': iso(self.tick + dt.timedelta(minutes=minutes)),
                                                 'recruiterName': recruiter}, manager=True)
        slot = next(slot for slot in state['slots'] if slot['start'] == iso(self.tick + dt.timedelta(minutes=minutes))
                    and slot['recruiterName'] == recruiter)
        with self.connect() as db:
            assign_fixture_slot(db, slot)
        return slot

    def completed_person(self, **changes):
        person, cookie = self.register(**changes)
        slot = self.slot(recruiter=person['id'])
        self.call('candidate/book', {'slotId': slot['id']}, cookie)
        self.call('recruiter/interview/join', {'candidateId': person['id'], 'consent': True}, manager=True)
        self.call('recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        return person, cookie

    def legacy_auto_person(self):
        person, cookie = self.completed_person(autoApprove=True)
        # Historical completed accounts did not have structured salary expectations.
        # Keep testing their opt-in behaviour without weakening the new booking gate.
        with self.connect() as db:
            row = db.execute('SELECT profile FROM cp_candidates WHERE id=?', (person['id'],)).fetchone()
            profile = json.loads(row[0])
            profile.pop('salaryMin', None)
            profile.pop('salaryMax', None)
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (dump(profile), person['id']))
        return person, cookie

    def vacancy(self, **changes):
        state, _ = self.call('recruiter/vacancies', {'title': 'Менеджер', 'company': 'Компания',
             'city': 'Москва', 'remote': False, 'description': 'Работа с клиентами', **changes}, manager=True)
        return state['vacancies'][0]

    def propose(self, person, vacancy):
        state, _ = self.call('recruiter/applications', {'candidateId': person['id'], 'vacancyId': vacancy['id']}, manager=True)
        return next(p for p in state['candidates'] if p['id'] == person['id'])['applications'][0]

    def assert_problem(self, status, *args, **kwargs):
        with self.assertRaises(Problem) as caught:
            self.call(*args, **kwargs)
        self.assertEqual(caught.exception.status, status, caught.exception.message)

    def test_registration_is_personal_and_auto_off_by_default(self):
        person, cookie = self.register()
        self.assertFalse(person['autoApprove'])
        self.assertEqual(person['applications'], [])
        self.assertIsNone(person['interview'])
        self.assertNotIn('internalProfile', person)
        self.assertNotIn('password', dump(person))
        self.assertTrue(cookie.startswith(COOKIE + '='))
        with self.connect() as db:
            row = db.execute('SELECT password_hash FROM cp_candidates').fetchone()
            self.assertTrue(row[0].startswith('scrypt$'))
            self.assertNotIn('test-passphrase', row[0])
            self.assertEqual(db.execute('SELECT auto_approve FROM cp_consent_log').fetchone()[0], 0)

    def test_secure_cookie_logout_and_login_persist_identity(self):
        payload = self.payload()
        person, headers = self.call('candidate/register', payload)
        cookie = headers['Set-Cookie'].split(';')[0]
        for flag in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertEqual(self.call('candidate/state', cookie=cookie)[0]['id'], person['id'])
        self.call('candidate/logout', {}, cookie)
        self.assert_problem(401, 'candidate/state', cookie=cookie)
        logged, _ = self.call('candidate/login', {'email': payload['email'].upper(), 'password': payload['password']})
        self.assertEqual(logged['id'], person['id'])
        self.assert_problem(401, 'candidate/login', {'email': payload['email'], 'password': 'wrong-password-here'})

    def test_duplicate_email_never_links_legacy_or_creates_second_identity(self):
        payload = self.payload(email='existing@example.test')
        person, _ = self.call('candidate/register', payload)
        self.assert_problem(409, 'candidate/register', {**payload, 'name': 'Другой'})
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0], 1)
        self.assert_problem(400, 'candidate/register', self.payload(actor='manager'))
        self.assert_problem(401, 'candidate/state', Authorization='Bearer legacy-test-candidate')

    def test_consent_required_and_auto_requires_consent(self):
        self.assert_problem(400, 'candidate/register', self.payload(consentReuse=False))
        self.assert_problem(400, 'candidate/register', self.payload(consentReuse=False, autoApprove=True))
        person, cookie = self.register(autoApprove=True)
        self.assertTrue(person['autoApprove'])
        state, _ = self.call('candidate/profile', {'consentReuse': False, 'autoApprove': False}, cookie)
        self.assertFalse(state['consentReuse'])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_consent_log').fetchone()[0], 2)

    def test_profile_edits_cannot_mutate_internal_dossier_or_email(self):
        person, cookie = self.register()
        self.call('recruiter/profile', {'candidateId': person['id'], 'summary': 'Private summary', 'evaluation': 'Private evaluation'}, manager=True)
        state, _ = self.call('candidate/profile', {'about': 'Моя краткая информация'}, cookie)
        self.assertNotIn('internalProfile', state)
        self.assertNotIn('Private', dump(state))
        self.assert_problem(400, 'candidate/profile', {'internalProfile': {'summary': 'overwrite'}}, cookie)
        self.assert_problem(400, 'candidate/profile', {'email': 'someone@example.test'}, cookie)
        internal = self.call('recruiter/state', manager=True)[0]['candidates'][0]['internalProfile']
        self.assertEqual(internal['summary'], 'Private summary')

    def test_empty_calendar_and_atomic_slot_booking(self):
        a, ca = self.register()
        b, cb = self.register()
        self.assertEqual(self.call('candidate/slots', cookie=ca)[0]['slots'], [])
        slot = self.slot()
        barrier = threading.Barrier(2)
        def book(cookie):
            barrier.wait()
            try:
                self.call('candidate/book', {'slotId': slot['id']}, cookie)
                return 200
            except Problem as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(book, cookie) for cookie in (ca, cb)]
            self.assertEqual(sorted(f.result() for f in futures), [200, 409])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_interviews').fetchone()[0], 1)
        self.assertEqual(self.call('candidate/slots', cookie=ca)[0]['slots'], [])

    def test_booking_retry_idempotent_and_second_booking_rejected(self):
        p, cookie = self.register()
        first = self.slot()
        self.call('candidate/book', {'slotId': first['id']}, cookie)
        self.call('candidate/book', {'slotId': first['id']}, cookie)
        second = self.slot(minutes=60)
        self.assert_problem(409, 'candidate/book', {'slotId': second['id']}, cookie)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM fake_events WHERE type='interview.booked'").fetchone()[0], 1)

    def test_overlapping_recruiter_slots_rejected(self):
        self.slot()
        self.assert_problem(409, 'recruiter/slots', {'start': iso(self.tick + dt.timedelta(minutes=15)), 'recruiterName': 'РЕКРУТЕР'}, manager=True)
        self.slot(minutes=15, recruiter='Другой рекрутер')

    def test_candidate_cannot_complete_or_access_recruiter(self):
        p, cookie = self.register()
        self.assert_problem(401, 'recruiter/state', cookie=cookie)
        self.assert_problem(404, 'candidate/interview/complete', {'candidateId': p['id']}, cookie)
        self.assert_problem(404, 'recruiter/interview/complete', {'candidateId': p['id']}, manager=True)

    def test_human_join_consent_owner_and_completion(self):
        p, cookie = self.register()
        slot = self.slot()
        self.call('candidate/book', {'slotId': slot['id']}, cookie)
        self.assert_problem(400, 'candidate/interview/join', {'consent': False}, cookie)
        self.call('candidate/interview/join', {'consent': True}, cookie)
        self.assertFalse(self.media.tokens[-1]['is_owner'])
        self.assertEqual(self.call('candidate/state', cookie=cookie)[0]['interview']['status'], 'booked')
        self.assert_problem(409, 'recruiter/interview/complete', {'candidateId': p['id']}, manager=True)
        self.call('recruiter/interview/join', {'candidateId': p['id'], 'consent': True}, manager=True)
        self.assertTrue(self.media.tokens[-1]['is_owner'])
        self.call('recruiter/interview/complete', {'candidateId': p['id']}, manager=True)
        self.assertEqual(self.call('candidate/state', cookie=cookie)[0]['interview']['status'], 'completed')
        self.assertEqual(len(self.media.finished), 1)
        self.assert_problem(409, 'candidate/interview/join', {'consent': True}, cookie)

    def test_materials_pending_never_fabricates_recording(self):
        p, _ = self.completed_person()
        state, _ = self.call('recruiter/interview/materials', {'candidateId': p['id']}, manager=True)
        self.assertEqual(state['status'], 'processing')
        self.assertEqual(state['recordings'], [])
        self.assertEqual(state['transcripts'], [])

    def test_concurrent_join_cannot_reopen_room_while_it_is_finishing(self):
        person, cookie = self.register()
        slot = self.slot()
        self.call('candidate/book', {'slotId': slot['id']}, cookie)
        self.call('recruiter/interview/join', {'candidateId': person['id'], 'consent': True}, manager=True)
        closing, release, joining = threading.Event(), threading.Event(), threading.Event()
        def finish(room):
            closing.set()
            self.assertTrue(release.wait(3))
            self.media.finished.append(room['id'])
        self.media.finish = finish
        def join():
            joining.set()
            try:
                self.call('candidate/interview/join', {'consent': True}, cookie)
                return 200
            except Problem as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            finishing = pool.submit(self.call, 'recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
            self.assertTrue(closing.wait(3))
            pending = pool.submit(join)
            self.assertTrue(joining.wait(3))
            try:
                with self.assertRaises(concurrent.futures.TimeoutError):
                    pending.result(timeout=0.05)
                self.assertEqual(len(self.media.rooms), 1)
            finally:
                release.set()
            finishing.result(timeout=3)
            self.assertEqual(pending.result(timeout=3), 409)
        self.assertEqual(len(self.media.rooms), 1)
        self.assertEqual(self.portal._media_locks, {})

    def test_vacancy_requires_interview_and_current_consent(self):
        p, cookie = self.register()
        vacancy = self.vacancy()
        self.assert_problem(409, 'recruiter/applications', {'candidateId': p['id'], 'vacancyId': vacancy['id']}, manager=True)
        p2, cookie2 = self.completed_person()
        self.call('candidate/profile', {'consentReuse': False, 'autoApprove': False}, cookie2)
        self.assert_problem(409, 'recruiter/applications', {'candidateId': p2['id'], 'vacancyId': vacancy['id']}, manager=True)

    def test_manual_vacancy_approval_and_time_always_separate(self):
        p, cookie = self.completed_person()
        app = self.propose(p, self.vacancy())
        self.assertEqual(app['status'], 'pending')
        self.assertFalse(app['approvedAutomatically'])
        self.assert_problem(409, 'recruiter/meetings', {'applicationId': app['id'], 'start': iso(self.tick + dt.timedelta(hours=1))}, manager=True)
        self.call('candidate/application/decision', {'applicationId': app['id'], 'decision': 'accepted'}, cookie)
        state, _ = self.call('recruiter/meetings', {'applicationId': app['id'], 'start': iso(self.tick + dt.timedelta(hours=1))}, manager=True)
        meeting = state['candidates'][0]['applications'][0]['meeting']
        self.assertEqual(meeting['status'], 'pending')
        self.assert_problem(409, 'candidate/meeting/join', {'applicationId': app['id'], 'meetingId': meeting['id'], 'consent': True}, cookie)
        self.assert_problem(409, 'candidate/meeting/confirm', {'applicationId': app['id'], 'meetingId': 'stale'}, cookie)
        result, _ = self.call('candidate/meeting/confirm', {'applicationId': app['id'], 'meetingId': meeting['id']}, cookie)
        self.assertEqual(result['applications'][0]['meeting']['status'], 'confirmed')

    def test_opt_in_auto_geo_match_does_not_confirm_time(self):
        p, cookie = self.legacy_auto_person()
        app = self.propose(p, self.vacancy(city='МОСКВА'))
        self.assertEqual(app['status'], 'accepted')
        self.assertTrue(app['approvedAutomatically'])
        state, _ = self.call('recruiter/meetings', {'applicationId': app['id'], 'start': iso(self.tick + dt.timedelta(hours=1))}, manager=True)
        self.assertEqual(state['candidates'][0]['applications'][0]['meeting']['status'], 'pending')

    def test_auto_geo_mismatch_and_remote_without_explicit_preference_stay_pending(self):
        p, cookie = self.legacy_auto_person()
        self.assertEqual(self.propose(p, self.vacancy(city='Казань'))['status'], 'pending')
        self.assertEqual(self.propose(p, self.vacancy(remote=True))['status'], 'pending')
        self.call('candidate/profile', {'workMode': 'remote'}, cookie)
        self.assertEqual(self.propose(p, self.vacancy(remote=True))['status'], 'accepted')

    def test_auto_unknown_role_salary_and_workmode_fall_back_to_manual(self):
        p, cookie = self.legacy_auto_person()
        self.assertEqual(self.propose(p, self.vacancy(title='Бухгалтер'))['status'], 'pending')
        self.call('candidate/profile', {'workMode': 'hybrid'}, cookie)
        self.assertEqual(self.propose(p, self.vacancy())['status'], 'pending')
        self.call('candidate/profile', {'workMode': 'office', 'desiredSalary': '150000 ₽'}, cookie)
        self.assertEqual(self.propose(p, self.vacancy())['status'], 'pending')

    def test_candidate_cannot_decide_or_confirm_for_another(self):
        p, cookie = self.completed_person()
        other, other_cookie = self.register()
        app = self.propose(p, self.vacancy())
        self.assert_problem(404, 'candidate/application/decision', {'applicationId': app['id'], 'decision': 'accepted'}, other_cookie)
        self.assert_problem(404, 'candidate/meeting/confirm', {'applicationId': app['id'], 'meetingId': 'unknown'}, other_cookie)
        self.assertEqual(self.call('candidate/state', cookie=other_cookie)[0]['applications'], [])

    def test_manager_times_must_match_actual_availability_and_are_unique(self):
        p, cookie = self.legacy_auto_person()
        app = self.propose(p, self.vacancy())
        self.assert_problem(409, 'recruiter/meetings', {'applicationId': app['id'], 'start': iso(self.tick + dt.timedelta(hours=5))}, manager=True)
        target = iso(self.tick + dt.timedelta(hours=1))
        self.call('recruiter/meetings', {'applicationId': app['id'], 'start': target}, manager=True)
        p2, c2 = self.legacy_auto_person()
        app2 = self.propose(p2, self.vacancy())
        self.assert_problem(409, 'recruiter/meetings', {'applicationId': app2['id'], 'start': target}, manager=True)

    def test_cross_origin_json_and_rate_limit(self):
        self.assert_problem(403, 'candidate/register', self.payload(), Origin='https://elsewhere.test')
        self.assert_problem(403, 'candidate/register', self.payload(), **{'Sec-Fetch-Site': 'cross-site'})
        self.assert_problem(415, 'candidate/register', self.payload(), **{'Content-Type': 'text/plain'})
        body = {'email': 'nobody@example.test', 'password': 'wrong-password-here'}
        for _ in range(15):
            self.assert_problem(401, 'candidate/login', body)
        self.assert_problem(429, 'candidate/login', body)

    def test_restart_preserves_accounts_profiles_and_events(self):
        p, cookie = self.register()
        new_portal = CandidatePortal(self.connect, threading.RLock(), self.media, 'https://portal.example', FakeNotifications)
        result, _ = new_portal.handle('GET', '/api/hr/candidate/state', {'Cookie': cookie}, None, 'ip', self.not_manager)
        self.assertEqual(result['id'], p['id'])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fake_events').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
