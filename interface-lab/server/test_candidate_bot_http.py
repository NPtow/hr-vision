"""Bot actions cross the real HTTP adapter and canonical SQLite candidate state.

Only Telegram's transport is faked. No test may reach a provider or use a real
recipient. Completed interview rows are fixtures; candidate decisions, binding,
proposals, meeting changes and reads use the application HTTP routes.
"""
import datetime as dt
from dataclasses import replace
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.parse
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

from domain import iso, now


class CandidateBotHTTPTest(unittest.TestCase):
    SECRET = 'test-webhook-secret-01234567890123456789'

    def setUp(self):
        self.data = tempfile.TemporaryDirectory(prefix='hr-candidate-bot-http-')
        self.addCleanup(self.data.cleanup)
        self.environment = patch.dict(os.environ, {
            'HR_DATA_DIR': self.data.name, 'HR_TEAM_KEY': 'bot-http-test-team-only',
            'HR_ORIGIN': 'https://portal.example.test',
            'HR_CANDIDATE_PUBLIC_URL': 'https://portal.example.test/iframe.html?id=hr-vision-product--candidate&viewMode=story',
            'HR_VIDEO_ENABLED': '0', 'DAILY_API_KEY': '',
            'HR_NOTIFICATIONS_ENABLED': '1', 'HR_SMTP_HOST': '',
            'HR_TELEGRAM_BOT_TOKEN': '123:never-a-real-token',
            'HR_TELEGRAM_BOT_USERNAME': 'HRVisionTestBot',
            'HR_TELEGRAM_WEBHOOK_SECRET': self.SECRET, 'HR_TELEGRAM_POLLING': '0',
            'HR_HH_ENABLED': '0', 'HR_HH_SENDING_ENABLED': '0',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for target in ('urllib.request.urlopen', 'smtplib.SMTP', 'smtplib.SMTP_SSL'):
            guard = patch(target, side_effect=AssertionError('Unexpected provider access'))
            guard.start()
            self.addCleanup(guard.stop)
        spec = importlib.util.spec_from_file_location('candidate_bot_http_' + uuid.uuid4().hex,
                                                      Path(__file__).with_name('app.py'))
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.sent = []
        self.polled_updates = []
        transport = patch.object(self.app.candidate_notifications.Transport, 'telegram_request',
                                 side_effect=self.telegram_request)
        transport.start()
        self.addCleanup(transport.stop)
        self.app.Handler.log_message = lambda *_args: None
        self.server = self.app.ThreadingHTTPServer(('127.0.0.1', 0), self.app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.sequence = 0
        self.start = now().replace(microsecond=0) + dt.timedelta(days=1)
        status, session, _ = self.request('session', {'teamKey': 'bot-http-test-team-only', 'actor': 'manager'})
        self.assertEqual(status, 200, session)
        self.manager = session['token']

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, body=None, *, cookie='', token='', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        request_headers = dict(headers or {})
        if cookie:
            request_headers['Cookie'] = cookie
        if token:
            request_headers['Authorization'] = 'Bearer ' + token
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        try:
            connection.request('GET' if body is None else 'POST', '/api/hr/' + path,
                               None if body is None else json.dumps(body), request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def telegram_request(self, method, payload):
        self.sent.append((method, json.loads(json.dumps(payload))))
        if method == 'sendMessage':
            return {'message_id': len(self.sent)}
        if method == 'answerCallbackQuery':
            return True
        if method == 'getUpdates':
            return [update for update in self.polled_updates if update['update_id'] >= payload['offset']]
        raise AssertionError('Unexpected Telegram method: ' + method)

    def drain(self):
        return self.app.candidate_notifications.process_pending(
            self.app.connect, limit=100, telegram_renderer=self.app.CANDIDATE_BOT.render_notification)

    def webhook(self, update, *, secret=SECRET):
        return self.request('candidate/telegram/webhook', update,
                            headers={'X-Telegram-Bot-Api-Secret-Token': secret})

    def message(self, chat, text, *, update_id=None):
        self.sequence += 1
        update = {'update_id': self.sequence if update_id is None else update_id,
                  'message': {'message_id': self.sequence, 'text': text,
                              'chat': {'id': chat, 'type': 'private'},
                              'from': {'id': chat, 'is_bot': False}}}
        status, result, _ = self.webhook(update)
        self.assertEqual(status, 200, result)
        return result

    def callback(self, chat, data, *, update_id=None):
        self.sequence += 1
        update = {'update_id': self.sequence if update_id is None else update_id,
                  'callback_query': {'id': 'query-' + str(self.sequence), 'data': data,
                                     'from': {'id': chat, 'is_bot': False},
                                     'message': {'message_id': 100,
                                                 'chat': {'id': chat, 'type': 'private'}}}}
        status, result, _ = self.webhook(update)
        self.assertEqual(status, 200, result)
        return result

    def register(self, chat=None):
        status, candidate, headers = self.request('candidate/register', {
            'email': uuid.uuid4().hex + '@example.test', 'password': 'correct-passphrase-123',
            'name': 'Bot HTTP Test', 'city': 'Москва', 'desiredRole': 'Менеджер',
            'geographies': ['Москва'], 'consentReuse': True,
            'availability': [{'start': iso(self.start), 'end': iso(self.start + dt.timedelta(hours=8))}],
        })
        self.assertEqual(status, 200, candidate)
        candidate['cookie'] = headers['Set-Cookie'].split(';')[0]
        if chat is not None:
            status, link, _ = self.request('candidate/telegram/link', {}, cookie=candidate['cookie'])
            self.assertEqual(status, 200, link)
            raw = urllib.parse.parse_qs(urllib.parse.urlsplit(link['url']).query)['start'][0]
            self.message(chat, '/start ' + raw)
            self.assertTrue(self.state(candidate)['channels']['telegram']['linked'])
            self.drain()
        return candidate

    def state(self, candidate):
        status, state, _ = self.request('candidate/state', cookie=candidate['cookie'])
        self.assertEqual(status, 200, state)
        return state

    def completed_interview(self, candidate):
        with self.app.connect() as db:
            if db.execute('SELECT 1 FROM cp_interviews WHERE candidate_id=?', (candidate['id'],)).fetchone():
                return
            slot, interview = 'slot-' + uuid.uuid4().hex, 'interview-' + uuid.uuid4().hex
            db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,?)',
                       (slot, iso(self.start - dt.timedelta(hours=2)),
                        iso(self.start - dt.timedelta(minutes=90)), 'Fixture recruiter', candidate['id']))
            db.execute('INSERT INTO cp_interviews VALUES (?,?,?,?,?,?,?)',
                       (interview, candidate['id'], slot, 'completed', None, iso(now()), iso(now())))

    def vacancy(self, label):
        company = 'Company ' + uuid.uuid4().hex
        status, state, _ = self.request('recruiter/vacancies', {
            'title': label, 'company': company, 'city': 'Москва', 'remote': False,
            'description': 'Isolated HTTP test vacancy'}, token=self.manager)
        self.assertEqual(status, 200, state)
        return next(item for item in state['vacancies'] if item['company'] == company)

    def application(self, candidate, label):
        self.completed_interview(candidate)
        vacancy = self.vacancy(label)
        status, result, _ = self.request('recruiter/applications', {
            'candidateId': candidate['id'], 'vacancyId': vacancy['id']}, token=self.manager)
        self.assertEqual(status, 200, result)
        return next(item for item in self.state(candidate)['applications'] if item['vacancy']['id'] == vacancy['id'])

    def proposed_meeting(self, candidate, application, *, hours=1):
        status, result, _ = self.request('candidate/application/decision', {
            'applicationId': application['id'], 'decision': 'accepted'}, cookie=candidate['cookie'])
        self.assertEqual(status, 200, result)
        status, result, _ = self.request('recruiter/meetings', {
            'applicationId': application['id'], 'start': iso(self.start + dt.timedelta(hours=hours))}, token=self.manager)
        self.assertEqual(status, 200, result)
        return self.application_state(candidate, application)['meeting']

    def application_state(self, candidate, application):
        return next(item for item in self.state(candidate)['applications'] if item['id'] == application['id'])

    def buttons(self):
        return [button for method, payload in self.sent if method == 'sendMessage'
                for row in payload.get('reply_markup', {}).get('inline_keyboard', []) for button in row]

    def assert_role_menu(self):
        self.assertEqual({(button['text'], button.get('callback_data')) for button in self.buttons()},
                         {('Я кандидат', 'role:candidate'), ('Я работодатель', 'role:employer')})

    def assert_role_entry(self, role):
        self.assertTrue(any(button.get('callback_data') == 'role:menu' for button in self.buttons()))
        links = [urllib.parse.urlsplit(button['url']) for button in self.buttons() if 'url' in button]
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual((link.scheme, link.netloc, link.path, link.fragment),
                         ('https', 'portal.example.test', '/iframe.html', ''))
        expected = {'id': ['hr-vision-product--candidate' if role == 'candidate' else 'hr-vision-product--manager'],
                    'viewMode': ['story']}
        if role == 'candidate':
            expected['telegram'] = ['connect']
        self.assertEqual(urllib.parse.parse_qs(link.query), expected)

    def action(self, name, application=None, meeting=None):
        # Select an actually delivered button, then verify its durable identity.
        for button in reversed(self.buttons()):
            data = button.get('callback_data', '')
            if not data.startswith('cb:'):
                continue
            self.assertLessEqual(len(data.encode()), 64)
            digest = hashlib.sha256(data[3:].encode()).hexdigest()
            with self.app.connect() as db:
                row = db.execute('SELECT action,application_id,meeting_id FROM cb_actions WHERE digest=?',
                                 (digest,)).fetchone()
            if row and row[0] == name and (application is None or row[1] == application['id']) \
                    and (meeting is None or row[2] == meeting['id']):
                return data
        self.fail('No delivered button for ' + name)

    def test_unbound_start_offers_registration_continuation_without_creating_identity(self):
        self.message(10001, '/start')
        self.assertEqual(self.sent, [], 'HTTP hooks must only queue Telegram delivery')
        self.drain()
        self.assert_role_menu()
        self.sent.clear()
        self.callback(10001, 'role:candidate')
        self.drain()
        self.assert_role_entry('candidate')
        links = [button['url'] for button in self.buttons() if 'url' in button]
        self.assertTrue(any(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get('telegram') == ['connect']
                            for url in links), links)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 0)
        count = len(self.sent)
        self.message(10001, '/start', update_id=1)
        self.drain()
        self.assertEqual(len(self.sent), count)
        candidate = self.register(10001)
        self.assertFalse(self.state(candidate)['autoApprove'])
        self.assertEqual(self.state(candidate)['applications'], [])
        self.assertEqual(self.request('candidate/state')[0], 401)

    def test_webhook_header_and_private_sender_are_required_before_data_or_delivery(self):
        update = {'update_id': 100, 'message': {'text': '/start',
                  'chat': {'id': 10001, 'type': 'private'}, 'from': {'id': 10001}}}
        for secret in ('', 'incorrect-test-secret'):
            self.assertEqual(self.webhook(update, secret=secret)[0], 403)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_telegram_updates').fetchone()[0], 0)
        for index, (kind, sender) in enumerate((('group', 10001), ('private', 10002)), start=101):
            update['update_id'] = index
            update['message']['chat']['type'] = kind
            update['message']['from']['id'] = sender
            self.assertEqual(self.webhook(update)[0], 200)
        # Public role buttons are still restricted to validated private senders.
        for index, (kind, sender) in enumerate((('group', 10001), ('private', 10002)), start=103):
            role_update = {'update_id': index, 'callback_query': {
                'id': 'invalid-private-role-' + str(index), 'data': 'role:employer',
                'from': {'id': sender, 'is_bot': False},
                'message': {'message_id': 1, 'chat': {'id': 10001, 'type': kind}}}}
            self.assertEqual(self.webhook(role_update)[0], 200)
        self.drain()
        self.assertEqual(self.sent, [])

    def test_unbound_role_navigation_is_repeatable_and_creates_no_profiles_or_actions(self):
        tables = ('cp_candidates', 'cp_sessions', 'cp_consent_log', 'cp_applications',
                  'cn_recipients', 'cb_actions', 'em_accounts', 'em_workspaces')
        with self.app.connect() as db:
            before = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables}
        self.message(10001, '/start')
        self.drain()
        self.assert_role_menu()
        for role in ('candidate', 'employer', 'candidate', 'employer'):
            self.sent.clear()
            self.callback(10001, 'role:' + role)
            self.drain()
            self.assert_role_entry(role)
            self.sent.clear()
            self.callback(10001, 'role:menu')
            self.drain()
            self.assert_role_menu()
        with self.app.connect() as db:
            for table in tables:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], before[table], table)

    def test_linked_start_and_role_switch_preserve_binding_profile_and_candidate_actions(self):
        owner = self.register(10001)
        application = self.application(owner, 'PRESERVED_AFTER_ROLE_SWITCH')
        self.drain()
        with self.app.connect() as db:
            profile_before = db.execute('SELECT * FROM cp_candidates WHERE id=?', (owner['id'],)).fetchone()
            recipient_before = db.execute('SELECT * FROM cn_recipients WHERE candidate_id=?', (owner['id'],)).fetchone()
            employers_before = db.execute('SELECT COUNT(*) FROM em_accounts').fetchone()[0]
        self.sent.clear()
        self.message(10001, '/start')
        self.drain()
        self.assert_role_menu()
        for role in ('employer', 'candidate'):
            self.sent.clear()
            self.callback(10001, 'role:' + role)
            self.drain()
            self.assert_role_entry(role)
            self.sent.clear()
            self.callback(10001, 'role:menu')
            self.drain()
            self.assert_role_menu()
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT * FROM cp_candidates WHERE id=?', (owner['id'],)).fetchone(), profile_before)
            self.assertEqual(db.execute('SELECT * FROM cn_recipients WHERE candidate_id=?', (owner['id'],)).fetchone(), recipient_before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_accounts').fetchone()[0], employers_before)
        self.assertTrue(self.state(owner)['channels']['telegram']['linked'])
        self.assertEqual(self.application_state(owner, application)['status'], 'pending')
        self.sent.clear()
        self.message(10001, '/vacancies')
        self.drain()
        self.callback(10001, self.action('application.accept', application))
        self.assertEqual(self.application_state(owner, application)['status'], 'accepted')

    def test_vacancies_contains_only_real_assigned_applications(self):
        owner, other = self.register(10001), self.register(10002)
        own = self.application(owner, 'OWNER_ONLY_VACANCY')
        self.application(other, 'FOREIGN_PRIVATE_VACANCY')
        self.vacancy('UNASSIGNED_CATALOG_VACANCY')
        self.drain()
        self.sent.clear()
        self.message(10001, '/vacancies')
        self.drain()
        text = '\n'.join(payload.get('text', '') for method, payload in self.sent if method == 'sendMessage')
        self.assertIn('OWNER_ONLY_VACANCY', text)
        self.assertNotIn('FOREIGN_PRIVATE_VACANCY', text)
        self.assertNotIn('UNASSIGNED_CATALOG_VACANCY', text)
        self.assertEqual([item['id'] for item in self.state(owner)['applications']], [own['id']])
        self.action('application.accept', own)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 2)

    def test_application_buttons_enforce_owner_single_decision_and_website_state(self):
        owner, other = self.register(10001), self.register(10002)
        accepted = self.application(owner, 'ACCEPT_EXACT_APPLICATION')
        declined = self.application(owner, 'DECLINE_EXACT_APPLICATION')
        foreign = self.application(other, 'OTHER_CANDIDATE_APPLICATION')
        self.drain()
        self.message(10001, '/vacancies')
        self.drain()
        accept = self.action('application.accept', accepted)
        opposite = self.action('application.decline', accepted)
        reject = self.action('application.decline', declined)
        self.callback(10002, accept)
        self.assertEqual(self.application_state(owner, accepted)['status'], 'pending')
        self.callback(10001, accept)
        first = self.application_state(owner, accepted)
        self.assertEqual(first['status'], 'accepted')
        self.assertFalse(first['approvedAutomatically'])
        replay = self.callback(10001, accept)  # New update ID, same consumed button.
        self.assertNotEqual(replay.get('status'), 'updated')
        self.callback(10001, opposite)
        self.callback(10001, reject)
        self.assertEqual(self.application_state(owner, accepted)['status'], 'accepted')
        self.assertEqual(self.application_state(owner, declined)['status'], 'declined')
        self.assertEqual(self.application_state(other, foreign)['status'], 'pending')
        self.assertFalse(self.state(owner)['autoApprove'])
        with self.app.connect() as db:
            decisions = dict(db.execute('SELECT id,status FROM cp_applications'))
        self.assertEqual(decisions[accepted['id']], 'accepted')
        self.assertEqual(decisions[declined['id']], 'declined')

    def test_meeting_callback_confirms_exact_id_and_time_without_touching_other_meeting(self):
        owner, other = self.register(10001), self.register(10002)
        first_app = self.application(owner, 'FIRST_MEETING_APPLICATION')
        second_app = self.application(owner, 'SECOND_MEETING_APPLICATION')
        first = self.proposed_meeting(owner, first_app, hours=1)
        second = self.proposed_meeting(owner, second_app, hours=3)
        self.drain()
        self.message(10001, '/meetings')
        self.drain()
        text = '\n'.join(payload.get('text', '') for method, payload in self.sent if method == 'sendMessage')
        displayed_time = dt.datetime.fromisoformat(first['start'].replace('Z', '+00:00')).astimezone(
            ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        self.assertIn(displayed_time, text)
        self.assertIn('МСК (UTC+3)', text)
        button = self.action('meeting.confirm', first_app, first)
        self.callback(10002, button)
        self.assertEqual(self.application_state(owner, first_app)['meeting']['status'], 'pending')
        self.callback(10001, button)
        confirmed = self.application_state(owner, first_app)['meeting']
        self.assertEqual((confirmed['id'], confirmed['start'], confirmed['end'], confirmed['status']),
                         (first['id'], first['start'], first['end'], 'confirmed'))
        replay = self.callback(10001, button)
        self.assertNotEqual(replay.get('status'), 'updated')
        self.assertEqual(self.application_state(owner, first_app)['meeting']['confirmedAt'], confirmed['confirmedAt'])
        self.assertEqual(self.application_state(owner, second_app)['meeting']['status'], 'pending')
        self.assertFalse(self.state(owner)['autoApprove'])
        self.assertEqual(self.state(other)['applications'], [])
        self.callback(10001, self.action('meeting.decline', second_app, second))
        self.assertIsNone(self.application_state(owner, second_app)['meeting'])
        self.assertEqual(self.application_state(owner, second_app)['meetings'][0]['status'], 'declined')

    def test_old_meeting_button_cannot_confirm_replacement_after_reschedule(self):
        owner = self.register(10001)
        application = self.application(owner, 'RESCHEDULED_APPLICATION')
        old = self.proposed_meeting(owner, application)
        self.drain()
        stale = self.action('meeting.confirm', application, old)
        status, result, _ = self.request('recruiter/meeting/reschedule', {
            'applicationId': application['id'], 'meetingId': old['id'], 'revision': old['revision'],
            'start': iso(self.start + dt.timedelta(hours=2))}, token=self.manager)
        self.assertEqual(status, 200, result)
        new = self.application_state(owner, application)['meeting']
        self.assertNotEqual(new['id'], old['id'])
        self.callback(10001, stale)
        self.assertEqual(self.application_state(owner, application)['meeting']['status'], 'pending')
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM cp_meetings WHERE id=?', (old['id'],)).fetchone()[0], 'cancelled')
        self.drain()
        self.callback(10001, self.action('meeting.confirm', application, new))
        self.assertEqual(self.application_state(owner, application)['meeting']['status'], 'confirmed')

    def test_meeting_snapshot_rejects_changed_time_even_if_id_and_revision_are_unchanged(self):
        owner = self.register(10001)
        application = self.application(owner, 'TIME_SNAPSHOT_APPLICATION')
        meeting = self.proposed_meeting(owner, application)
        self.drain()
        button = self.action('meeting.confirm', application, meeting)
        # Simulate another writer changing a row without increasing its revision.
        changed_start = iso(self.start + dt.timedelta(hours=4))
        with self.app.connect() as db:
            db.execute('UPDATE cp_meetings SET start=?,end=? WHERE id=?',
                       (changed_start, iso(self.start + dt.timedelta(hours=4, minutes=30)), meeting['id']))
        self.callback(10001, button)
        current = self.application_state(owner, application)['meeting']
        self.assertEqual((current['id'], current['revision'], current['start'], current['status']),
                         (meeting['id'], meeting['revision'], changed_start, 'pending'))

    def test_expired_or_malformed_callback_cannot_change_pending_application(self):
        owner = self.register(10001)
        application = self.application(owner, 'EXPIRED_CALLBACK_APPLICATION')
        self.drain()
        button = self.action('application.accept', application)
        with self.app.connect() as db:
            db.execute('UPDATE cb_actions SET expires_at=? WHERE digest=?',
                       (int(now().timestamp()) - 1, hashlib.sha256(button[3:].encode()).hexdigest()))
        for data in (button, 'cb:not-a-valid-token', 'application.accept:' + application['id']):
            self.callback(10001, data)
        self.assertEqual(self.application_state(owner, application)['status'], 'pending')
        self.assertFalse(self.state(owner)['autoApprove'])

    def test_changed_vacancy_requires_a_new_decision_button(self):
        owner = self.register(10001)
        application = self.application(owner, 'CHANGED_VACANCY_APPLICATION')
        self.drain()
        button = self.action('application.accept', application)
        with self.app.connect() as db:
            row = db.execute('SELECT body FROM cp_vacancies WHERE id=?', (application['vacancy']['id'],)).fetchone()
            changed = json.loads(row[0])
            changed['description'] = 'The employer changed the offered terms after the card was rendered.'
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?',
                       (json.dumps(changed), application['vacancy']['id']))
        self.callback(10001, button)
        self.assertEqual(self.application_state(owner, application)['status'], 'pending')
        self.message(10001, '/vacancies')
        self.drain()
        fresh = self.action('application.accept', application)
        self.assertNotEqual(fresh, button)
        self.callback(10001, fresh)
        self.assertEqual(self.application_state(owner, application)['status'], 'accepted')

    def test_auto_approval_requires_explicit_opt_in_and_never_confirms_a_meeting(self):
        owner = self.register(10001)
        pending = self.application(owner, 'Менеджер')
        self.assertFalse(self.state(owner)['autoApprove'])
        self.assertEqual(pending['status'], 'pending')
        self.message(10001, '/settings')
        self.drain()
        enable = self.action('settings.auto_on')
        self.callback(10001, enable)
        self.assertTrue(self.state(owner)['autoApprove'])
        self.assertEqual(self.application_state(owner, pending)['status'], 'pending')
        future = self.application(owner, 'Менеджер')
        self.assertEqual(future['status'], 'accepted')
        self.assertTrue(future['approvedAutomatically'])
        status, result, _ = self.request('recruiter/meetings', {
            'applicationId': future['id'], 'start': iso(self.start + dt.timedelta(hours=1))}, token=self.manager)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.application_state(owner, future)['meeting']['status'], 'pending')
        self.message(10001, '/settings')
        self.drain()
        self.callback(10001, self.action('settings.auto_off'))
        self.callback(10001, enable)  # Replaying the original opt-in cannot enable it again.
        self.assertFalse(self.state(owner)['autoApprove'])
        self.assertEqual(self.application_state(owner, future)['meeting']['status'], 'pending')

    def test_employer_rejection_invalidates_existing_button_and_hides_new_decision_buttons(self):
        owner = self.register(10001)
        application = self.application(owner, 'EMPLOYER_REJECTED_APPLICATION')
        self.drain()
        button = self.action('application.accept', application)
        # This independent employer workflow state does not modify the vacancy
        # body or the candidate's own pending decision.
        with self.app.connect() as db:
            db.execute('''INSERT INTO cp_employer_workflows
                (application_id,revision,decision,updated_at) VALUES (?,1,'declined',?)''',
                       (application['id'], iso(now())))
        self.callback(10001, button)
        current = self.application_state(owner, application)
        self.assertEqual(current['status'], 'pending')
        self.assertEqual(current['workflow']['decision'], 'declined')
        self.drain()
        self.sent.clear()
        self.message(10001, '/vacancies')
        self.drain()
        self.assertFalse(any('callback_data' in button for button in self.buttons()))
        text = '\n'.join(payload.get('text', '') for method, payload in self.sent if method == 'sendMessage')
        self.assertIn('Работодатель завершил рассмотрение', text)

    def test_whitespace_webhook_commits_updates_and_allows_next_normal_command(self):
        owner = self.register(10001)
        application = self.application(owner, 'AFTER_WHITESPACE_WEBHOOK')
        self.drain()
        self.sent.clear()
        update_ids = []
        for content in ('   ', '\t\n', '\u2003'):
            self.message(10001, content)
            update_ids.append(self.sequence)
        with self.app.connect() as db:
            persisted = {row[0] for row in db.execute('SELECT update_id FROM cn_telegram_updates')}
        self.assertTrue(set(update_ids) <= persisted)
        self.message(10001, '/vacancies')
        self.drain()
        text = '\n'.join(payload.get('text', '') for method, payload in self.sent if method == 'sendMessage')
        self.assertIn('AFTER_WHITESPACE_WEBHOOK', text)
        self.action('application.accept', application)
        self.assertEqual(self.application_state(owner, application)['status'], 'pending')

    def test_whitespace_polling_update_does_not_block_next_update_or_offset(self):
        owner = self.register(10001)
        application = self.application(owner, 'AFTER_WHITESPACE_POLLING')
        self.drain()
        self.sent.clear()
        self.polled_updates = [
            {'update_id': identifier, 'message': {'message_id': identifier, 'text': content,
                'chat': {'id': 10001, 'type': 'private'}, 'from': {'id': 10001, 'is_bot': False}}}
            for identifier, content in ((500, ' \u2003\t'), (501, '/vacancies'))]
        notifications = self.app.candidate_notifications
        config = replace(notifications.settings(), telegram_polling=True)
        result = notifications.poll_telegram_updates(self.app.connect, settings=config,
                                                     update_handler=self.app.CANDIDATE_BOT.handle_update)
        self.assertEqual(result, {'status': 'ready', 'processed': 2})
        with self.app.connect() as db:
            self.assertEqual(db.execute("SELECT value FROM cn_worker_state WHERE name='telegram_offset'").fetchone()[0], '502')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_telegram_updates WHERE update_id IN (500,501)').fetchone()[0], 2)
        self.drain()
        text = '\n'.join(payload.get('text', '') for method, payload in self.sent if method == 'sendMessage')
        self.assertIn('AFTER_WHITESPACE_POLLING', text)
        self.action('application.accept', application)
        result = notifications.poll_telegram_updates(self.app.connect, settings=config,
                                                     update_handler=self.app.CANDIDATE_BOT.handle_update)
        self.assertEqual(result['processed'], 0)
        self.assertEqual(self.sent[-1][0], 'getUpdates')
        self.assertEqual(self.sent[-1][1]['offset'], 502)


if __name__ == '__main__':
    unittest.main()
