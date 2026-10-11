import concurrent.futures
from dataclasses import replace
import io
import json
from pathlib import Path
import re
import smtplib
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.parse

import candidate_notifications as notifications
from domain import Problem


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class FakeTransport:
    def __init__(self):
        self.email, self.telegram = [], []
        self.email_failure = None
        self.telegram_failure = None
        self.updates = []
        self.requests = []

    def send_email(self, recipient, subject, content, message_id):
        self.email.append({'recipient': recipient, 'subject': subject, 'content': content, 'id': message_id})
        if self.email_failure:
            raise self.email_failure
        return 'smtp_accepted'

    def send_telegram(self, recipient, content):
        self.telegram.append({'recipient': recipient, 'content': content})
        if self.telegram_failure:
            raise self.telegram_failure
        return '1234'

    def telegram_request(self, method, payload):
        self.requests.append((method, payload))
        return self.updates


class CandidateNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'notifications.sqlite3'
        self.config = notifications.Settings(
            enabled=True, smtp_host='smtp.example.test', smtp_from='hr@example.test',
            smtp_user='test-user', smtp_password='never-a-real-password',
            telegram_token='123:never-a-real-token', telegram_username='HRVisionTestBot',
            telegram_webhook_secret='test-webhook-secret-01234567890123456789')
        self.transport = FakeTransport()
        self.net = patch('candidate_notifications.urllib.request.urlopen', side_effect=AssertionError('Unexpected network'))
        self.smtp = patch('candidate_notifications.smtplib.SMTP', side_effect=AssertionError('Unexpected SMTP'))
        self.smtps = patch('candidate_notifications.smtplib.SMTP_SSL', side_effect=AssertionError('Unexpected SMTP SSL'))
        self.net.start()
        self.smtp.start()
        self.smtps.start()
        self.addCleanup(self.net.stop)
        self.addCleanup(self.smtp.stop)
        self.addCleanup(self.smtps.stop)
        self.addCleanup(self.directory.cleanup)
        with self.connect() as db:
            notifications.init(db)
            notifications.set_email(db, 'candidate-a', 'a@example.test', now=1)
            notifications.set_email(db, 'candidate-b', 'b@example.test', now=1)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)

    def process(self, now=100, config=None, limit=10):
        return notifications.process_pending(self.connect, settings=config or self.config,
                                              now=now, transport=self.transport, limit=limit)

    def rows(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM cn_outbox ORDER BY rowid')]

    def verify_fixture(self):
        with self.connect() as db:
            notifications.set_email(db, 'candidate-a', 'a@example.test', verified=True, now=2)

    def telegram_link(self, candidate='candidate-a', now=10):
        with self.connect() as db:
            result = notifications.create_telegram_link(db, candidate, self.config, now)
        return urllib.parse.parse_qs(urllib.parse.urlsplit(result['url']).query)['start'][0]

    def update(self, token, update_id=1, chat_id=1001):
        return {'update_id': update_id, 'message': {'text': '/start ' + token,
                'chat': {'id': chat_id, 'type': 'private'}, 'from': {'id': chat_id, 'is_bot': False}}}

    def link_fixture(self):
        token = self.telegram_link()
        with self.connect() as db:
            notifications.handle_telegram_update(db, self.update(token), self.config.telegram_webhook_secret, self.config, 20)

    def verification_token(self):
        text = self.transport.email[-1]['content']
        url = re.search(r'https://\S+', text).group(0)
        return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['verify'][0]

    def test_config_off_is_explicit_and_secret_safe(self):
        config = notifications.settings({})
        public = notifications.configuration_public(config)
        self.assertEqual(public, {'enabled': False, 'emailConfigured': False,
                                 'telegramConfigured': False, 'telegramBindingAvailable': False})
        self.assertNotIn('never-a-real', repr(self.config))
        self.assertNotIn('never-a-real', json.dumps(notifications.configuration_public(self.config)))
        self.assertFalse(replace(self.config, smtp_security='none').email_configured)
        self.assertFalse(replace(self.config, public_url='http://example.test').email_configured)

    def test_default_link_preserves_candidate_story(self):
        config = notifications.settings({'HR_ORIGIN': 'https://hr.example.test/'})
        self.assertEqual(config.public_url, 'https://hr.example.test/iframe.html?id=hr-vision-product--candidate&viewMode=story')

    def test_init_does_not_commit_caller_transaction(self):
        db = self.connect()
        try:
            db.execute("INSERT INTO cn_worker_state VALUES ('transaction-test','private')")
            notifications.init(db)
            db.rollback()
            self.assertIsNone(db.execute("SELECT value FROM cn_worker_state WHERE name='transaction-test'").fetchone())
        finally:
            db.close()

    def test_disabled_events_do_not_wake_when_config_appears(self):
        self.verify_fixture()
        with self.connect() as db:
            records = notifications.enqueue(db, 'candidate-a', 'register:one', 'candidate.registered',
                                            settings=notifications.Settings(), now=10)
        self.assertTrue(all(item['status'] == 'disabled' for item in records))
        self.assertEqual(self.process()['delivered'], 0)
        self.assertEqual(self.transport.email, [])
        with self.connect() as db:
            retried = notifications.retry(db, records[0]['id'], self.config, 101)
        self.assertEqual(retried['status'], 'pending')
        self.assertEqual(self.process(102)['delivered'], 1)
        self.assertEqual(self.rows()[1]['status'], 'disabled')

    def test_idempotency_is_per_event_and_channel(self):
        self.verify_fixture()
        with self.connect() as db:
            original = notifications.enqueue(db, 'candidate-a', 'event:one', 'interview.booked', settings=self.config, now=10)
            duplicate = notifications.enqueue(db, 'candidate-a', 'event:one', 'interview.booked', settings=self.config, now=20)
        self.assertEqual(original, duplicate)
        self.assertEqual(len(self.rows()), 2)
        with self.connect() as db, self.assertRaises(Problem):
            notifications.enqueue(db, 'candidate-a', 'event:one', 'meeting.confirmed', settings=self.config)

    def test_unverified_email_and_unbound_telegram_never_receive_lifecycle(self):
        with self.connect() as db:
            result = notifications.enqueue(db, 'candidate-a', 'event:one', 'meeting.proposed', settings=self.config, now=10)
        self.assertEqual([item['status'] for item in result], ['unverified', 'unbound'])
        self.verify_fixture()
        self.link_fixture()
        self.assertEqual(self.process()['delivered'], 0)
        with self.connect() as db:
            notifications.enqueue(db, 'candidate-a', 'event:two', 'meeting.proposed', settings=self.config, now=30)
        self.assertEqual(self.process()['delivered'], 2)
        self.assertIn('встреча ожидает вашего решения', self.transport.email[0]['content'])
        self.assertNotIn('a@example.test', self.transport.email[0]['content'])

    def test_verification_token_generated_at_send_and_only_hash_persisted(self):
        with self.connect() as db:
            queued = notifications.request_email_verification(db, 'candidate-a', self.config, 10)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_tokens').fetchone()[0], 0)
        self.assertTrue(queued['queued'])
        self.assertEqual(self.process()['delivered'], 1)
        raw = self.verification_token()
        self.assertEqual(len(raw), 43)
        self.assertIn('id=hr-vision-product--candidate', self.transport.email[0]['content'])
        self.assertIn('viewMode=story', self.transport.email[0]['content'])
        with self.connect() as db:
            storage = '\n'.join(db.iterdump())
            self.assertNotIn(raw, storage)
            self.assertIn(notifications._hash(raw), storage)
            self.assertNotIn('candidate-a', self.transport.email[0]['content'])

    def test_verification_requires_same_authenticated_candidate_and_single_use(self):
        with self.connect() as db:
            notifications.request_email_verification(db, 'candidate-a', self.config, 10)
        self.process()
        token = self.verification_token()
        with self.connect() as db, self.assertRaises(Problem):
            notifications.verify_email(db, 'candidate-b', token, self.config, 120)
        with self.connect() as db:
            status = notifications.verify_email(db, 'candidate-a', token, self.config, 120)
        self.assertTrue(status['email']['verified'])
        with self.connect() as db, self.assertRaises(Problem):
            notifications.verify_email(db, 'candidate-a', token, self.config, 121)

    def test_verification_expires_and_email_change_invalidates_token(self):
        with self.connect() as db:
            notifications.request_email_verification(db, 'candidate-a', self.config, 10)
        self.process()
        token = self.verification_token()
        with self.connect() as db, self.assertRaises(Problem):
            notifications.verify_email(db, 'candidate-a', token, self.config, 1900)
        with self.connect() as db:
            notifications.set_email(db, 'candidate-a', 'new@example.test', now=130)
        with self.connect() as db, self.assertRaises(Problem):
            notifications.verify_email(db, 'candidate-a', token, self.config, 140)

    def test_verification_resend_cooldown_and_hourly_cap(self):
        with self.connect() as db:
            self.assertTrue(notifications.request_email_verification(db, 'candidate-a', self.config, 10)['queued'])
            denied = notifications.request_email_verification(db, 'candidate-a', self.config, 20)
            self.assertEqual(denied, {'status': 'rate_limited', 'queued': False, 'retryAfter': 50})
            for timestamp in (71, 132, 193, 254):
                self.assertTrue(notifications.request_email_verification(db, 'candidate-a', self.config, timestamp)['queued'])
            capped = notifications.request_email_verification(db, 'candidate-a', self.config, 315)
        self.assertEqual(capped['status'], 'rate_limited')
        self.assertGreater(capped['retryAfter'], 60)

    def test_pending_email_cannot_move_to_a_changed_address(self):
        self.verify_fixture()
        with self.connect() as db:
            notifications.enqueue(db, 'candidate-a', 'event:one', 'interview.booked', 'email', self.config, 10)
            notifications.set_email(db, 'candidate-a', 'new@example.test', verified=True, now=20)
        self.assertEqual(self.process()['error'], 1)
        self.assertEqual(self.transport.email, [])
        self.assertEqual(self.rows()[0]['last_error'], 'recipient_changed')

    def test_telegram_link_is_hashed_expires_and_requires_webhook_secret(self):
        token = self.telegram_link()
        with self.connect() as db:
            self.assertNotIn(token, '\n'.join(db.iterdump()))
        for secret in ('', 'wrong', 'секрет'):
            with self.connect() as db, self.assertRaises(Problem):
                notifications.handle_telegram_update(db, self.update(token), secret, self.config, 11)
        with self.connect() as db:
            expired = notifications.handle_telegram_update(db, self.update(token), self.config.telegram_webhook_secret, self.config, 610)
        self.assertEqual(expired['status'], 'invalid_link')

    def test_telegram_binding_only_private_verified_sender(self):
        token = self.telegram_link()
        bad_group = self.update(token)
        bad_group['message']['chat']['type'] = 'group'
        wrong_sender = self.update(token, update_id=2)
        wrong_sender['message']['from']['id'] = 2020
        with self.connect() as db:
            self.assertEqual(notifications.handle_telegram_update(db, bad_group, self.config.telegram_webhook_secret, self.config, 20)['status'], 'ignored')
            self.assertEqual(notifications.handle_telegram_update(db, wrong_sender, self.config.telegram_webhook_secret, self.config, 20)['status'], 'ignored')
            linked = notifications.handle_telegram_update(db, self.update(token, 3), self.config.telegram_webhook_secret, self.config, 21)
            repeat = notifications.handle_telegram_update(db, self.update(token, 3), self.config.telegram_webhook_secret, self.config, 22)
            reused = notifications.handle_telegram_update(db, self.update(token, 4, 2002), self.config.telegram_webhook_secret, self.config, 23)
            status = notifications.public_status(db, 'candidate-a', self.config)
        self.assertEqual(linked['status'], 'linked')
        self.assertEqual(repeat['status'], 'duplicate')
        self.assertEqual(reused['status'], 'invalid_link')
        self.assertTrue(status['telegram']['linked'])
        self.assertNotIn('1001', json.dumps(status))

    def test_telegram_cannot_silently_bind_one_chat_to_different_candidate(self):
        self.link_fixture()
        other = self.telegram_link('candidate-b', 30)
        with self.connect() as db:
            result = notifications.handle_telegram_update(db, self.update(other, 2), self.config.telegram_webhook_secret, self.config, 40)
            status = notifications.public_status(db, 'candidate-b', self.config)
        self.assertEqual(result['status'], 'chat_already_linked')
        self.assertFalse(status['telegram']['linked'])

    def test_channel_failure_does_not_resend_other_channel(self):
        self.verify_fixture()
        self.link_fixture()
        with self.connect() as db:
            notifications.enqueue(db, 'candidate-a', 'meeting:one', 'meeting.confirmed', settings=self.config, now=25)
        self.transport.telegram_failure = notifications.DeliveryFailure('telegram_http_429', True, 60)
        result = self.process(100)
        self.assertEqual(result['delivered'], 1)
        self.assertEqual(result['pending'], 1)
        self.assertEqual(self.process(120)['delivered'], 0)
        self.transport.telegram_failure = None
        self.assertEqual(self.process(160)['delivered'], 1)
        self.assertEqual(len(self.transport.email), 1)
        self.assertTrue(all(row['status'] == 'delivered' for row in self.rows()))

    def test_retries_are_bounded_and_manual_retry_explicit(self):
        self.verify_fixture()
        with self.connect() as db:
            result = notifications.enqueue(db, 'candidate-a', 'booking:one', 'interview.booked', 'email', self.config, 10)
        self.transport.email_failure = notifications.DeliveryFailure('smtp_rejected', True)
        self.assertEqual(self.process(100)['pending'], 1)
        self.assertEqual(self.process(130)['pending'], 1)
        self.assertEqual(self.process(190)['error'], 1)
        self.process(1000)
        self.assertEqual(len(self.transport.email), 3)
        with self.connect() as db, self.assertRaises(Problem):
            notifications.retry(db, result[0]['id'], self.config, 1001, candidate_id='candidate-b')
        with self.connect() as db:
            notifications.retry(db, result[0]['id'], self.config, 1002, candidate_id='candidate-a')
        self.transport.email_failure = None
        self.assertEqual(self.process(1003)['delivered'], 1)

    def test_expired_inflight_is_uncertain_not_automatically_resent(self):
        self.verify_fixture()
        with self.connect() as db:
            notifications.enqueue(db, 'candidate-a', 'event:one', 'interview.completed', 'email', self.config, 10)
            db.execute("UPDATE cn_outbox SET status='sending',lease_id='interrupted',lease_until=50,attempts=1")
        self.process(100)
        self.assertEqual(self.transport.email, [])
        self.assertEqual(self.rows()[0]['status'], 'error')
        self.assertEqual(self.rows()[0]['last_error'], 'delivery_uncertain')

    def test_unexpected_provider_error_does_not_persist_secret_text(self):
        self.verify_fixture()
        with self.connect() as db:
            notifications.enqueue(db, 'candidate-a', 'event:one', 'interview.completed', 'email', self.config, 10)
        self.transport.email_failure = RuntimeError('credential=private-provider-secret')
        self.assertEqual(self.process()['error'], 1)
        self.assertEqual(self.rows()[0]['last_error'], 'delivery_uncertain')
        self.assertNotIn('private-provider-secret', json.dumps(self.rows()))

    def test_parallel_workers_claim_each_email_once(self):
        self.verify_fixture()
        with self.connect() as db:
            for index in range(20):
                notifications.enqueue(db, 'candidate-a', 'event:' + str(index), 'interview.completed', 'email', self.config, 10)
        lock = threading.Lock()
        delivered = []
        def send(recipient, subject, content, identifier):
            time.sleep(0.002)
            with lock:
                delivered.append(identifier)
            return 'accepted'
        self.transport.send_email = send
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.process(limit=20), range(2)))
        self.assertEqual(sum(item['delivered'] for item in results), 20)
        self.assertEqual(len(set(delivered)), 20)
        self.assertEqual(len(delivered), 20)

    def test_polling_consumes_verified_updates_and_persists_offset(self):
        token = self.telegram_link()
        config = replace(self.config, telegram_polling=True)
        self.transport.updates = [self.update(token, update_id=500)]
        result = notifications.poll_telegram_updates(self.connect, config, 20, self.transport)
        self.assertEqual(result['processed'], 1)
        notifications.poll_telegram_updates(self.connect, config, 21, self.transport)
        self.assertEqual(self.transport.requests[-1][1]['offset'], 501)
        with self.connect() as db:
            self.assertTrue(notifications.public_status(db, 'candidate-a', config)['telegram']['linked'])

    def test_real_smtp_adapter_requires_and_validates_tls(self):
        server = MagicMock()
        server.__enter__.return_value = server
        server.send_message.return_value = {}
        with patch('candidate_notifications.smtplib.SMTP', return_value=server) as smtp:
            result = notifications.Transport(self.config).send_email('a@example.test', 'HR Vision', 'generic', 'id-1')
        self.assertEqual(result, 'smtp_accepted')
        smtp.assert_called_once_with('smtp.example.test', 587, timeout=15)
        self.assertEqual(server.ehlo.call_count, 2)
        self.assertTrue(server.starttls.call_args.kwargs['context'].check_hostname)
        server.login.assert_called_once_with('test-user', 'never-a-real-password')
        message = server.send_message.call_args.args[0]
        self.assertEqual(message['To'], 'a@example.test')

    def test_real_adapters_sanitize_rejections(self):
        error = urllib.error.HTTPError('https://api.telegram.org/bot-secret/sendMessage', 429, 'secret-body', {}, io.BytesIO(b'not logged'))
        with patch('candidate_notifications.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(notifications.DeliveryFailure) as caught:
                notifications.Transport(self.config).send_telegram('1001', 'generic')
        self.assertEqual(caught.exception.code, 'telegram_http_429')
        self.assertTrue(caught.exception.retryable)
        with patch('candidate_notifications.smtplib.SMTP', side_effect=smtplib.SMTPAuthenticationError(535, b'secret')):
            with self.assertRaises(notifications.DeliveryFailure) as caught:
                notifications.Transport(self.config).send_email('a@example.test', 'HR', 'generic', 'id')
        self.assertEqual(caught.exception.code, 'smtp_authentication')
        self.assertFalse(caught.exception.retryable)

    def test_server_worker_is_off_without_explicit_enable(self):
        with patch.dict('os.environ', {}, clear=True), patch('candidate_notifications.threading.Thread') as thread:
            self.assertIsNone(notifications.start_worker(self.connect))
        thread.assert_not_called()

    def test_server_worker_runs_daemon_and_handles_failure_without_exposing_details(self):
        stop, seen = threading.Event(), threading.Event()
        def fail(*args, **kwargs):
            seen.set()
            raise RuntimeError('smtp-password=must-not-be-logged')
        with patch.dict('os.environ', {'HR_NOTIFICATIONS_ENABLED': '1'}, clear=True), \
             patch('candidate_notifications.process_pending', side_effect=fail), \
             patch('builtins.print') as output:
            worker = notifications.start_worker(self.connect, interval=2, stop_event=stop)
            self.assertTrue(seen.wait(2))
            self.assertTrue(worker.daemon)
            self.assertIs(notifications.start_worker(self.connect), worker)
            stop.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertNotIn('must-not-be-logged', str(output.call_args_list))


if __name__ == '__main__':
    unittest.main()
