"""Independent employer acceptance through the real HTTP handler.

Uses a temporary SQLite database, synthetic CVs and fake Daily responses. It
proves local identity/state/API integration, not actual delivery or recording.
The matching result is explicitly inserted as a recruiter fixture; no matcher
is represented as available by these tests.
"""
import base64
import concurrent.futures
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from unittest.mock import patch

from domain import Problem, iso, now
from test_candidate_media import MaterialMedia
import employer_access
import employer_store


PASSWORD = 'acceptance-test-passphrase-123'


class EmployerAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generation = patch('candidate_resume_model.ResumeModel.generate', side_effect=lambda resume, *_: resume)
        cls.generation.start()
        cls.addClassCleanup(cls.generation.stop)
        cls.data = tempfile.TemporaryDirectory(prefix='hr-employer-acceptance-')
        cls.env = patch.dict(os.environ, {
            'HR_DATA_DIR': cls.data.name, 'HR_TEAM_KEY': 'acceptance-private-team',
            'HR_VIDEO_ENABLED': '0', 'DAILY_API_KEY': '',
            'HR_NOTIFICATIONS_ENABLED': '0', 'HR_HH_ENABLED': '0',
            'HR_HH_SENDING_ENABLED': '0', 'HR_TRUST_CLIENT_IP': '0',
        })
        cls.env.start()
        spec = importlib.util.spec_from_file_location(
            'employer_acceptance_isolated_app', Path(__file__).with_name('app.py'))
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)
        cls.media = MaterialMedia()
        cls.app.MEDIA = cls.media
        cls.app.CANDIDATE_PORTAL.media = cls.media
        if hasattr(cls.app, 'EMPLOYER_ROUTES'):
            cls.app.EMPLOYER_ROUTES.media = cls.media
        cls.server = cls.app.ThreadingHTTPServer(('127.0.0.1', 0), cls.app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/api/hr/'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.env.stop()
        cls.data.cleanup()

    def request(self, path, body=None, *, token='', cookie='', headers=None):
        request_headers = {'Cookie': cookie, **(headers or {})}
        if token:
            request_headers['Authorization'] = 'Bearer ' + token
        if body is not None:
            request_headers.setdefault('Content-Type', 'application/json')
        req = urllib.request.Request(self.url + path,
            data=None if body is None else json.dumps(body).encode(), headers=request_headers)
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = response.read()
            try:
                value = json.loads(content)
            except ValueError:
                value = {'nonJsonResponse': content.decode(errors='replace')[:250]}
            return response.status, value, response.headers

    def ok(self, path, body=None, **auth):
        status, value, _ = self.request(path, body, **auth)
        self.assertEqual(status, 200, (path, status, value))
        return value

    def setUp(self):
        self.tick = now().replace(microsecond=0) + dt.timedelta(minutes=5)
        # Independent cases intentionally do not share authentication throttles.
        with self.app.connect() as db:
            for table in ('em_auth_limits', 'cp_rate_limits'):
                if db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone():
                    db.execute('DELETE FROM ' + table)
        self.manager = self.ok('session', {'teamKey': 'acceptance-private-team', 'actor': 'manager'})['token']

    def employer(self, suffix='A'):
        email = str(uuid.uuid4()) + '@example.test'
        status, result, headers = self.request('employer/register', {
            'email': email, 'password': PASSWORD, 'name': 'Нанимающий ' + suffix,
            'workspaceName': 'Приемка ' + suffix,
        })
        self.assertEqual(status, 200, result)
        for flag in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/'):
            self.assertIn(flag, headers['Set-Cookie'])
        return {**result, 'email': email, 'cookie': headers['Set-Cookie'].split(';')[0]}

    def vacancy(self, employer, title='Менеджер приемки'):
        result = self.ok('employer/vacancies', {
            'title': title, 'company': 'Приемка', 'city': 'Москва', 'remote': False,
            'description': 'Синтетическая вакансия для проверки backend.',
            'salary': '100–150 тыс. ₽', 'format': 'Москва · офис',
            'expectations': 'Работа с клиентами',
        }, token=employer['token'])
        return result.get('vacancy', result)

    def state(self, employer, vacancy=None):
        query = '?vacancyId=' + urllib.parse.quote(vacancy['id']) if vacancy else ''
        return self.ok('employer/state' + query, token=employer['token'])

    def candidate(self, suffix='Кандидат'):
        status, person, headers = self.request('candidate/register', {
            'email': str(uuid.uuid4()) + '@example.test', 'password': PASSWORD,
            'consentReuse': True, 'autoApprove': False,
        })
        self.assertEqual(status, 200, person)
        cookie = headers['Set-Cookie'].split(';')[0]
        cv = ('Имя: ' + suffix + '\nГород: Москва\nЖелаемая должность: Менеджер\n'
              'Опыт работы: пять лет развивал продажи и поддерживал отношения с клиентами. '
              'Проводил переговоры, готовил коммерческие предложения, обучал коллег и работал в CRM. '
              'Образование: высшее. Навыки: планирование, коммуникация и анализ данных.')
        self.ok('candidate/resume', {'filename': 'acceptance.txt',
            'contentBase64': base64.b64encode(cv.encode()).decode()}, cookie=cookie)
        self.ok('candidate/profile', {'name': suffix, 'salaryMin': 100000, 'salaryMax': 150000,
            'latestStartDate': (self.tick + dt.timedelta(days=14)).date().isoformat(),
            'resumeReviewed': True, 'availability': [{'start': iso(self.tick),
                'end': iso(self.tick + dt.timedelta(hours=8))}]}, cookie=cookie)
        # An already-completed initial interview is the input boundary for the
        # employer tests. No fake provider execution is claimed for this fixture.
        slot, mid = 'slot_' + uuid.uuid4().hex, 'initial_' + uuid.uuid4().hex
        with self.app.connect() as db:
            start = self.tick - dt.timedelta(days=2)
            db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,?)',
                (slot, iso(start), iso(start + dt.timedelta(minutes=30)), 'Acceptance', person['id']))
            db.execute('INSERT INTO cp_interviews VALUES (?,?,?,?,?,?,?)',
                (mid, person['id'], slot, 'completed', None, iso(start), iso(start + dt.timedelta(minutes=30))))
        return {**person, 'cookie': cookie, 'initialId': mid}

    def batch(self, employer, vacancy, people):
        """Authorised integration fixture, separate from public candidate access."""
        with self.app.LOCK, self.app.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            identity = employer_access.authenticate(db, {'Authorization': 'Bearer ' + employer['token']})
            return employer_store.import_batch(db, identity['workspaceId'], vacancy['id'], {
                'source': 'recruiter', 'version': 'acceptance-fixture-v1',
                'externalBatchId': 'acceptance-' + uuid.uuid4().hex,
                'candidates': [{'candidateId': person['id'], 'rank': rank,
                    'reason': 'Синтетическая рекомендация для тестирования интеграции.'}
                    for rank, person in enumerate(people)],
            }, portal=self.app.CANDIDATE_PORTAL)

    def accepted(self, employer, vacancy, person=None):
        person = person or self.candidate()
        batch = self.batch(employer, vacancy, [person])
        aid = batch['applicationIds'][0]
        self.ok('candidate/application/decision', {'applicationId': aid, 'decision': 'accepted'}, cookie=person['cookie'])
        state = self.state(employer, vacancy)
        self.assertEqual(state['candidates'][aid]['candidateId'], person['id'])
        return person, aid

    def action_body(self, employer, vacancy, application, action, **changes):
        state = self.state(employer, vacancy)
        return {'action': action, 'vacancyId': vacancy['id'], 'candidate': application,
            'applicationId': application, 'generation': state['generation'],
            'requestId': uuid.uuid4().hex, **changes}

    def act(self, employer, vacancy, application, action, **changes):
        return self.ok('employer/action', self.action_body(employer, vacancy, application, action, **changes),
            token=employer['token'])

    def propose(self, employer, vacancy, aid, *, minutes=0, previous=None):
        state = self.act(employer, vacancy, aid, 'meeting.propose',
            start=iso(self.tick + dt.timedelta(minutes=minutes)),
            previousMeetingId=previous['id'] if previous else None,
            **({'meetingId': previous['id'], 'revision': previous['revision']} if previous else {}))
        return state['candidates'][aid]['meeting']

    @staticmethod
    def exact(aid, meeting):
        return {'applicationId': aid, 'meetingId': meeting['id'], 'revision': meeting['revision']}

    def complete(self, employer, vacancy, person, aid, meeting):
        self.ok('candidate/meeting/confirm', self.exact(aid, meeting), cookie=person['cookie'])
        self.ok('employer/room', self.action_body(employer, vacancy, aid, 'meeting.room',
            meetingId=meeting['id'], revision=meeting['revision'], consent=True), token=employer['token'])
        self.act(employer, vacancy, aid, 'meeting.finish', meetingId=meeting['id'], revision=meeting['revision'])
        self.assertEqual(self.state(employer, vacancy)['candidates'][aid]['meeting']['status'], 'completed')

    def feedback(self, employer, vacancy, aid, meeting):
        state = self.act(employer, vacancy, aid, 'feedback.questions', meetingId=meeting['id'],
            questions='Как вы организовали работу с возражениями клиента?')
        questionnaire = state['candidates'][aid]['meeting']['questionnaire']
        answers = {q['id']: {'rating': 'clear', 'comment': 'Ответ записан в тесте.'} for q in questionnaire['questions']}
        self.act(employer, vacancy, aid, 'feedback.draft', meetingId=meeting['id'],
            questionnaireId=questionnaire['id'], answers=answers)
        return self.act(employer, vacancy, aid, 'feedback', meetingId=meeting['id'],
            questionnaireId=questionnaire['id'], answers=answers)

    def offer_terms(self, **changes):
        return {'role': 'Менеджер приемки', 'compensation': '150 000 ₽', 'format': 'Москва · офис',
            'expectations': 'Развивать отношения с клиентами',
            'startDate': (self.tick + dt.timedelta(days=7)).date().isoformat(), **changes}

    def test_public_identity_and_cookie_are_separate_from_candidate_and_team(self):
        employer = self.employer()
        self.assertTrue(employer['cookie'].startswith('__Host-hr_employer='))
        status, session, headers = self.request('employer/session', cookie=employer['cookie'])
        self.assertEqual(status, 200, session)
        self.assertNotIn(PASSWORD, json.dumps(session))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        person = self.candidate()
        for path in ('employer/state', 'recruiter/state'):
            self.assertEqual(self.request(path, cookie=person['cookie'])[0], 401, path)
        self.assertEqual(self.request('candidate/state', cookie=employer['cookie'])[0], 401)
        self.assertEqual(self.request('recruiter/state', token=employer['token'])[0], 401)
        self.assertEqual(self.request('employer/state', token='em_invalid', cookie=employer['cookie'])[0], 401)
        self.assertNotEqual(self.request('employer/session', {'workspaceId': 'hr-vision-team'})[0], 200)
        self.assertEqual(self.request('employer/register', {
            'email': str(uuid.uuid4()) + '@example.test', 'password': PASSWORD,
            'name': 'Attack', 'workspaceId': 'hr-vision-team',
        })[0], 400)

    def test_empty_workspaces_are_not_seeded_with_demo_candidates(self):
        a, b = self.employer('A'), self.employer('B')
        self.assertEqual(self.state(a)['candidates'], {})
        self.assertEqual(self.state(b)['candidates'], {})
        av, bv = self.vacancy(a, 'A vacancy'), self.vacancy(b, 'B vacancy')
        sa, sb = self.state(a, av), self.state(b, bv)
        self.assertEqual(sa['vacancy']['id'], av['id'])
        self.assertEqual(sb['vacancy']['id'], bv['id'])
        self.assertNotIn(bv['id'], json.dumps(sa))
        self.assertNotIn(av['id'], json.dumps(sb))
        self.assertEqual(self.request('employer/state?vacancyId=' + av['id'], token=b['token'])[0], 404)
        self.assertEqual(sa['candidates'], {})
        self.assertEqual(sb['candidates'], {})

    def test_http_origin_and_credentials_do_not_cross(self):
        employer = self.employer()
        self.assertEqual(self.request('employer/vacancies', {'title': 'Forbidden'},
            token=employer['token'], headers={'Origin': 'https://hostile.example'})[0], 403)
        self.assertEqual(self.request('employer/login', {'email': employer['email'], 'password': PASSWORD},
            headers={'Origin': 'https://hostile.example'})[0], 403)
        status, result, _ = self.request('employer/login', {'email': employer['email'].upper(), 'password': PASSWORD})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.state(employer)['workspaceId'], self.state(result)['workspaceId'])
        self.assertEqual(self.request('employer/login', {'email': employer['email'], 'password': 'incorrect-passphrase'})[0], 401)
        self.ok('employer/logout', {}, cookie=employer['cookie'])
        self.assertEqual(self.request('employer/state', token=employer['token'])[0], 401)
        self.assertEqual(self.request('employer/state', token=result['token'])[0], 200)

    def test_complete_candidate_meeting_feedback_offer_path_keeps_identity(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        other = self.vacancy(employer, 'Другая вакансия')
        person = self.candidate('Приемочный кандидат')
        batch = self.batch(employer, vacancy, [person])
        aid = batch['applicationIds'][0]
        self.assertNotIn(aid, self.state(employer, vacancy)['candidates'])
        pending = next(a for a in self.ok('candidate/state', cookie=person['cookie'])['applications'] if a['id'] == aid)
        self.assertEqual(pending['status'], 'pending')
        self.assertIsNone(pending['meeting'])
        self.ok('candidate/application/decision', {'applicationId': aid, 'decision': 'accepted'}, cookie=person['cookie'])
        meeting = self.propose(employer, vacancy, aid)
        state = self.state(employer, vacancy)
        self.assertEqual(state['candidates'][aid]['id'], aid)
        self.assertEqual(state['candidates'][aid]['candidateId'], person['id'])
        self.assertEqual(state['candidates'][aid]['reviewState'], 'selected')
        self.assertEqual(meeting['status'], 'pending')
        pending = next(a for a in self.ok('candidate/state', cookie=person['cookie'])['applications'] if a['id'] == aid)
        self.assertEqual(pending['meeting']['id'], meeting['id'])
        self.assertEqual(pending['meeting']['status'], 'pending')
        self.complete(employer, vacancy, person, aid, meeting)
        self.assertNotIn(meeting['id'], [p['meeting']['id'] for p in self.state(employer, vacancy)['meetings']])
        self.feedback(employer, vacancy, aid, meeting)
        self.act(employer, vacancy, aid, 'decision', value='pool')
        state = self.act(employer, vacancy, aid, 'offer.save', **self.offer_terms(version=0))
        self.assertEqual(state['candidates'][aid]['offer']['version'], 1)
        candidate = self.ok('candidate/state', cookie=person['cookie'])
        app = next(a for a in candidate['applications'] if a['id'] == aid)
        self.assertIsNone(app['offer'])
        self.assertNotIn('feedback', app)
        self.assertEqual(candidate['id'], person['id'])
        self.assertEqual(candidate['interview']['id'], person['initialId'])
        self.act(employer, vacancy, aid, 'offer.send', version=1)
        self.act(employer, vacancy, aid, 'offer.save', **self.offer_terms(compensation='170 000 ₽', version=1))
        self.assertEqual(self.request('candidate/workflow', {'applicationId': aid, 'action': 'offer.reply',
            'value': 'accepted', 'version': 1}, cookie=person['cookie'])[0], 409)
        self.ok('candidate/workflow', {'applicationId': aid, 'action': 'offer.reply',
            'value': 'accepted', 'version': 2}, cookie=person['cookie'])
        state = self.state(employer, vacancy)
        self.assertEqual(state['closedBy'], aid)
        self.assertEqual(state['candidates'][aid]['offer']['status'], 'accepted')
        self.assertFalse(self.state(employer, other)['closedBy'])
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT candidate_id FROM cp_applications WHERE id=?', (aid,)).fetchone()[0], person['id'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates WHERE id=?', (person['id'],)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_offer_versions WHERE application_id=?', (aid,)).fetchone()[0], 2)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_exact_reschedule_ids_and_candidate_global_busy_calendar(self):
        a, b = self.employer('A'), self.employer('B')
        av, bv = self.vacancy(a), self.vacancy(b)
        person, aa = self.accepted(a, av)
        _, ba = self.accepted(b, bv, person)
        first = self.propose(a, av, aa)
        self.assertEqual(self.request('employer/action', self.action_body(b, bv, ba, 'meeting.propose',
            start=first['start']), token=b['token'])[0], 409)
        self.ok('candidate/meeting/confirm', self.exact(aa, first), cookie=person['cookie'])
        second = self.propose(a, av, aa, minutes=60, previous=first)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(second['revision'], first['revision'] + 1)
        self.assertEqual(second['status'], 'pending')
        self.assertEqual(self.request('candidate/meeting/confirm', self.exact(aa, first), cookie=person['cookie'])[0], 409)
        state = self.ok('candidate/state', cookie=person['cookie'])
        app = next(p for p in state['applications'] if p['id'] == aa)
        self.assertEqual({m['id']: m['status'] for m in app['meetings']}, {second['id']: 'pending', first['id']: 'cancelled'})
        self.assertTrue(all('room' not in m for m in app['meetings']))
        self.propose(b, bv, ba)
        self.assertEqual(self.state(a, av)['candidates'][aa]['candidateId'], self.state(b, bv)['candidates'][ba]['candidateId'])
        self.assertNotIn(ba, json.dumps(self.state(a, av)))
        self.assertNotIn(aa, json.dumps(self.state(b, bv)))

    def test_known_foreign_ids_fail_before_provider_calls(self):
        a, b = self.employer('A'), self.employer('B')
        av, bv = self.vacancy(a), self.vacancy(b)
        _, aa = self.accepted(a, av)
        foreign = self.action_body(a, av, aa, 'chat', text='Нельзя отправить в чужой кабинет')
        for path, payload in (
            ('employer/action', foreign),
            ('employer/room', {**foreign, 'consent': True, 'meetingId': 'known-id'}),
            ('employer/materials', {**foreign, 'meetingId': 'known-id'}),
            ('employer/feedback/questions', {**foreign, 'meetingId': 'known-id'}),
        ):
            before = len(self.media.tokens)
            with self.subTest(path=path):
                self.assertEqual(self.request(path, payload, token=b['token'])[0], 404)
                self.assertEqual(len(self.media.tokens), before)
        # Supplying one's own vacancy alongside another workspace's application
        # must not turn a correct vacancy check into authorization for that app.
        foreign['vacancyId'] = bv['id']
        self.assertEqual(self.request('employer/action', foreign, token=b['token'])[0], 404)

    def test_rejection_and_consent_withdrawal_hide_all_employer_sections(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        person, aid = self.accepted(employer, vacancy)
        self.act(employer, vacancy, aid, 'review.reject')
        state = self.state(employer, vacancy)
        self.assertNotIn(aid, json.dumps(state))
        self.assertNotIn(person['id'], json.dumps(state))
        self.batch(employer, vacancy, [person])
        self.assertNotIn(aid, self.state(employer, vacancy)['candidates'])
        other, aid2 = self.accepted(employer, vacancy)
        self.ok('candidate/profile', {'consentReuse': False, 'autoApprove': False}, cookie=other['cookie'])
        self.assertNotIn(aid2, json.dumps(self.state(employer, vacancy)))
        self.assertEqual(self.request('employer/action', self.action_body(employer, vacancy, aid2, 'chat', text='После отзыва'),
            token=employer['token'])[0], 404)

    def test_skip_moves_to_end_and_retry_does_not_move_twice(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        ids = [self.accepted(employer, vacancy)[1] for _ in range(3)]
        state = self.state(employer, vacancy)
        self.assertEqual(list(state['candidates']), ids)
        command = self.action_body(employer, vacancy, ids[0], 'review.skip')
        self.ok('employer/action', command, token=employer['token'])
        self.ok('employer/action', command, token=employer['token'])
        self.assertEqual(list(self.state(employer, vacancy)['candidates']), [ids[1], ids[2], ids[0]])
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT skip_count FROM em_reviews WHERE application_id=?', (ids[0],)).fetchone()[0], 1)

    def test_employer_cannot_import_known_candidate_ids_or_claim_core_matches(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        candidate = self.candidate()
        for source in ('recruiter', 'core'):
            payload = {'vacancyId': vacancy['id'], 'source': source, 'version': 'fake-v1',
                'externalBatchId': uuid.uuid4().hex,
                'candidates': [{'candidateId': candidate['id'], 'rank': 0, 'reason': 'known-id attack'}]}
            self.assertEqual(self.request('employer/batches', payload, token=employer['token'])[0], 403)
        self.assertEqual(self.state(employer, vacancy)['candidates'], {})
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications WHERE vacancy_id=?', (vacancy['id'],)).fetchone()[0], 0)

    def test_wrong_password_attempts_survive_rollback(self):
        employer = self.employer()
        codes = [self.request('employer/login', {'email': employer['email'], 'password': 'incorrect-passphrase'})[0]
                 for _ in range(16)]
        self.assertEqual(codes, [401] * 15 + [429])
        self.assertEqual(self.request('employer/login', {'email': employer['email'], 'password': PASSWORD})[0], 429)

    def test_chat_retry_exact_application_and_candidate_privacy(self):
        a, b = self.employer('A'), self.employer('B')
        av, bv = self.vacancy(a), self.vacancy(b)
        person, aa = self.accepted(a, av)
        _, ba = self.accepted(b, bv, person)
        command = self.action_body(a, av, aa, 'chat', text='Приватное сообщение вакансии A')
        self.ok('employer/action', command, token=a['token'])
        self.ok('employer/action', command, token=a['token'])
        state = self.state(a, av)
        self.assertEqual(len([m for m in state['candidates'][aa]['messages'] if m['text'] == command['text']]), 1)
        self.assertNotIn(command['text'], json.dumps(self.state(b, bv), ensure_ascii=False))
        self.ok('candidate/workflow', {'applicationId': aa, 'action': 'chat', 'text': 'Ответ только A'}, cookie=person['cookie'])
        self.assertIn('Ответ только A', json.dumps(self.state(a, av), ensure_ascii=False))
        self.assertNotIn('Ответ только A', json.dumps(self.state(b, bv), ensure_ascii=False))
        stranger = self.candidate('Посторонний')
        self.assertEqual(self.request('candidate/workflow', {'applicationId': ba, 'action': 'chat', 'text': 'Чужой'},
            cookie=stranger['cookie'])[0], 404)

    def test_distinct_employers_can_meet_different_candidates_at_same_time(self):
        a, b = self.employer('A'), self.employer('B')
        av, bv = self.vacancy(a), self.vacancy(b)
        _, aa = self.accepted(a, av)
        _, ba = self.accepted(b, bv)
        first, second = self.propose(a, av, aa), self.propose(b, bv, ba)
        self.assertEqual(first['start'], second['start'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(self.state(a, av)['meetings']), 1)
        self.assertEqual(len(self.state(b, bv)['meetings']), 1)

    def test_media_finish_failure_is_retryable_without_faking_completion(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        person, aid = self.accepted(employer, vacancy)
        meeting = self.propose(employer, vacancy, aid)
        self.ok('candidate/meeting/confirm', self.exact(aid, meeting), cookie=person['cookie'])
        self.ok('employer/room', self.action_body(employer, vacancy, aid, 'meeting.room',
            meetingId=meeting['id'], consent=True), token=employer['token'])
        command = self.action_body(employer, vacancy, aid, 'meeting.finish', meetingId=meeting['id'])
        with patch.object(self.media, 'finish', side_effect=Problem('Synthetic provider unavailable', 502)):
            self.assertEqual(self.request('employer/action', command, token=employer['token'])[0], 502)
        self.assertEqual(self.state(employer, vacancy)['candidates'][aid]['meeting']['status'], 'live')
        self.ok('employer/action', command, token=employer['token'])
        self.assertEqual(self.state(employer, vacancy)['candidates'][aid]['meeting']['status'], 'completed')

    def test_only_one_active_offer_can_win_concurrent_send(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        ids = []
        for _ in range(2):
            person, aid = self.accepted(employer, vacancy)
            meeting = self.propose(employer, vacancy, aid)
            self.complete(employer, vacancy, person, aid, meeting)
            self.feedback(employer, vacancy, aid, meeting)
            self.act(employer, vacancy, aid, 'decision', value='pool')
            self.act(employer, vacancy, aid, 'offer.save', **self.offer_terms(version=0))
            ids.append(aid)
        commands = [self.action_body(employer, vacancy, aid, 'offer.send', version=1) for aid in ids]
        barrier = threading.Barrier(2)
        def send(command):
            barrier.wait()
            return self.request('employer/action', command, token=employer['token'])[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, commands))
        self.assertEqual(sorted(results), [200, 409])
        with self.app.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cp_offer_versions WHERE vacancy_id=? AND status='sent'",
                (vacancy['id'],)).fetchone()[0], 1)

    def test_logout_during_room_provider_io_never_returns_a_token(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        person, aid = self.accepted(employer, vacancy)
        meeting = self.propose(employer, vacancy, aid)
        self.ok('candidate/meeting/confirm', self.exact(aid, meeting), cookie=person['cookie'])
        command = self.action_body(employer, vacancy, aid, 'meeting.room', meetingId=meeting['id'], consent=True)
        entered, release = threading.Event(), threading.Event()
        original = self.media.room
        def delayed(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5), 'provider probe was not released')
            return original(*args, **kwargs)
        with patch.object(self.media, 'room', side_effect=delayed), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(self.request, 'employer/room', command, token=employer['token'])
            try:
                self.assertTrue(entered.wait(3))
                # Must remain responsive while a provider request is pending.
                self.ok('employer/logout', {}, cookie=employer['cookie'])
            finally:
                release.set()
            status, result, _ = request.result()
        self.assertEqual(status, 401, result)
        self.assertNotIn('token', result)


if __name__ == '__main__':
    unittest.main()
