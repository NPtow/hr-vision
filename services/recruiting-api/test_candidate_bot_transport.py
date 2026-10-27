import copy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.parse
from unittest.mock import patch

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
        self.sent = []
        self.requests = []
        self.updates = []
        self.failure = None

    def send_telegram(self, recipient, content, reply_markup=None):
        self.sent.append(('message', recipient, content, reply_markup))
        if self.failure:
            raise self.failure
        return '123'

    def answer_callback_query(self, callback_query_id, text='', show_alert=False):
        self.sent.append(('callback', callback_query_id, text, show_alert))
        if self.failure:
            raise self.failure
        return 'telegram_callback_accepted'

    def send_email(self, recipient, subject, content, message_id):
        self.sent.append(('email', recipient, content, message_id))
        return 'smtp_accepted'

    def telegram_request(self, method, payload):
        self.requests.append((method, payload))
        return self.updates


class CandidateBotTransportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'bot.sqlite3'
        self.config = notifications.Settings(
            enabled=True, smtp_host='smtp.example.test', smtp_from='hr@example.test',
            telegram_token='123:not-a-real-token', telegram_username='HRVisionTestBot',
            telegram_webhook_secret='webhook-secret-01234567890123456789', telegram_polling=True)
        self.transport = FakeTransport()
        self.keyboard = {'inline_keyboard': [[{'text': 'Продолжить', 'callback_data': 'opaque-action'}]]}
        self.net = patch('candidate_notifications.urllib.request.urlopen', side_effect=AssertionError('Unexpected network'))
        self.net.start()
        self.addCleanup(self.net.stop)
        with self.connect() as db:
            notifications.init(db)
            notifications.set_email(db, 'candidate-a', 'a@example.test', verified=True, now=1)
            notifications.set_email(db, 'candidate-b', 'b@example.test', verified=True, now=1)
            db.execute("UPDATE cn_recipients SET telegram_chat_id='1001',telegram_verified_at=1 WHERE candidate_id='candidate-a'")

    def connect(self):
        return sqlite3.connect(self.path, factory=ClosingConnection, timeout=5)

    def rows(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM cn_outbox ORDER BY rowid')]

    def reply(self, db, event='bot:1:0', candidate='candidate-a', config=None, text='Сохранено.'):
        return notifications.enqueue_telegram_reply(db, 1001, event, text, self.keyboard,
                                                     candidate, config or self.config, 10)

    def process(self, now=20, renderer=None):
        return notifications.process_pending(self.connect, self.config, now=now, transport=self.transport,
                                              telegram_renderer=renderer)

    def update(self, update_id=1, callback=False):
        chat = {'id': 1001, 'type': 'private'}
        sender = {'id': 1001, 'is_bot': False}
        if callback:
            return {'update_id': update_id, 'callback_query': {'id': 'query-1', 'from': sender,
                    'data': 'opaque-action', 'message': {'message_id': 2, 'chat': chat,
                    'from': {'id': 999, 'is_bot': True}}}}
        return {'update_id': update_id, 'message': {'chat': chat, 'from': sender, 'text': '/menu'}}

    def handle(self, db, update, handler):
        return notifications.handle_telegram_update(db, update, self.config.telegram_webhook_secret,
                                                     self.config, 10, update_handler=handler)

    def test_old_schema_migrates_without_committing_caller_transaction(self):
        with self.connect() as db:
            db.execute('ALTER TABLE cn_outbox DROP COLUMN payload_json')
        db = self.connect()
        try:
            db.execute("INSERT INTO cn_worker_state VALUES ('migration', 'uncommitted')")
            notifications.init(db)
            self.reply(db)
            db.rollback()
            self.assertIsNone(db.execute("SELECT value FROM cn_worker_state WHERE name='migration'").fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0], 0)
            notifications.init(db)
            self.reply(db)
            db.commit()
        finally:
            db.close()
        self.assertEqual(self.process()['delivered'], 1)

    def test_bot_replies_are_durable_and_idempotent(self):
        with self.connect() as db:
            first = self.reply(db)
            self.assertEqual(self.reply(db), first)
        self.assertEqual(self.transport.sent, [])
        self.assertEqual(self.process()['delivered'], 1)
        self.assertEqual(self.process()['delivered'], 0)
        self.assertEqual(self.transport.sent, [('message', '1001', 'Сохранено.', self.keyboard)])
        self.assertEqual(self.rows()[0]['status'], 'delivered')

    def test_outbox_same_event_cannot_change_content_or_recipient(self):
        with self.connect() as db:
            self.reply(db, candidate=None)
        with self.connect() as db, self.assertRaises(Problem) as content_error:
            self.reply(db, candidate=None, text='Changed')
        self.assertEqual(content_error.exception.status, 409)
        with self.connect() as db, self.assertRaises(Problem) as recipient_error:
            notifications.enqueue_telegram_reply(db, 1002, 'bot:1:0', 'Сохранено.', self.keyboard,
                                                 settings=self.config, now=10)
        self.assertEqual(recipient_error.exception.status, 409)

    def test_candidate_content_requires_own_linked_chat(self):
        with self.connect() as db, self.assertRaises(Problem) as wrong_owner:
            self.reply(db, candidate='candidate-b')
        self.assertEqual(wrong_owner.exception.status, 403)
        for chat in (-1001, True, '@channel', '01', '1001\n', 2 ** 52):
            with self.subTest(chat=chat), self.connect() as db, self.assertRaises(Problem):
                notifications.enqueue_telegram_reply(db, chat, 'bot:1:0', 'Generic', settings=self.config)
        self.assertEqual(self.rows(), [])

    def test_changed_binding_quarantines_private_reply_and_cannot_retarget_retry(self):
        with self.connect() as db:
            queued = self.reply(db)
            db.execute("UPDATE cn_recipients SET telegram_chat_id='2002' WHERE candidate_id='candidate-a'")
        self.assertEqual(self.process()['error'], 1)
        self.assertEqual(self.transport.sent, [])
        self.assertEqual(self.rows()[0]['last_error'], 'recipient_changed')
        with self.connect() as db, self.assertRaises(Problem) as error:
            notifications.retry(db, queued['id'], self.config, 30, candidate_id='candidate-a')
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.rows()[0]['recipient'], '1001')

    def test_revoked_verification_blocks_queued_personal_replies_callbacks_and_lifecycle(self):
        with self.connect() as db:
            reply = self.reply(db)
            notifications.enqueue_telegram_callback(db, 1001, 'bot:1:ack', 'query-1', 'Личный ответ',
                                                    candidate_id='candidate-a', settings=self.config, now=10)
            lifecycle = notifications.enqueue(db, 'candidate-a', 'application:one', 'application.proposed',
                                               'telegram', self.config, 10)[0]
            db.execute("UPDATE cn_recipients SET telegram_verified_at=NULL WHERE candidate_id='candidate-a'")
        counts = self.process(renderer=lambda *args: self.fail('Revoked identity reached renderer'))
        self.assertEqual((counts['delivered'], counts['error'], counts['unbound']), (0, 2, 1))
        self.assertEqual(self.transport.sent, [])
        with self.connect() as db:
            status = notifications.public_status(db, 'candidate-a', self.config)
            self.assertFalse(status['telegram']['linked'])
            self.assertEqual(status['telegram']['status'], 'unbound')
            self.assertEqual(notifications.retry(db, lifecycle['id'], self.config, 30)['status'], 'unbound')
        with self.assertRaises(Problem):
            with self.connect() as db:
                notifications.retry(db, reply['id'], self.config, 30)
        with self.assertRaises(Problem):
            with self.connect() as db:
                self.reply(db, event='bot:2:0')
        self.assertEqual(self.process(now=31)['delivered'], 0)

    def test_canonical_account_must_remain_active_before_telegram_dispatch(self):
        with self.connect() as db:
            db.execute('CREATE TABLE cp_candidates (id TEXT PRIMARY KEY, email TEXT)')
            db.execute("INSERT INTO cp_candidates VALUES ('candidate-a','a@example.test')")
            self.reply(db, event='active:reply')
        self.assertEqual(self.process()['delivered'], 1)
        self.transport.sent.clear()
        for index, email in enumerate((None, '', 'deleted')):
            with self.subTest(email=email):
                prefix = 'case:' + str(index)
                with self.connect() as db:
                    db.execute("INSERT OR REPLACE INTO cp_candidates VALUES ('candidate-a','a@example.test')")
                    reply = self.reply(db, event=prefix + ':reply')
                    notifications.enqueue_telegram_callback(db, 1001, prefix + ':callback', 'query-' + str(index),
                        'Личный ответ', candidate_id='candidate-a', settings=self.config, now=10)
                    lifecycle = notifications.enqueue(db, 'candidate-a', prefix + ':lifecycle',
                        'application.proposed', 'telegram', self.config, 10)[0]
                    if email == 'deleted':
                        db.execute("DELETE FROM cp_candidates WHERE id='candidate-a'")
                    else:
                        db.execute("UPDATE cp_candidates SET email=? WHERE id='candidate-a'", (email,))
                counts = self.process(renderer=lambda *args: self.fail('Inactive identity reached renderer'))
                self.assertEqual((counts['delivered'], counts['error'], counts['unbound']), (0, 2, 1))
                self.assertEqual(self.transport.sent, [])
                with self.connect() as db:
                    self.assertFalse(notifications.public_status(db, 'candidate-a', self.config)['telegram']['linked'])
                    self.assertEqual(notifications.retry(db, lifecycle['id'], self.config, 30)['status'], 'unbound')
                    self.assertEqual(notifications.enqueue(db, 'candidate-a', prefix + ':new',
                        'application.proposed', 'telegram', self.config, 30)[0]['status'], 'unbound')
                with self.assertRaises(Problem):
                    with self.connect() as db:
                        notifications.retry(db, reply['id'], self.config, 30)
                with self.assertRaises(Problem):
                    with self.connect() as db:
                        self.reply(db, event=prefix + ':new-reply')

    def test_anonymous_disabled_reply_requires_explicit_retry(self):
        with self.connect() as db:
            queued = self.reply(db, candidate=None, config=notifications.Settings(), text='Откройте приглашение.')
        self.assertEqual(queued['status'], 'disabled')
        self.assertEqual(self.process()['delivered'], 0)
        with self.connect() as db:
            notifications.retry(db, queued['id'], self.config, 21)
        self.assertEqual(self.process(now=22)['delivered'], 1)

    def test_callback_acknowledgements_share_outbox_and_get_priority(self):
        with self.connect() as db:
            self.reply(db)
            notifications.enqueue_telegram_callback(db, 1001, 'bot:1:ack', 'query-1', 'Готово', True,
                                                    'candidate-a', self.config, 11)
        self.assertEqual(self.process()['delivered'], 2)
        self.assertEqual(self.transport.sent[0], ('callback', 'query-1', 'Готово', True))

    def test_markup_and_callback_parameters_are_bounded(self):
        invalid = [
            {'inline_keyboard': [[{'text': 'Go', 'callback_data': 'ы' * 33}]]},
            {'inline_keyboard': [[{'text': 'Go', 'callback_data': ''}]]},
            {'inline_keyboard': [[{'text': 'Go', 'callback_data': 'x', 'url': 'https://example.test'}]]},
            {'inline_keyboard': [[{'text': 'Go', 'url': 'http://example.test'}]]},
            {'inline_keyboard': [[{'text': 'Go', 'url': 'https://user:secret@example.test'}]]},
            {'keyboard': [['unexpected']]},
        ]
        for markup in invalid:
            with self.subTest(markup=markup), self.connect() as db, self.assertRaises(Problem):
                notifications.enqueue_telegram_reply(db, 1001, 'test', 'Generic', markup, settings=self.config)
        with self.connect() as db:
            notifications.enqueue_telegram_reply(db, 1001, 'valid', 'Generic',
                {'inline_keyboard': [[{'text': 'Go', 'callback_data': 'ы' * 32}]]}, settings=self.config)
        for text in ('', 'x' * 4097):
            with self.connect() as db, self.assertRaises(Problem):
                notifications.enqueue_telegram_reply(db, 1001, 'invalid-text', text, settings=self.config)
        for query, text, alert in (('', '', False), ('query', 'x' * 201, False), ('query', '', 'true')):
            with self.connect() as db, self.assertRaises(Problem):
                notifications.enqueue_telegram_callback(db, 1001, 'invalid-callback', query, text, alert,
                                                        settings=self.config)

    def test_reply_failure_remains_secret_safe_and_retries_are_bounded(self):
        with self.connect() as db:
            self.reply(db)
        self.transport.failure = notifications.DeliveryFailure('telegram_http_429', True, 60)
        self.assertEqual(self.process(now=20)['pending'], 1)
        self.assertEqual(self.process(now=80)['pending'], 1)
        self.assertEqual(self.process(now=140)['error'], 1)
        self.assertEqual(self.process(now=1000)['delivered'], 0)
        self.assertEqual(len(self.transport.sent), 3)
        with self.connect() as db:
            self.reply(db, event='bot:2:0')
        self.transport.failure = RuntimeError('bot-token=secret-provider-body')
        self.assertEqual(self.process()['error'], 1)
        self.assertNotIn('secret-provider-body', json.dumps(self.rows()))

    def test_uncertain_bot_delivery_is_not_resent(self):
        with self.connect() as db:
            self.reply(db)
            db.execute("UPDATE cn_outbox SET status='sending',attempts=1,lease_until=5,lease_id='lost'")
        self.process()
        self.assertEqual(self.transport.sent, [])
        self.assertEqual(self.rows()[0]['last_error'], 'delivery_uncertain')

    def test_handler_receives_callback_sender_not_bot_message_author(self):
        seen = []
        def handler(db, update, timestamp, config, binding_result):
            seen.append((update, timestamp, config, binding_result))
            self.reply(db)
            return {'status': 'handled'}
        update = self.update(callback=True)
        with self.connect() as db:
            self.assertEqual(self.handle(db, update, handler), {'status': 'handled'})
            self.assertEqual(self.handle(db, update, handler), {'status': 'duplicate'})
        self.assertEqual(seen, [(update, 10, self.config, None)])
        self.assertEqual(len(self.rows()), 1)

    def test_invalid_callback_never_reaches_handler(self):
        valid = self.update(callback=True)
        variants = []
        for path, value in ((('message', 'chat', 'type'), 'group'), (('from', 'id'), 999),
                            (('from', 'is_bot'), True), (('from', 'id'), True),
                            (('data',), 'ы' * 33), (('id',), ''), (('message',), None)):
            item = copy.deepcopy(valid)
            target = item['callback_query']
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            variants.append(item)
        variants.append({'update_id': 50, 'callback_query': {'id': 'q', 'from': {'id': 1001},
                         'data': 'action', 'inline_message_id': 'inline'}})
        for index, update in enumerate(variants):
            update['update_id'] = index
            with self.subTest(index=index), self.connect() as db:
                with patch('builtins.print') as output:
                    result = self.handle(db, update, lambda *args: self.fail('Untrusted identity reached handler'))
                self.assertEqual(result['status'], 'ignored')
                output.assert_not_called()

    def test_binding_result_precedes_handler_and_rollback_releases_update_id(self):
        with self.connect() as db:
            link = notifications.create_telegram_link(db, 'candidate-b', self.config, 1)
        token = link['url'].split('?start=')[1]
        update = self.update()
        update['message']['chat']['id'] = update['message']['from']['id'] = 2002
        update['message']['text'] = '/start ' + token
        seen = []
        def fail(db, update, timestamp, config, binding_result):
            seen.append(binding_result)
            notifications.enqueue_telegram_reply(db, 2002, 'bot:1:0', 'Связано', candidate_id='candidate-b',
                                                 settings=config, now=timestamp)
            raise RuntimeError('rollback')
        with self.assertRaises(RuntimeError):
            with self.connect() as db:
                self.handle(db, update, fail)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_telegram_updates').fetchone()[0], 0)
            self.assertEqual(self.handle(db, update, lambda *args: args[-1]), {'status': 'linked'})
        self.assertEqual(seen, [{'status': 'linked'}])
        self.assertEqual(self.rows(), [])

    def test_polling_requests_callbacks_and_commits_handler_with_offset(self):
        self.transport.updates = [self.update(update_id=500, callback=True)]
        def handler(db, update, timestamp, config, binding_result):
            self.reply(db)
        result = notifications.poll_telegram_updates(self.connect, self.config, 10, self.transport, handler)
        self.assertEqual(result, {'status': 'ready', 'processed': 1})
        self.assertEqual(self.transport.requests[0][1]['allowed_updates'], ['message', 'callback_query'])
        self.assertEqual(len(self.rows()), 1)
        notifications.poll_telegram_updates(self.connect, self.config, 11, self.transport, handler)
        self.assertEqual(self.transport.requests[-1][1]['offset'], 501)
        self.assertEqual(len(self.rows()), 1)

    def test_failed_handler_does_not_advance_poll_offset(self):
        self.transport.updates = [self.update(update_id=500, callback=True)]
        def handler(db, *args):
            self.reply(db)
            raise RuntimeError('rollback')
        with self.assertRaises(RuntimeError):
            notifications.poll_telegram_updates(self.connect, self.config, 10, self.transport, handler)
        with self.connect() as db:
            self.assertIsNone(db.execute("SELECT value FROM cn_worker_state WHERE name='telegram_offset'").fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_telegram_updates').fetchone()[0], 0)
        self.assertEqual(self.rows(), [])

    def test_lifecycle_renderer_runs_inside_claim_transaction_only_for_telegram(self):
        with self.connect() as db:
            notifications.enqueue(db, 'candidate-a', 'application:one', 'application.proposed', settings=self.config, now=10)
        seen = []
        def renderer(db, row, config, timestamp):
            seen.append((row['event_type'], timestamp, db.in_transaction))
            db.execute("INSERT INTO cn_worker_state VALUES ('action-token', 'opaque')")
            return {'text': 'Предложение вакансии', 'reply_markup': self.keyboard}
        self.assertEqual(self.process(renderer=renderer)['delivered'], 2)
        self.assertEqual(seen, [('application.proposed', 20, True)])
        self.assertEqual(self.transport.sent[1], ('message', '1001', 'Предложение вакансии', self.keyboard))
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT value FROM cn_worker_state WHERE name='action-token'").fetchone()[0], 'opaque')

    def test_renderer_none_keeps_generic_message(self):
        with self.connect() as db:
            notifications.enqueue(db, 'candidate-a', 'application:one', 'application.proposed', 'telegram', self.config, 10)
        self.assertEqual(self.process(renderer=lambda *args: None)['delivered'], 1)
        self.assertIn(self.config.public_url, self.transport.sent[0][2])
        self.assertIsNone(self.transport.sent[0][3])

    def test_real_transport_emits_expected_methods_without_provider_details(self):
        adapter = notifications.Transport(self.config)
        with patch.object(adapter, 'telegram_request', return_value={'message_id': 123}) as request:
            self.assertEqual(adapter.send_telegram('1001', 'Сохранено.', self.keyboard), '123')
        self.assertEqual(request.call_args.args, ('sendMessage', {'chat_id': '1001', 'text': 'Сохранено.',
                         'reply_markup': self.keyboard, 'link_preview_options': {'is_disabled': True}}))
        with patch.object(adapter, 'telegram_request', return_value=True) as request:
            self.assertEqual(adapter.answer_callback_query('query-1', 'Готово', True), 'telegram_callback_accepted')
        self.assertEqual(request.call_args.args, ('answerCallbackQuery',
                         {'callback_query_id': 'query-1', 'text': 'Готово', 'show_alert': True}))
        with patch.object(adapter, 'telegram_request', return_value={'message_id': 'secret-provider-body'}):
            with self.assertRaises(notifications.DeliveryFailure) as error:
                adapter.send_telegram('1001', 'Generic')
        self.assertEqual(error.exception.code, 'telegram_response_invalid')
        with patch.object(adapter, 'telegram_request', return_value={'unexpected': True}):
            with self.assertRaises(notifications.DeliveryFailure):
                adapter.answer_callback_query('query-1')

    def test_worker_drains_polled_replies_immediately_and_aggregates_delivery_counts(self):
        for processed in (0, 1):
            with self.subTest(processed=processed):
                stop = threading.Event()
                steps = []
                handler, renderer = object(), object()
                drains = []

                def drain(connect, **kwargs):
                    steps.append('drain')
                    drains.append(kwargs)
                    if len(drains) == 2:
                        stop.set()
                    return {'delivered': len(drains), 'pending': 0, 'error': 0,
                            'disabled': 0, 'unverified': 0, 'unbound': 0}

                def poll(connect, **kwargs):
                    steps.append('poll')
                    if not processed:
                        stop.set()
                    return {'status': 'ready', 'processed': processed}

                with patch('candidate_notifications.settings', return_value=self.config), \
                     patch('candidate_notifications.process_pending', side_effect=drain), \
                     patch('candidate_notifications.poll_telegram_updates', side_effect=poll) as polling, \
                     patch('builtins.print') as output:
                    worker = notifications.start_worker(self.connect, interval=60, stop_event=stop,
                                                         update_handler=handler, telegram_renderer=renderer)
                    try:
                        worker.join(2)
                        self.assertFalse(worker.is_alive(), 'Worker waited for interval before draining replies')
                    finally:
                        stop.set()
                        worker.join(2)
                self.assertEqual(steps, ['drain', 'poll', 'drain'] if processed else ['drain', 'poll'])
                self.assertTrue(all(item == {'settings': self.config, 'limit': 5, 'telegram_renderer': renderer}
                                    for item in drains))
                self.assertEqual(polling.call_args.kwargs, {'settings': self.config, 'update_handler': handler})
                summary = json.loads(output.call_args.args[0])
                self.assertEqual(summary['outbox']['delivered'], 3 if processed else 1)
                self.assertEqual(summary['telegram']['processed'], processed)

    def test_socks_proxy_keeps_token_and_escaped_payload_on_stdin_only(self):
        config = replace(self.config, telegram_proxy_url='socks5h://127.0.0.1:10808')
        adapter = notifications.Transport(config)
        payload = {'chat_id': '1001', 'text': 'Кавычки "\\"\nновая строка\r\nurl = "https://untrusted.invalid"',
                   'reply_markup': self.keyboard}
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            kwargs['stdout'].write(b'{"ok":true,"result":{"message_id":123}}\n200')
            return notifications.subprocess.CompletedProcess(command, 0)

        with patch('candidate_notifications.subprocess.run', side_effect=run), \
             patch.dict('os.environ', {'HR_TELEGRAM_BOT_TOKEN': config.telegram_token,
                                      'ALL_PROXY': 'http://untrusted.invalid', 'SSLKEYLOGFILE': '/tmp/untrusted'}):
            self.assertEqual(adapter.telegram_request('sendMessage', payload), {'message_id': 123})
        command, kwargs = calls[0]
        self.assertEqual(command[:4], ['/usr/bin/curl', '--disable', '--config', '-'])
        self.assertNotIn(config.telegram_token, repr(command) + repr(kwargs['env']))
        self.assertNotIn(payload['text'], repr(command) + repr(kwargs['env']))
        self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        self.assertEqual(kwargs['stderr'], notifications.subprocess.DEVNULL)
        self.assertEqual(kwargs['timeout'], 20)
        self.assertEqual(command[command.index('--max-time') + 1], '15')
        self.assertEqual(command[command.index('--connect-timeout') + 1], '5')
        self.assertNotIn('--location', command)
        lines = kwargs['input'].decode().splitlines()
        self.assertEqual(len(lines), 5, 'Payload injected an additional curl config line')
        parsed = {key.strip(): json.loads(value.strip()) for key, value in (line.split('=', 1) for line in lines)}
        self.assertEqual(json.loads(parsed['data']), payload)
        self.assertEqual(urllib.parse.urlsplit(parsed['url']).hostname, 'api.telegram.org')
        self.assertEqual(parsed['url'], 'https://api.telegram.org/bot' + config.telegram_token + '/sendMessage')
        self.assertEqual(parsed['proxy'], config.telegram_proxy_url)
        self.assertNotIn(config.telegram_proxy_url, repr(config))
        self.assertEqual(notifications.settings({'HR_TELEGRAM_PROXY_URL': config.telegram_proxy_url}).telegram_proxy_url,
                         config.telegram_proxy_url)

    def test_proxy_rejects_non_loopback_or_credential_urls_without_subprocess(self):
        invalid = ['http://127.0.0.1:10808', 'socks5://127.0.0.1:10808', 'socks5h://proxy.example:10808',
                   'socks5h://localhost:10808', 'socks5h://user:secret@127.0.0.1:10808',
                   'socks5h://127.0.0.1:10808/', 'socks5h://127.0.0.1:10808?q=1',
                   'socks5h://127.0.0.1:10808#fragment', 'socks5h://127.0.0.1:0',
                   'socks5h://127.0.0.1:65536', 'socks5h://127.0.0.1:10808\n']
        for proxy in invalid:
            with self.subTest(proxy=proxy), patch('candidate_notifications.subprocess.run') as run:
                adapter = notifications.Transport(replace(self.config, telegram_proxy_url=proxy))
                with self.assertRaises(notifications.DeliveryFailure) as error:
                    adapter.telegram_request('getMe', {})
                self.assertEqual(error.exception.code, 'telegram_proxy_invalid')
                self.assertFalse(error.exception.retryable)
                run.assert_not_called()
        adapter = notifications.Transport(replace(self.config, telegram_proxy_url='socks5h://127.0.0.1:10808'))
        with patch('candidate_notifications.subprocess.run') as run:
            with self.assertRaises(notifications.DeliveryFailure):
                adapter.telegram_request('../untrusted?url=https://untrusted.invalid', {})
            run.assert_not_called()

    def test_proxy_http_errors_retry_only_explicit_rejections_and_never_expose_body(self):
        adapter = notifications.Transport(replace(self.config, telegram_proxy_url='socks5h://127.0.0.1:10808'))
        for status, code, retry in ((429, 22, True), (503, 22, True), (403, 22, False), (302, 0, False)):
            def run(command, **kwargs):
                kwargs['stdout'].write(b'secret-provider-body\n' + str(status).encode())
                return notifications.subprocess.CompletedProcess(command, code)
            with self.subTest(status=status), patch('candidate_notifications.subprocess.run', side_effect=run):
                with self.assertRaises(notifications.DeliveryFailure) as error:
                    adapter.telegram_request('getUpdates', {})
                self.assertEqual(error.exception.code, 'telegram_http_' + str(status))
                self.assertEqual(error.exception.retryable, retry)
                self.assertEqual(error.exception.retry_after, 60 if status == 429 else 0)
                self.assertNotIn('secret-provider-body', str(error.exception))

    def test_proxy_network_timeout_or_subprocess_failure_is_uncertain_without_auto_retry(self):
        adapter = notifications.Transport(replace(self.config, telegram_proxy_url='socks5h://127.0.0.1:10808'))
        failures = [OSError('token=private'), notifications.subprocess.TimeoutExpired('secret-command', 20)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                 patch('candidate_notifications.subprocess.run', side_effect=failure):
                with self.assertRaises(notifications.DeliveryFailure) as error:
                    adapter.telegram_request('sendMessage', {'text': 'test'})
                self.assertEqual(str(error.exception), 'telegram_delivery_uncertain')
                self.assertFalse(error.exception.retryable)
        with patch('candidate_notifications.subprocess.run',
                   return_value=notifications.subprocess.CompletedProcess([], 28)):
            with self.assertRaises(notifications.DeliveryFailure) as error:
                adapter.telegram_request('sendMessage', {'text': 'test'})
            self.assertEqual(error.exception.code, 'telegram_delivery_uncertain')
            self.assertFalse(error.exception.retryable)

    def test_proxy_response_size_and_status_trailer_are_validated_before_json(self):
        adapter = notifications.Transport(replace(self.config, telegram_proxy_url='socks5h://127.0.0.1:10808'))
        for output in (b'x' * (1024 * 1024 + 1) + b'\n200', b'{}', b'{}\nsecret', b'not-json\n200'):
            def run(command, **kwargs):
                kwargs['stdout'].write(output)
                return notifications.subprocess.CompletedProcess(command, 0)
            with self.subTest(size=len(output)), patch('candidate_notifications.subprocess.run', side_effect=run):
                with self.assertRaises(notifications.DeliveryFailure) as error:
                    adapter.telegram_request('getUpdates', {})
                self.assertEqual(error.exception.code, 'telegram_response_invalid')
                self.assertFalse(error.exception.retryable)


if __name__ == '__main__':
    unittest.main()
