import concurrent.futures
from dataclasses import replace
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

import candidate_notifications as delivery
import recruiter_notifications as notifications
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
        self.failure = None
        self.updates = []

    def send_email(self, recipient, subject, content, message_id):
        self.email.append({'recipient': recipient, 'subject': subject, 'content': content, 'id': message_id})
        if self.failure:
            raise self.failure
        return 'smtp_accepted'

    def send_telegram(self, recipient, content):
        self.telegram.append({'recipient': recipient, 'content': content})
        return 'message-1'

    def telegram_request(self, method, payload):
        return self.updates


class RecruiterNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'notifications.sqlite3'
        self.addCleanup(self.directory.cleanup)
        self.config = replace(notifications.settings({'HR_ORIGIN': 'https://hr.example.test'}),
                              enabled=True, smtp_host='smtp.example.test', smtp_from='hr@example.test',
                              smtp_user='test-user', smtp_password='not-real', telegram_token='not-real',
                              telegram_username='HRVisionTestBot',
                              telegram_webhook_secret='test-webhook-secret-01234567890123456789')
        self.transport = FakeTransport()
        for target in ('candidate_notifications.urllib.request.urlopen',
                       'candidate_notifications.smtplib.SMTP', 'candidate_notifications.smtplib.SMTP_SSL'):
            mock = patch(target, side_effect=AssertionError('Unexpected real provider call'))
            mock.start()
            self.addCleanup(mock.stop)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE rc_accounts(id TEXT PRIMARY KEY,email TEXT NOT NULL,name TEXT,active INTEGER);
                CREATE TABLE cp_candidates(id TEXT PRIMARY KEY,profile TEXT,email TEXT);
                CREATE TABLE cp_intake(candidate_id TEXT PRIMARY KEY,source TEXT);
                CREATE TABLE cp_recruiter_slots(id TEXT PRIMARY KEY,start TEXT,end TEXT,recruiter_name TEXT,candidate_id TEXT);
                CREATE TABLE cp_recruiter_slot_owners(slot_id TEXT PRIMARY KEY,recruiter_id TEXT REFERENCES rc_accounts(id));
                CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,candidate_id TEXT,slot_id TEXT,status TEXT);
                INSERT INTO rc_accounts VALUES ('r-a','a@example.test','Same Name',1),('r-b','b@example.test','Same Name',1);
                INSERT INTO cp_candidates VALUES ('c-a','{"name":"Анна Миронова","about":"private CV details","salaryMin":"100000"}','candidate@example.test');
                INSERT INTO cp_intake VALUES ('c-a','hh');
                INSERT INTO cp_recruiter_slots VALUES ('s-a','2099-10-14T13:00:00Z','2099-10-14T13:30:00Z','Same Name','c-a');
                INSERT INTO cp_recruiter_slot_owners VALUES ('s-a','r-a');
                INSERT INTO cp_interviews VALUES ('i-a','c-a','s-a','booked');
            ''')
            delivery.init(db)
            notifications.init(db)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def enqueue(self, now=30, config=None):
        with self.connect() as db:
            return notifications.enqueue_booking(db, 'r-a', 'i-a', config or self.config, now)

    def rows(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM rn_outbox ORDER BY rowid')]

    def process(self, now=100, config=None, limit=10):
        return notifications.process_pending(self.connect, config or self.config, limit, now, self.transport)

    def verify_fixture(self):
        with self.connect() as db:
            notifications.public_status(db, 'r-a', self.config)
            db.execute("UPDATE rn_channels SET email_verified=1 WHERE recruiter_id='r-a'")

    def link_token(self, recruiter='r-a', now=10):
        with self.connect() as db:
            response = notifications.create_telegram_link(db, recruiter, self.config, now)
        return urllib.parse.parse_qs(urllib.parse.urlsplit(response['url']).query)['start'][0]

    @staticmethod
    def update(token, update_id=1, chat_id=1001):
        return {'update_id': update_id, 'message': {'text': '/start ' + token,
                'chat': {'id': chat_id, 'type': 'private'}, 'from': {'id': chat_id, 'is_bot': False}}}

    def consume(self, update, now=20):
        with self.connect() as db:
            return delivery.handle_telegram_update(db, update, self.config.telegram_webhook_secret, self.config, now)

    def link_fixture(self):
        self.assertEqual(self.consume(self.update(self.link_token()))['status'], 'linked')

    def verification_token(self):
        link = re.search(r'https://\S+', self.transport.email[-1]['content'])[0]
        return urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)['recruiterVerify'][0]

    def test_public_status_no_secrets(self):
        self.assertIn('workspace=recruiter', self.config.public_url)
        self.assertEqual(notifications.settings({'HR_TELEGRAM_PROXY_URL': 'socks5h://127.0.0.1:10808'}).telegram_proxy_url,
                         'socks5h://127.0.0.1:10808')
        with self.connect() as db:
            status = notifications.public_status(db, 'r-a', self.config)
        self.assertEqual(status['email']['address'], 'a***@example.test')
        self.assertEqual(status['email']['status'], 'ready')
        self.assertFalse(status['email']['verified'])
        self.assertEqual(status['telegram']['status'], 'unbound')
        self.assertNotIn('not-real', json.dumps(status))
        self.assertNotIn('telegram_chat_id', json.dumps(status))

    def test_owner_is_exact_slot_identity_not_same_display_name(self):
        with self.connect() as db, self.assertRaises(Problem) as failure:
            notifications.enqueue_booking(db, 'r-b', 'i-a', self.config, 10)
        self.assertEqual(failure.exception.status, 404)
        self.assertEqual(self.rows(), [])
        self.enqueue()
        self.assertTrue(all(row['recruiter_id'] == 'r-a' for row in self.rows()))
        self.assertEqual(self.rows()[0]['recipient'], 'a@example.test')

    def test_idempotent_booking_has_two_channel_rows(self):
        self.assertEqual(self.enqueue(10), self.enqueue(20))
        self.assertEqual(len(self.rows()), 2)

    def test_caller_rollback_undoes_notifications_and_channels(self):
        db = self.connect()
        try:
            db.execute('BEGIN')
            notifications.init(db)
            notifications.enqueue_booking(db, 'r-a', 'i-a', self.config, 10)
            db.rollback()
        finally:
            db.close()
        self.assertEqual(self.rows(), [])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM rn_channels').fetchone()[0], 0)

    def test_provisioned_recruiter_gets_email_without_channel_setup(self):
        self.assertEqual([r['status'] for r in self.enqueue()], ['pending', 'unbound'])
        self.assertEqual(self.process()['delivered'], 1)
        self.assertEqual(self.transport.email[0]['recipient'], 'a@example.test')
        self.assertEqual(self.transport.telegram, [])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT email_verified FROM rn_channels').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM rn_tokens').fetchone()[0], 0)
        self.assertEqual(self.process()['delivered'], 0)

    def test_missing_smtp_does_not_claim_delivery_or_replay_old_events(self):
        result = self.enqueue(config=replace(self.config, smtp_host=''))
        self.assertEqual(result[0]['status'], 'disabled')
        self.assertEqual(self.process()['delivered'], 0)
        self.assertEqual(self.transport.email, [])

    def test_old_unverified_events_are_not_replayed_after_upgrade(self):
        self.enqueue()
        with self.connect() as db:
            db.execute("UPDATE rn_outbox SET status='unverified' WHERE channel='email'")
        self.assertEqual(self.process()['delivered'], 0)
        self.assertEqual(self.transport.email, [])

    def test_booked_message_contains_real_time_source_and_minimal_profile(self):
        self.verify_fixture()
        self.link_fixture()
        self.enqueue()
        self.assertEqual(self.process()['delivered'], 2)
        content = self.transport.email[-1]['content']
        for expected in ('Анна Миронова', '14.10.2099 16:00–16:30 МСК', 'Длительность: 30 минут',
                         'Источник: HH', 'workspace=recruiter'):
            self.assertIn(expected, content)
        for private in ('private CV details', '100000', 'c-a'):
            self.assertNotIn(private, content)
        self.assertEqual(self.transport.telegram[-1]['recipient'], '1001')
        self.assertEqual(self.process()['delivered'], 0)

    def test_missing_profile_name_is_not_guessed(self):
        with self.connect() as db:
            db.execute("UPDATE cp_candidates SET profile='{}'")
            db.execute('DELETE FROM cp_intake')
        self.verify_fixture()
        self.enqueue()
        self.process()
        self.assertIn('Кандидат: Кандидат', self.transport.email[-1]['content'])
        self.assertIn('Источник: Регистрация HR Vision', self.transport.email[-1]['content'])

    def test_disabled_rows_do_not_automatically_replay(self):
        self.verify_fixture()
        result = self.enqueue(config=replace(self.config, enabled=False))
        self.assertTrue(all(row['status'] == 'disabled' for row in result))
        self.assertEqual(self.process()['delivered'], 0)
        with self.connect() as db:
            notifications.retry(db, 'r-a', result[0]['id'], self.config, 100)
        self.assertEqual(self.process(101)['delivered'], 1)

    def test_email_verification_generic_hashed_single_use_and_same_account(self):
        with self.connect() as db:
            self.assertTrue(notifications.request_email_verification(db, 'r-a', self.config, 10)['queued'])
            self.assertEqual(db.execute('SELECT count(*) FROM rn_tokens').fetchone()[0], 0)
        self.assertEqual(self.process()['delivered'], 1)
        token = self.verification_token()
        self.assertNotIn('Анна', self.transport.email[-1]['content'])
        self.assertIn('workspace=recruiter', self.transport.email[-1]['content'])
        with self.connect() as db:
            self.assertNotIn(token, '\n'.join(db.iterdump()))
            with self.assertRaises(Problem):
                notifications.verify_email(db, 'r-b', token, self.config, 101)
            self.assertTrue(notifications.verify_email(db, 'r-a', token, self.config, 101)['email']['verified'])
            with self.assertRaises(Problem):
                notifications.verify_email(db, 'r-a', token, self.config, 102)

    def test_email_rate_limit_and_expired_token(self):
        with self.connect() as db:
            notifications.request_email_verification(db, 'r-a', self.config, 10)
            self.assertEqual(notifications.request_email_verification(db, 'r-a', self.config, 11)['status'], 'rate_limited')
        self.process(100)
        with self.connect() as db, self.assertRaises(Problem):
            notifications.verify_email(db, 'r-a', self.verification_token(), self.config, 2000)

    def test_changed_email_stops_old_recipient_delivery(self):
        self.verify_fixture()
        self.enqueue()
        with self.connect() as db:
            db.execute("UPDATE rc_accounts SET email='new@example.test' WHERE id='r-a'")
        self.assertEqual(self.process()['error'], 1)
        self.assertEqual(self.transport.email, [])
        with self.connect() as db:
            self.assertFalse(notifications.public_status(db, 'r-a', self.config)['email']['verified'])

    def test_shared_webhook_requires_secret_and_routes_recruiter_token(self):
        token = self.link_token()
        self.assertTrue(token.startswith('rc_'))
        with self.connect() as db:
            self.assertNotIn(token[3:], '\n'.join(db.iterdump()))
            with self.assertRaises(Problem) as failure:
                delivery.handle_telegram_update(db, self.update(token), 'wrong-secret', self.config, 20)
            self.assertEqual(failure.exception.status, 403)
        def candidate_handler(*args):
            self.fail('Recruiter binding was incorrectly sent to the candidate bot')
        with self.connect() as db:
            result = delivery.handle_telegram_update(db, self.update(token), self.config.telegram_webhook_secret,
                                                    self.config, 20, update_handler=candidate_handler)
            self.assertEqual(result['status'], 'linked')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cn_recipients').fetchone()[0], 0)
            self.assertTrue(notifications.public_status(db, 'r-a', self.config)['telegram']['linked'])

    def test_telegram_link_duplicate_and_replay(self):
        token = self.link_token()
        self.assertEqual(self.consume(self.update(token))['status'], 'linked')
        self.assertEqual(self.consume(self.update(token))['status'], 'duplicate')
        self.assertEqual(self.consume(self.update(token, 2))['status'], 'invalid_link')

    def test_telegram_private_sender_only_expiry_and_superseded_tokens(self):
        old, token = self.link_token(), self.link_token(now=11)
        self.assertEqual(self.consume(self.update(old))['status'], 'invalid_link')
        group = self.update(token, 2)
        group['message']['chat']['type'] = 'group'
        self.assertEqual(self.consume(group)['status'], 'ignored')
        mismatch = self.update(token, 3)
        mismatch['message']['from']['id'] = 999
        self.assertEqual(self.consume(mismatch)['status'], 'ignored')
        self.assertEqual(self.consume(self.update(token, 4), 612)['status'], 'invalid_link')

    def test_telegram_chat_cannot_link_another_recruiter(self):
        self.link_fixture()
        token = self.link_token('r-b')
        self.assertEqual(self.consume(self.update(token, 2))['status'], 'chat_already_linked')
        self.assertEqual(self.consume(self.update(token, 3, 2002))['status'], 'linked')

    def test_shared_polling_routes_both_identity_namespaces(self):
        token = self.link_token()
        with self.connect() as db:
            delivery.set_email(db, 'c-a', 'candidate@example.test', now=1)
            link = delivery.create_telegram_link(db, 'c-a', self.config, 10)
        candidate_token = urllib.parse.parse_qs(urllib.parse.urlsplit(link['url']).query)['start'][0]
        self.transport.updates = [self.update(token, 1), self.update(candidate_token, 2, 2002)]
        result = delivery.poll_telegram_updates(self.connect, replace(self.config, telegram_polling=True), 20, self.transport)
        self.assertEqual(result['processed'], 2)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT value FROM cn_worker_state WHERE name='telegram_offset'").fetchone()[0], '3')
            self.assertTrue(notifications.public_status(db, 'r-a', self.config)['telegram']['linked'])
            self.assertTrue(delivery.public_status(db, 'c-a', self.config)['telegram']['linked'])

    def test_changed_owner_completed_or_past_interview_not_notified(self):
        self.verify_fixture()
        row = self.enqueue()[0]
        for mutation in ("UPDATE cp_interviews SET status='completed'", "UPDATE cp_recruiter_slot_owners SET recruiter_id='r-b'", "UPDATE rc_accounts SET active=0 WHERE id='r-a'"):
            with self.connect() as db:
                db.execute("UPDATE cp_interviews SET status='booked'")
                db.execute("UPDATE cp_recruiter_slot_owners SET recruiter_id='r-a'")
                notifications.retry(db, 'r-a', row['id'], self.config, 100)
                db.execute(mutation)
            self.assertEqual(self.process()['cancelled'], 1)
        self.assertEqual(self.transport.email, [])

    def test_past_interview_notification_not_sent(self):
        self.verify_fixture()
        self.enqueue()
        self.assertEqual(self.process(9999999999)['cancelled'], 1)

    def test_retry_uses_same_message_id_and_no_duplicate_success(self):
        self.verify_fixture()
        self.enqueue()
        self.transport.failure = delivery.DeliveryFailure('smtp_rejected', retryable=True)
        self.assertEqual(self.process()['pending'], 1)
        self.assertEqual(self.process(110)['pending'], 0)
        self.transport.failure = None
        self.assertEqual(self.process(130)['delivered'], 1)
        self.assertEqual(self.transport.email[0]['id'], self.transport.email[1]['id'])
        self.assertEqual(self.process(200)['delivered'], 0)

    def test_uncertain_failure_never_automatically_retries(self):
        self.verify_fixture()
        self.enqueue()
        self.transport.failure = delivery.DeliveryFailure('smtp_delivery_uncertain')
        self.assertEqual(self.process()['error'], 1)
        self.transport.failure = None
        self.assertEqual(self.process(500)['delivered'], 0)
        self.assertEqual(len(self.transport.email), 1)

    def test_abandoned_sending_lease_quarantined(self):
        self.verify_fixture()
        self.enqueue()
        with self.connect() as db:
            db.execute("UPDATE rn_outbox SET status='sending',lease_id='crashed',lease_until=50 WHERE channel='email'")
        self.process()
        self.assertEqual(self.rows()[0]['status'], 'error')
        self.assertEqual(self.rows()[0]['last_error'], 'delivery_uncertain')
        self.assertEqual(self.transport.email, [])

    def test_competing_workers_only_send_once_and_network_outside_db_lock(self):
        self.verify_fixture()
        self.enqueue()
        original = self.transport.send_email
        def check_lock(*args):
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE rc_accounts SET name='Still editable' WHERE id='r-b'")
            return original(*args)
        self.transport.send_email = check_lock
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.process(), range(2)))
        self.assertEqual(sum(r['delivered'] for r in results), 1)
        self.assertEqual(len(self.transport.email), 1)

    def test_retry_cannot_access_another_recruiter_or_resend_success(self):
        self.verify_fixture()
        row = self.enqueue()[0]
        with self.connect() as db, self.assertRaises(Problem) as failure:
            notifications.retry(db, 'r-b', row['id'], self.config, 100)
        self.assertEqual(failure.exception.status, 404)
        self.process()
        with self.connect() as db:
            self.assertEqual(notifications.retry(db, 'r-a', row['id'], self.config, 101)['status'], 'delivered')
        self.assertEqual(self.process(102)['delivered'], 0)


if __name__ == '__main__':
    unittest.main()
