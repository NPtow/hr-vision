"""Candidate email / Telegram delivery with a durable, channel-specific outbox.

No network calls occur in init, enqueue or HTTP hooks. app.py starts a daemon only
in its __main__ server entrypoint and only when HR_NOTIFICATIONS_ENABLED=1.
Alternatively run one dedicated worker (do not run both intentionally):
    python candidate_notifications.py --db /var/lib/hr-vision-api/journey.sqlite3 --loop
The standalone CLI handles account binding and delivery only. Interactive bot
updates and lifecycle keyboards use the handler/renderer injected by app.py.

Environment (all off until HR_NOTIFICATIONS_ENABLED=1):
  HR_CANDIDATE_PUBLIC_URL: candidate portal URL, otherwise HR_ORIGIN + candidate story.
  HR_SMTP_HOST, HR_SMTP_PORT (587), HR_SMTP_FROM, HR_SMTP_USER, HR_SMTP_PASSWORD,
  HR_SMTP_SECURITY=starttls|ssl. TLS and certificate validation cannot be disabled.
  HR_TELEGRAM_BOT_TOKEN, HR_TELEGRAM_BOT_USERNAME.
  HR_TELEGRAM_PROXY_URL: optional loopback socks5h URL, for example
  socks5h://127.0.0.1:10808. Uses /usr/bin/curl only for Telegram requests.
  HR_TELEGRAM_WEBHOOK_SECRET: 32+ URL-safe characters. The HTTP adapter must pass the
  X-Telegram-Bot-Api-Secret-Token header to handle_telegram_update, never form input.
  Or HR_TELEGRAM_POLLING=1 for this worker to read updates directly from Telegram.
  Do not enable polling when a webhook is registered. Registering a webhook is an
  explicit operator action; this module never changes provider configuration.

Minimal operator configuration, values supplied securely (no literal credentials):
  HR_NOTIFICATIONS_ENABLED=1
  HR_SMTP_HOST=<provider host> HR_SMTP_PORT=587 HR_SMTP_SECURITY=starttls
  HR_SMTP_FROM=<sender email> HR_SMTP_USER=<SMTP login> HR_SMTP_PASSWORD=<secret>
  HR_TELEGRAM_BOT_TOKEN=<BotFather token> HR_TELEGRAM_BOT_USERNAME=<bot username>
  HR_TELEGRAM_POLLING=1
Email and Telegram are independent; omit the unused channel. Restart API after
updating environment. With webhook instead of polling, set WEBHOOK_SECRET above,
register the actual HTTPS API route with that same secret_token, and omit POLLING.
For a controlled check, first bind and verify a test account, then trigger a NEW
event. Enabling settings never drains old disabled events. Delivered means provider
acceptance; verify actual receipt separately. No automatic reminder schedule exists.

Disabled/unbound/unverified records never wake automatically after configuration or
recipient changes. retry() is an explicit operator action. A provider acceptance is
recorded as delivered; it does not prove that a human read the email/message. Failed
or abandoned in-flight deliveries with an unknown outcome require manual retry,
because SMTP/Telegram cannot provide exactly-once delivery after a process crash.

Default lifecycle delivery sends generic notices and an authenticated portal link.
Injected bot handlers/renderers may send candidate details only to the candidate's
currently verified Telegram chat and, when available, an active canonical account.
Verification emails and explicitly requested booking confirmations are the only
exceptions allowed to an unverified address. Booking confirmations carry only the
consented interview time and its scoped invitation; they never verify an account.
Verification is a generic ownership check,
with no person/profile/vacancy data. Tokens are generated at dispatch, SHA256-only
at rest, single-use, and redeemable only by the same authenticated candidate.
"""
import argparse
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
from email.message import EmailMessage
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from domain import Problem, require


EVENTS = {
    'candidate.registered': ('Кабинет HR Vision', 'Ваш кабинет HR Vision доступен. Продолжить можно по ссылке.'),
    'interview.booked': ('Интервью с рекрутером', 'Вы записались на интервью с рекрутером. Время и вход на встречу — в кабинете.'),
    'interview.completed': ('После интервью', 'Интервью завершено. Следующий статус и предложения вакансий появятся в кабинете.'),
    'application.proposed': ('Предложение вакансии', 'В кабинете появилось предложение вакансии. Проверьте его и выбранный вами режим согласования. Время встречи подтверждается отдельно.'),
    'meeting.proposed': ('Предложено время встречи', 'Работодатель предложил время встречи. Откройте кабинет: если вы ещё не подтвердили время, встреча ожидает вашего решения.'),
    'meeting.confirmed': ('Встреча подтверждена', 'Вы подтвердили время встречи. Время и вход на встречу — в кабинете.'),
    'meeting.rescheduled': ('Предложено новое время', 'Время встречи изменилось. Проверьте и подтвердите новое время в кабинете.'),
    'meeting.cancelled': ('Встреча отменена', 'Встреча отменена. Актуальные предложения и встречи доступны в кабинете.'),
    'meeting.declined': ('Время встречи отклонено', 'Предложенное время отклонено. Актуальные встречи доступны в кабинете.'),
    'offer.sent': ('Предложение о работе', 'Работодатель прислал предложение о работе. Условия и ответ — в кабинете.'),
    'offer.updated': ('Предложение обновлено', 'Условия предложения о работе изменились. Проверьте актуальную версию в кабинете.'),
    'offer.accepted': ('Предложение принято', 'Ответ на предложение о работе сохранён. Подробности — в кабинете.'),
    'offer.declined': ('Ответ сохранён', 'Ответ на предложение о работе сохранён. Подробности — в кабинете.'),
    'offer.withdrawn': ('Предложение отозвано', 'Работодатель отозвал предложение о работе. Актуальные предложения — в кабинете.'),
    'chat.message': ('Новое сообщение', 'В кабинете HR Vision появилось новое сообщение.'),
    'employer.declined': ('Решение по вакансии', 'Работодатель завершил рассмотрение по вакансии. Другие предложения доступны в кабинете.'),
}
MAX_ATTEMPTS = 3
LEASE_SECONDS = 180
VERIFY_SECONDS = 1800
LINK_SECONDS = 600
RESEND_COOLDOWN = 60
TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{40,64}$')
BOT_EVENTS = frozenset(('telegram.reply', 'telegram.callback'))


def _number(value, fallback, low, high):
    try:
        return min(high, max(low, int(value)))
    except (ValueError, TypeError):
        return fallback


def _email(value):
    require(isinstance(value, str) and 3 <= len(value) <= 254
            and re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', value)
            and value.isascii(), 'Проверьте email.', 400)
    return value.strip().lower()


def _url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        return bool(parsed.scheme == 'https' and parsed.hostname and not parsed.username
                    and not parsed.password and not parsed.fragment
                    and not any(ord(c) < 32 for c in value))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    public_url: str = 'https://hr-vision.158-160-179-53.sslip.io/iframe.html?id=hr-vision-product--candidate&viewMode=story'
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_from: str = ''
    smtp_user: str = field(default='', repr=False)
    smtp_password: str = field(default='', repr=False)
    smtp_security: str = 'starttls'
    telegram_token: str = field(default='', repr=False)
    telegram_proxy_url: str = field(default='', repr=False)
    telegram_username: str = ''
    telegram_webhook_secret: str = field(default='', repr=False)
    telegram_polling: bool = False

    @property
    def email_configured(self):
        try:
            _email(self.smtp_from)
        except Problem:
            return False
        return bool(self.enabled and _url(self.public_url) and self.smtp_host
                    and self.smtp_security in ('starttls', 'ssl')
                    and (not self.smtp_user or self.smtp_password))

    @property
    def telegram_configured(self):
        return bool(self.enabled and _url(self.public_url) and self.telegram_token
                    and re.fullmatch(r'[A-Za-z0-9_]{5,32}', self.telegram_username))

    @property
    def telegram_binding_available(self):
        return bool(self.telegram_configured and (self.telegram_polling or
                    re.fullmatch(r'[A-Za-z0-9_-]{32,256}', self.telegram_webhook_secret)))


def settings(env=None):
    env = os.environ if env is None else env
    default_url = env.get('HR_ORIGIN', 'https://hr-vision.158-160-179-53.sslip.io').rstrip('/') + \
        '/iframe.html?id=hr-vision-product--candidate&viewMode=story'
    security = env.get('HR_SMTP_SECURITY', 'starttls')
    return Settings(
        enabled=env.get('HR_NOTIFICATIONS_ENABLED') == '1',
        public_url=env.get('HR_CANDIDATE_PUBLIC_URL', default_url),
        smtp_host=env.get('HR_SMTP_HOST', ''),
        smtp_port=_number(env.get('HR_SMTP_PORT'), 465 if security == 'ssl' else 587, 1, 65535),
        smtp_from=env.get('HR_SMTP_FROM', ''), smtp_user=env.get('HR_SMTP_USER', ''),
        smtp_password=env.get('HR_SMTP_PASSWORD', ''), smtp_security=security,
        telegram_token=env.get('HR_TELEGRAM_BOT_TOKEN', ''),
        telegram_proxy_url=env.get('HR_TELEGRAM_PROXY_URL', ''),
        telegram_username=env.get('HR_TELEGRAM_BOT_USERNAME', '').lstrip('@'),
        telegram_webhook_secret=env.get('HR_TELEGRAM_WEBHOOK_SECRET', ''),
        telegram_polling=env.get('HR_TELEGRAM_POLLING') == '1',
    )


def _config(value):
    return value if value is not None else settings()


def _now(value):
    return int(time.time()) if value is None else int(value)


def _dict(cursor):
    row = cursor.fetchone()
    return dict(zip([column[0] for column in cursor.description], row)) if row else None


def init(db):
    # execute() preserves caller transactions; executescript() would commit them.
    statements = [
        '''CREATE TABLE IF NOT EXISTS cn_recipients (
            candidate_id TEXT PRIMARY KEY, email TEXT NOT NULL,
            email_verified INTEGER NOT NULL DEFAULT 0, telegram_chat_id TEXT UNIQUE,
            telegram_verified_at INTEGER, updated_at INTEGER NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS cn_tokens (
            digest TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, purpose TEXT NOT NULL,
            recipient TEXT NOT NULL, created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL, used_at INTEGER)''',
        '''CREATE TABLE IF NOT EXISTS cn_outbox (
            id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, event_key TEXT NOT NULL,
            event_type TEXT NOT NULL, channel TEXT NOT NULL, recipient TEXT,
            created_at INTEGER NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL, last_error TEXT, delivered_at INTEGER,
            lease_id TEXT, lease_until INTEGER, provider_ref TEXT,
            UNIQUE(candidate_id,event_key,channel))''',
        'CREATE INDEX IF NOT EXISTS cn_outbox_ready ON cn_outbox(status,next_attempt_at)',
        'CREATE INDEX IF NOT EXISTS cn_outbox_owner ON cn_outbox(candidate_id,created_at)',
        '''CREATE TABLE IF NOT EXISTS cn_telegram_updates (
            update_id INTEGER PRIMARY KEY, received_at INTEGER NOT NULL)''',
        'CREATE TABLE IF NOT EXISTS cn_worker_state (name TEXT PRIMARY KEY, value TEXT NOT NULL)',
        '''CREATE TABLE IF NOT EXISTS cn_booking_telegram (
            invitation_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
            chat_id TEXT NOT NULL, linked_at INTEGER NOT NULL)''',
    ]
    for statement in statements:
        db.execute(statement)
    if 'payload_json' not in {row[1] for row in db.execute('PRAGMA table_info(cn_outbox)')}:
        db.execute('ALTER TABLE cn_outbox ADD COLUMN payload_json TEXT')


def _recipient(db, candidate_id):
    row = _dict(db.execute('SELECT * FROM cn_recipients WHERE candidate_id=?', (candidate_id,)))
    require(row is not None, 'Кандидат не найден.', 404)
    return row


def set_email(db, candidate_id, email, verified=False, now=None):
    """Internal hook. verified=True is only for an independently verified identity."""
    address, timestamp = _email(email), _now(now)
    require(isinstance(candidate_id, str) and 1 <= len(candidate_id) <= 160, 'Кандидат не найден.', 400)
    old = _dict(db.execute('SELECT * FROM cn_recipients WHERE candidate_id=?', (candidate_id,)))
    if old:
        own = bool(verified or (old['email'] == address and old['email_verified']))
        db.execute('UPDATE cn_recipients SET email=?,email_verified=?,updated_at=? WHERE candidate_id=?',
                   (address, int(own), timestamp, candidate_id))
        if old['email'] != address:
            db.execute("UPDATE cn_tokens SET used_at=? WHERE candidate_id=? AND purpose='email' AND used_at IS NULL",
                       (timestamp, candidate_id))
    else:
        db.execute('INSERT INTO cn_recipients(candidate_id,email,email_verified,updated_at) VALUES (?,?,?,?)',
                   (candidate_id, address, int(bool(verified)), timestamp))


def configuration_public(settings=None):
    config = _config(settings)
    return {'enabled': config.enabled, 'emailConfigured': config.email_configured,
            'telegramConfigured': config.telegram_configured,
            'telegramBindingAvailable': config.telegram_binding_available}


def _telegram_identity_active(db, recipient):
    if not recipient or not recipient['telegram_chat_id'] or recipient['telegram_verified_at'] is None:
        return False
    # The standalone notification store has no canonical account table. When
    # embedded in the portal, an intake-only or deleted identity cannot receive
    # candidate data even if an old notification recipient still exists.
    if db is not None and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cp_candidates'").fetchone():
        active = db.execute('SELECT email FROM cp_candidates WHERE id=?', (recipient['candidate_id'],)).fetchone()
        return bool(active and active[0])
    return True


def _readiness(recipient, channel, config, verification=False, db=None):
    if channel == 'email':
        if not config.email_configured:
            return 'disabled'
        return 'pending' if verification or recipient['email_verified'] else 'unverified'
    if not config.telegram_configured:
        return 'disabled'
    return 'pending' if _telegram_identity_active(db, recipient) else 'unbound'


def _safe(row):
    return {key: row[key] for key in ('id', 'event_type', 'channel', 'status', 'attempts',
                                     'created_at', 'delivered_at', 'last_error')}


def public_status(db, candidate_id, settings=None):
    config, recipient = _config(settings), _recipient(db, candidate_id)
    address = recipient['email'].split('@')
    result = {
        'email': {'configured': config.email_configured, 'verified': bool(recipient['email_verified']),
                  'address': address[0][:1] + '***@' + address[1],
                  'status': _readiness(recipient, 'email', config, db=db), 'lastDelivery': None},
        'telegram': {'configured': config.telegram_configured, 'linked': _telegram_identity_active(db, recipient),
                     'canLink': config.telegram_binding_available,
                     'status': _readiness(recipient, 'telegram', config, db=db), 'lastDelivery': None},
    }
    for channel in ('email', 'telegram'):
        last = _dict(db.execute('SELECT * FROM cn_outbox WHERE candidate_id=? AND channel=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
                                (candidate_id, channel)))
        if last:
            result[channel]['lastDelivery'] = _safe(last)
        if result[channel]['status'] == 'pending':
            result[channel]['status'] = 'ready'
    return result


def _enqueue_one(db, candidate_id, event_key, event_type, channel, config, timestamp, verification=False):
    recipient = _recipient(db, candidate_id)
    existing = _dict(db.execute('SELECT * FROM cn_outbox WHERE candidate_id=? AND event_key=? AND channel=?',
                                (candidate_id, event_key, channel)))
    if existing:
        require(existing['event_type'] == event_type, 'Событие уже существует с другим типом.', 409)
        return _safe(existing)
    rid = secrets.token_hex(16)
    target = recipient['email'] if channel == 'email' else recipient['telegram_chat_id']
    state = _readiness(recipient, channel, config, verification, db)
    db.execute('''INSERT INTO cn_outbox
        (id,candidate_id,event_key,event_type,channel,recipient,created_at,status,next_attempt_at)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (rid, candidate_id, event_key, event_type, channel, target, timestamp, state, timestamp))
    return _safe(_dict(db.execute('SELECT * FROM cn_outbox WHERE id=?', (rid,))))


def enqueue(db, candidate_id, event_key, event_type, channel='all', settings=None, now=None):
    require(event_type in EVENTS, 'Неизвестное событие уведомления.', 400)
    require(isinstance(event_key, str) and 1 <= len(event_key) <= 240, 'Не указан ключ события.', 400)
    require(channel in ('all', 'email', 'telegram'), 'Неизвестный канал.', 400)
    config, timestamp = _config(settings), _now(now)
    return [_enqueue_one(db, candidate_id, event_key, event_type, item, config, timestamp)
            for item in (('email', 'telegram') if channel == 'all' else (channel,))]


def _private_chat_id(value):
    # Telegram user identifiers have at most 52 significant bits. No groups,
    # usernames, booleans or caller-controlled URLs are delivery targets here.
    if type(value) is int:
        value = str(value)
    require(isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,15}', value)
            and int(value) < 2 ** 52, 'Некорректный Telegram-чат.', 400)
    return value


def _callback_data_valid(value):
    if not isinstance(value, str):
        return False
    try:
        return 1 <= len(value.encode('utf-8')) <= 64
    except UnicodeEncodeError:
        return False


def _reply_markup(value):
    if value is None:
        return None
    require(isinstance(value, dict) and set(value) == {'inline_keyboard'},
            'Некорректная клавиатура.', 400)
    rows = value['inline_keyboard']
    require(isinstance(rows, list) and 1 <= len(rows) <= 20, 'Некорректная клавиатура.', 400)
    count = 0
    for row in rows:
        require(isinstance(row, list) and 1 <= len(row) <= 8, 'Некорректная клавиатура.', 400)
        for button in row:
            require(isinstance(button, dict) and set(button) in ({'text', 'callback_data'}, {'text', 'url'})
                    and isinstance(button.get('text'), str) and 1 <= len(button['text']) <= 128,
                    'Некорректная кнопка.', 400)
            require(_callback_data_valid(button['callback_data']) if 'callback_data' in button
                    else _url(button['url']), 'Некорректная кнопка.', 400)
            count += 1
    require(count <= 100, 'Слишком много кнопок.', 400)
    return value


def _telegram_message(text, reply_markup=None):
    require(isinstance(text, str) and 1 <= len(text) <= 4096, 'Некорректный текст сообщения.', 400)
    result = {'text': text}
    markup = _reply_markup(reply_markup)
    if markup is not None:
        result['reply_markup'] = markup
    return result


def _telegram_callback(callback_query_id, text='', show_alert=False):
    require(isinstance(callback_query_id, str) and 1 <= len(callback_query_id) <= 256
            and callback_query_id.isascii() and all(32 < ord(c) < 127 for c in callback_query_id),
            'Некорректный ответ на кнопку.', 400)
    require(isinstance(text, str) and len(text) <= 200 and type(show_alert) is bool,
            'Некорректный ответ на кнопку.', 400)
    return {'callback_query_id': callback_query_id, 'text': text, 'show_alert': show_alert}


def _bot_readiness(db, candidate_id, chat_id, config):
    if candidate_id:
        recipient = _dict(db.execute('SELECT * FROM cn_recipients WHERE candidate_id=?', (candidate_id,)))
        if not _telegram_identity_active(db, recipient) or recipient['telegram_chat_id'] != chat_id:
            return 'error'
    return 'pending' if config.telegram_configured else 'disabled'


def _enqueue_telegram(db, chat_id, event_key, event_type, payload, candidate_id, config, timestamp):
    target = _private_chat_id(chat_id)
    require(candidate_id is None or isinstance(candidate_id, str) and 1 <= len(candidate_id) <= 160,
            'Кандидат не найден.', 400)
    require(isinstance(event_key, str) and 1 <= len(event_key) <= 240, 'Не указан ключ события.', 400)
    owner = candidate_id or ''
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    existing = _dict(db.execute("SELECT * FROM cn_outbox WHERE candidate_id=? AND event_key=? AND channel='telegram'",
                                (owner, event_key)))
    if existing:
        require(existing['event_type'] == event_type and existing['recipient'] == target
                and existing['payload_json'] == encoded, 'Событие уже существует с другим содержимым.', 409)
        return _safe(existing)
    state = _bot_readiness(db, owner, target, config)
    require(state != 'error', 'Telegram-чат не привязан к кандидату.', 403)
    rid = secrets.token_hex(16)
    db.execute('''INSERT INTO cn_outbox
        (id,candidate_id,event_key,event_type,channel,recipient,created_at,status,next_attempt_at,payload_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (rid, owner, event_key, event_type, 'telegram', target, timestamp, state, timestamp, encoded))
    return _safe(_dict(db.execute('SELECT * FROM cn_outbox WHERE id=?', (rid,))))


def enqueue_telegram_reply(db, chat_id, event_key, text, reply_markup=None, candidate_id=None, settings=None, now=None,
                           application_id=None):
    """Internal bot hook; callers must validate the originating private update.

    Without a candidate_id only generic onboarding/error prompts are permitted.
    Candidate-specific content must pass the linked owner for dispatch revalidation.
    No provider I/O occurs until the caller transaction commits and a worker claims it.
    """
    payload = _telegram_message(text, reply_markup)
    _tag_application(payload, application_id, candidate_id)
    return _enqueue_telegram(db, chat_id, event_key, 'telegram.reply', payload,
                             candidate_id, _config(settings), _now(now))


def enqueue_telegram_callback(db, chat_id, event_key, callback_query_id, text='', show_alert=False,
                              candidate_id=None, settings=None, now=None, application_id=None):
    """Queue a callback acknowledgement in the same durable delivery outbox."""
    payload = _telegram_callback(callback_query_id, text, show_alert)
    _tag_application(payload, application_id, candidate_id)
    return _enqueue_telegram(db, chat_id, event_key, 'telegram.callback', payload,
                             candidate_id, _config(settings), _now(now))


def _tag_application(payload, application_id, candidate_id):
    if application_id is not None:
        require(isinstance(application_id, str) and 1 <= len(application_id) <= 160 and candidate_id,
                'Не указана вакансия кандидата.', 400)
        # Private dispatch metadata is never forwarded to Telegram.
        payload['application_id'] = application_id


def _columns(db, table):
    # Only module-owned table names reach this helper.
    return {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}


def _candidate_searching(db, candidate_id):
    if 'searching' not in _columns(db, 'cp_candidates'):
        return True  # Standalone notification stores predate canonical search state.
    row = db.execute('SELECT searching FROM cp_candidates WHERE id=?', (candidate_id,)).fetchone()
    return bool(row and row[0])


def _application_delivery_error(db, candidate_id, application_id):
    if 'archived_at' not in _columns(db, 'cp_applications'):
        return None
    row = db.execute('SELECT archived_at FROM cp_applications WHERE id=? AND candidate_id=?',
                     (application_id, candidate_id)).fetchone()
    if not row or row[0] is not None:
        return 'application_archived'
    return None if _candidate_searching(db, candidate_id) else 'search_stopped'


def _delivery_block_reason(db, row):
    """Revalidate record-bound notifications while holding the dispatch claim.

    Account/verification messages and plain bot navigation remain usable while
    searching is paused. A blocked row stays terminal even after search resumes.
    """
    if not row['candidate_id'] or 'archived_at' not in _columns(db, 'cp_applications'):
        return None
    kind, candidate_id = row['event_type'], row['candidate_id']
    if kind in BOT_EVENTS:
        payload = json.loads(row['payload_json'])
        if payload.get('application_id') is not None:
            return _application_delivery_error(db, candidate_id, payload['application_id'])
        # Old durable replies predate the explicit record metadata. Resolve their
        # opaque buttons against the existing action registry before releasing.
        markup = payload.get('reply_markup') or {}
        for buttons in markup.get('inline_keyboard', []):
            for button in buttons:
                data = button.get('callback_data')
                match = re.fullmatch(r'cb:([A-Za-z0-9_-]{32})', data) if isinstance(data, str) else None
                if not match:
                    continue
                if not _columns(db, 'cb_actions'):
                    return 'application_archived'
                action = db.execute('SELECT candidate_id,application_id,action FROM cb_actions WHERE digest=?',
                                    (_hash(match[1]),)).fetchone()
                if not action or action[0] != candidate_id:
                    return 'application_archived'
                if action[1]:
                    reason = _application_delivery_error(db, candidate_id, action[1])
                    if reason:
                        return reason
                elif action[2] == 'vacancies.page' and not _candidate_searching(db, candidate_id):
                    return 'search_stopped'
        return None
    if kind == 'application.proposed':
        application_id = row['event_key'][len(kind) + 1:] if row['event_key'].startswith(kind + ':') else None
    elif kind.startswith('meeting.'):
        entity = row['event_key'][len(kind) + 1:] if row['event_key'].startswith(kind + ':') else None
        meeting = db.execute('SELECT application_id FROM cp_meetings WHERE id=?', (entity,)).fetchone() \
            if _columns(db, 'cp_meetings') else None
        application_id = meeting[0] if meeting else None
    elif kind.startswith('offer.') or kind in ('chat.message', 'employer.declined'):
        event = db.execute('SELECT application_id FROM cp_workflow_events WHERE event_key=? AND candidate_id=?',
                           (row['event_key'], candidate_id)).fetchone() if _columns(db, 'cp_workflow_events') else None
        application_id = event[0] if event else None
    else:
        return None
    return _application_delivery_error(db, candidate_id, application_id)


def request_email_verification(db, candidate_id, settings=None, now=None):
    config, timestamp, recipient = _config(settings), _now(now), _recipient(db, candidate_id)
    if recipient['email_verified']:
        return {'status': 'verified', 'queued': False, 'retryAfter': 0}
    recent = db.execute("""SELECT created_at FROM cn_outbox WHERE event_type='email.verification'
        AND (candidate_id=? OR recipient=?) AND created_at>? ORDER BY created_at DESC""",
        (candidate_id, recipient['email'], timestamp - 3600)).fetchall()
    retry_after = max(0, recent[0][0] + RESEND_COOLDOWN - timestamp) if recent else 0
    if len(recent) >= 5:
        retry_after = max(retry_after, recent[-1][0] + 3600 - timestamp)
    if retry_after:
        return {'status': 'rate_limited', 'queued': False, 'retryAfter': retry_after}
    queued = _enqueue_one(db, candidate_id, 'email.verification:' + secrets.token_hex(12),
                          'email.verification', 'email', config, timestamp, True)
    return {'status': queued['status'], 'queued': queued['status'] == 'pending',
            'retryAfter': RESEND_COOLDOWN, 'notificationId': queued['id']}


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _token(db, candidate_id, purpose, recipient, timestamp, ttl):
    raw = secrets.token_urlsafe(32)
    db.execute('UPDATE cn_tokens SET used_at=? WHERE candidate_id=? AND purpose=? AND used_at IS NULL',
               (timestamp, candidate_id, purpose))
    db.execute('INSERT INTO cn_tokens VALUES (?,?,?,?,?,?,NULL)',
               (_hash(raw), candidate_id, purpose, recipient, timestamp, timestamp + ttl))
    return raw


def verify_email(db, candidate_id, token, settings=None, now=None):
    timestamp, recipient = _now(now), _recipient(db, candidate_id)
    require(isinstance(token, str) and TOKEN_RE.fullmatch(token), 'Ссылка подтверждения недействительна.', 400)
    result = db.execute("""UPDATE cn_tokens SET used_at=? WHERE digest=? AND candidate_id=?
        AND purpose='email' AND recipient=? AND expires_at>? AND used_at IS NULL""",
        (timestamp, _hash(token), candidate_id, recipient['email'], timestamp))
    require(result.rowcount == 1, 'Ссылка истекла или относится к другому аккаунту. Запросите новую.', 400)
    db.execute('UPDATE cn_recipients SET email_verified=1,updated_at=? WHERE candidate_id=?', (timestamp, candidate_id))
    return public_status(db, candidate_id, settings)


def create_telegram_link(db, candidate_id, settings=None, now=None):
    config, timestamp = _config(settings), _now(now)
    _recipient(db, candidate_id)
    if not config.telegram_binding_available:
        return {'status': 'disabled', 'url': None, 'expiresAt': None}
    raw = _token(db, candidate_id, 'telegram', '', timestamp, LINK_SECONDS)
    return {'status': 'ready', 'url': 'https://t.me/' + config.telegram_username + '?start=' + raw,
            'expiresAt': timestamp + LINK_SECONDS}


def booking_delivery_status(db, candidate_id, invitation_id, settings=None):
    config = _config(settings)
    consent = db.execute('SELECT invitation_id FROM cp_booking_profile_consents WHERE candidate_id=?', (candidate_id,)).fetchone()
    invitation_id = consent[0] if consent else invitation_id
    row = db.execute('''SELECT status FROM cn_outbox WHERE candidate_id=? AND event_key=? AND channel='email' ''',
                     (candidate_id, 'booking:' + invitation_id)).fetchone()
    bound = db.execute('SELECT 1 FROM cn_booking_telegram WHERE invitation_id=? AND candidate_id=?',
                       (invitation_id, candidate_id)).fetchone()
    return {'email': row[0] if row else None, 'telegramAvailable': bool(config.telegram_binding_available
            and _booking_record(db, candidate_id, invitation_id, _now(None))),
            'telegramLinked': bool(bound)}


def _booking_record(db, candidate_id, invitation_id, timestamp):
    record = _dict(db.execute('''SELECT c.interview_id,c.contact_email,s.start,s.end FROM cp_booking_profile_consents c
      JOIN cp_booking_invitations b ON b.id=c.invitation_id AND b.candidate_id=c.candidate_id
      JOIN cp_interviews i ON i.id=c.interview_id AND i.candidate_id=c.candidate_id
      JOIN cp_recruiter_slots s ON s.id=i.slot_id
      JOIN cp_recruiter_slot_owners o ON o.slot_id=s.id
      JOIN rc_accounts a ON a.id=o.recruiter_id AND a.active=1
      WHERE c.candidate_id=? AND b.id=? AND b.revoked_at IS NULL AND b.expires>?
      AND s.candidate_id=i.candidate_id AND i.status IN ('booked','live')''', (candidate_id, invitation_id, timestamp)))
    return record if record and datetime.fromisoformat(record['end'].replace('Z', '+00:00')).timestamp() > timestamp else None


def _booking_readiness(db, row, config, timestamp):
    payload = json.loads(row['payload_json'])
    booking = _booking_record(db, row['candidate_id'], payload['invitationId'], timestamp)
    if not booking or booking['interview_id'] != payload['interviewId']:
        return 'error'
    if row['channel'] == 'email':
        recipient = _recipient(db, row['candidate_id'])
        if row['recipient'] != booking['contact_email'] or row['recipient'] != recipient['email']:
            return 'error'
        return 'pending' if config.email_configured else 'disabled'
    bound = db.execute('SELECT chat_id FROM cn_booking_telegram WHERE invitation_id=? AND candidate_id=?',
                       (payload['invitationId'], row['candidate_id'])).fetchone()
    if not bound or bound[0] != row['recipient']:
        return 'error'
    return 'pending' if config.telegram_configured else 'disabled'


def enqueue_booking_confirmation(db, candidate_id, invitation_id, interview_id, email, url, settings=None, now=None):
    """Explicit booking consent authorizes this one invitation, not general mail."""
    config, tick = _config(settings), _now(now)
    consent = db.execute('''SELECT 1 FROM cp_booking_profile_consents
      WHERE candidate_id=? AND invitation_id=? AND interview_id=? AND contact_email=?''',
      (candidate_id, invitation_id, interview_id, email)).fetchone()
    require(consent, 'Запись не подтверждена.', 409)
    record = _booking_record(db, candidate_id, invitation_id, tick)
    if not record:
        return  # Legacy profile consent remains valid after the meeting stops being deliverable.
    set_email(db, candidate_id, email, now=tick)
    payload = json.dumps({'invitationId': invitation_id, 'interviewId': interview_id, 'url': url})
    db.execute('''INSERT OR IGNORE INTO cn_outbox
      (id,candidate_id,event_key,event_type,channel,recipient,created_at,status,next_attempt_at,payload_json)
      VALUES (?,?,?,'booking.confirmed','email',?,?,?,?,?)''',
      (secrets.token_hex(16), candidate_id, 'booking:' + invitation_id, email, tick,
       'pending' if config.email_configured else 'disabled', tick, payload))


def create_booking_telegram_link(db, candidate_id, invitation_id, url, settings=None, now=None):
    config, tick = _config(settings), _now(now)
    booking = _booking_record(db, candidate_id, invitation_id, tick)
    require(booking, 'Сначала подтвердите запись.', 409)
    if not config.telegram_binding_available:
        return {'status': 'disabled', 'url': None}
    if db.execute('SELECT 1 FROM cn_booking_telegram WHERE invitation_id=? AND candidate_id=?',
                  (invitation_id, candidate_id)).fetchone():
        return {'status': 'linked', 'url': None}
    payload = json.dumps({'invitationId': invitation_id, 'interviewId': booking['interview_id'], 'url': url})
    raw = _token(db, candidate_id, 'booking_telegram', payload, tick, LINK_SECONDS)
    return {'status': 'ready', 'url': 'https://t.me/' + config.telegram_username + '?start=bk_' + raw}


def _consume_booking_telegram_link(db, token, chat_id, tick, config):
    row = _dict(db.execute("SELECT * FROM cn_tokens WHERE digest=? AND purpose='booking_telegram' AND used_at IS NULL AND expires_at>?",
                           (_hash(token), tick)))
    payload = json.loads(row['recipient']) if row else None
    booking = _booking_record(db, row['candidate_id'], payload['invitationId'], tick) if row else None
    if not booking or booking['interview_id'] != payload['interviewId']:
        return {'status': 'invalid_link'}
    cid, chat = row['candidate_id'], str(chat_id)
    # Only this consented interview becomes available; no portal session, account,
    # generic candidate notifications or interactive vacancy access is granted.
    db.execute('UPDATE cn_tokens SET used_at=? WHERE digest=? AND used_at IS NULL', (tick, row['digest']))
    db.execute('INSERT OR IGNORE INTO cn_booking_telegram VALUES (?,?,?,?)', (payload['invitationId'], cid, chat, tick))
    db.execute('''INSERT OR IGNORE INTO cn_outbox
      (id,candidate_id,event_key,event_type,channel,recipient,created_at,status,next_attempt_at,payload_json)
      VALUES (?,?,?,'booking.confirmed','telegram',?,?,?,?,?)''',
      (secrets.token_hex(16), cid, 'booking:' + payload['invitationId'], chat, tick,
       'pending' if config.telegram_configured else 'disabled', tick, row['recipient']))
    return {'status': 'linked'}


def _booking_message(db, row, tick):
    payload = json.loads(row['payload_json'])
    booking = _booking_record(db, row['candidate_id'], payload['invitationId'], tick)
    start = datetime.fromisoformat(booking['start'].replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Moscow'))
    end = datetime.fromisoformat(booking['end'].replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Moscow'))
    prefix = 'Telegram подключён.\n\n' if row['channel'] == 'telegram' else ''
    return ('Вы записаны на интервью · МашаДаша', prefix + 'Ваше интервью: ' + start.strftime('%d.%m.%Y, %H:%M')
            + '–' + end.strftime('%H:%M') + ' МСК.\n\nВремя и подключение к встрече:\n' + payload['url'])


def _consume_telegram_update(db, update, timestamp, settings=None, update_handler=None):
    require(isinstance(update, dict) and type(update.get('update_id')) is int
            and 0 <= update['update_id'] < 2 ** 63, 'Некорректное обновление.', 400)
    inserted = db.execute('INSERT OR IGNORE INTO cn_telegram_updates VALUES (?,?)', (update['update_id'], timestamp))
    if not inserted.rowcount:
        return {'status': 'duplicate'}
    callback = update.get('callback_query')
    if callback is not None:
        if not isinstance(callback, dict) or 'message' in update or not _callback_data_valid(callback.get('data')):
            return {'status': 'ignored'}
        try:
            _telegram_callback(callback.get('id'))
        except Problem:
            return {'status': 'ignored'}
        message, sender = callback.get('message'), callback.get('from')
    else:
        message = update.get('message')
        sender = message.get('from') if isinstance(message, dict) else None
    if not isinstance(message, dict):
        return {'status': 'ignored'}
    chat = message.get('chat')
    if not (isinstance(chat, dict) and isinstance(sender, dict) and chat.get('type') == 'private'
            and type(chat.get('id')) is int and 0 < chat['id'] < 2 ** 52 and type(sender.get('id')) is int
            and chat['id'] == sender.get('id') and not sender.get('is_bot')):
        return {'status': 'ignored'}
    content = message.get('text', '') if callback is None else ''
    booking_match = re.fullmatch(r'/start(?:@[A-Za-z0-9_]+)? bk_([A-Za-z0-9_-]{43})', content) if isinstance(content, str) else None
    if booking_match:
        result = _consume_booking_telegram_link(db, booking_match[1], chat['id'], timestamp, _config(settings))
        if result['status'] != 'linked':
            text = 'Этот Telegram уже подключён к другому профилю. Обратитесь к команде.' if result['status'] == 'chat_already_linked' else 'Ссылка истекла. Вернитесь к записи на интервью и нажмите «Подключить Telegram» ещё раз.'
            enqueue_telegram_reply(db, str(chat['id']), 'booking-link:' + str(update['update_id']), text, settings=settings, now=timestamp)
        return result
    if isinstance(content, str) and re.fullmatch(r'/start(?:@[A-Za-z0-9_]+)? rc_[A-Za-z0-9_-]{43}', content):
        # One bot/polling offset, separate identity namespaces and link tokens.
        # Recruiter capabilities must not reach candidate binding or bot commands.
        import recruiter_notifications
        return recruiter_notifications.consume_telegram_update(db, update, timestamp)
    match = re.fullmatch(r'/start(?:@[A-Za-z0-9_]+)? ([A-Za-z0-9_-]{40,64})', content) if isinstance(content, str) else None
    binding_result = _consume_telegram_link(db, match[1], chat['id'], timestamp) if match else None
    if update_handler is not None:
        result = update_handler(db, update, timestamp, _config(settings), binding_result)
        if result is not None:
            return result
    return binding_result or {'status': 'ignored'}


def _consume_telegram_link(db, token, chat_id, timestamp):
    row = _dict(db.execute("SELECT * FROM cn_tokens WHERE digest=? AND purpose='telegram' AND used_at IS NULL AND expires_at>?",
                           (_hash(token), timestamp)))
    if not row:
        return {'status': 'invalid_link'}
    chat_id = str(chat_id)
    taken = db.execute('SELECT candidate_id FROM cn_recipients WHERE telegram_chat_id=? AND candidate_id<>?',
                       (chat_id, row['candidate_id'])).fetchone()
    if taken:
        return {'status': 'chat_already_linked'}
    consumed = db.execute('UPDATE cn_tokens SET used_at=? WHERE digest=? AND used_at IS NULL', (timestamp, row['digest']))
    if not consumed.rowcount:
        return {'status': 'invalid_link'}
    db.execute('UPDATE cn_recipients SET telegram_chat_id=?,telegram_verified_at=?,updated_at=? WHERE candidate_id=?',
               (chat_id, timestamp, timestamp, row['candidate_id']))
    return {'status': 'linked'}


def handle_telegram_update(db, update, supplied_secret='', settings=None, now=None, update_handler=None):
    config = _config(settings)
    expected = config.telegram_webhook_secret
    require(config.telegram_configured and re.fullmatch(r'[A-Za-z0-9_-]{32,256}', expected)
            and isinstance(supplied_secret, str) and supplied_secret.isascii() and len(supplied_secret) <= 256
            and hmac.compare_digest(expected, supplied_secret), 'Нет доступа.', 403)
    return _consume_telegram_update(db, update, _now(now), config, update_handler)


def retry(db, notification_id, settings=None, now=None, candidate_id=None):
    """Explicit retry; HTTP callers must check operator or matching candidate access."""
    config, timestamp = _config(settings), _now(now)
    row = _dict(db.execute('SELECT * FROM cn_outbox WHERE id=?', (notification_id,)))
    require(row and (candidate_id is None or row['candidate_id'] == candidate_id), 'Уведомление не найдено.', 404)
    require(row['status'] != 'sending', 'Отправка уже выполняется.', 409)
    if row['status'] == 'delivered':
        return _safe(row)
    if row['status'] == 'error' and row['last_error'] in ('application_archived', 'search_stopped'):
        return _safe(row)
    reason = _delivery_block_reason(db, row)
    if reason:
        db.execute("UPDATE cn_outbox SET status='error',last_error=?,lease_id=NULL,lease_until=NULL WHERE id=?",
                   (reason, notification_id))
        return _safe(_dict(db.execute('SELECT * FROM cn_outbox WHERE id=?', (notification_id,))))
    if row['event_type'] == 'booking.confirmed':
        target = row['recipient']
        state = _booking_readiness(db, row, config, timestamp)
    elif row['event_type'] in BOT_EVENTS:
        # Replaying a private reply must never retarget its stored content.
        target = row['recipient']
        state = _bot_readiness(db, row['candidate_id'], target, config)
        require(state != 'error', 'Telegram-чат больше не привязан к кандидату.', 409)
    else:
        recipient = _recipient(db, row['candidate_id'])
        state = _readiness(recipient, row['channel'], config, row['event_type'] == 'email.verification', db)
        target = recipient['email'] if row['channel'] == 'email' else recipient['telegram_chat_id']
    db.execute('''UPDATE cn_outbox SET status=?,recipient=?,attempts=0,next_attempt_at=?,last_error=NULL,
               lease_id=NULL,lease_until=NULL WHERE id=?''', (state, target, timestamp, notification_id))
    return _safe(_dict(db.execute('SELECT * FROM cn_outbox WHERE id=?', (notification_id,))))


class DeliveryFailure(Exception):
    def __init__(self, code, retryable=False, retry_after=0):
        # Codes are module-owned constants. Never put provider bodies/URLs/passwords in DB or logs.
        self.code, self.retryable = code, retryable
        self.retry_after = min(3600, max(0, int(retry_after)))
        super().__init__(code)


class Transport:
    def __init__(self, config):
        self.config = config

    def send_email(self, recipient, subject, content, message_id):
        config = self.config
        message = EmailMessage()
        message['From'], message['To'], message['Subject'] = config.smtp_from, recipient, subject
        message['Message-ID'] = '<' + message_id + '@hr-vision.notifications>'
        message.set_content(content)
        context = ssl.create_default_context()
        try:
            if config.smtp_security == 'ssl':
                server = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=15, context=context)
            elif config.smtp_security == 'starttls':
                server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15)
            else:
                raise DeliveryFailure('smtp_tls_required')
            with server:
                server.ehlo()
                if config.smtp_security == 'starttls':
                    server.starttls(context=context)
                    server.ehlo()
                if config.smtp_user:
                    server.login(config.smtp_user, config.smtp_password)
                refused = server.send_message(message)
                if refused:
                    raise DeliveryFailure('smtp_recipient_refused')
            return 'smtp_accepted'
        except DeliveryFailure:
            raise
        except smtplib.SMTPAuthenticationError:
            raise DeliveryFailure('smtp_authentication') from None
        except smtplib.SMTPResponseException as error:
            raise DeliveryFailure('smtp_rejected', 400 <= error.smtp_code < 500) from None
        except smtplib.SMTPRecipientsRefused:
            raise DeliveryFailure('smtp_recipient_refused') from None
        except (OSError, smtplib.SMTPException):
            # Failure may occur after DATA was accepted; do not silently duplicate delivery.
            raise DeliveryFailure('smtp_delivery_uncertain') from None

    def telegram_request(self, method, payload):
        if not isinstance(method, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}', method):
            raise DeliveryFailure('telegram_request_invalid')
        url = 'https://api.telegram.org/bot' + self.config.telegram_token + '/' + method
        try:
            if self.config.telegram_proxy_url:
                raw = self._telegram_proxy_response(url, payload)
            else:
                request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(request, timeout=15) as response:
                    raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise DeliveryFailure('telegram_response_invalid')
            body = json.loads(raw)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise DeliveryFailure('telegram_http_' + str(code), code == 429 or code >= 500,
                                  60 if code == 429 else 0) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DeliveryFailure('telegram_delivery_uncertain') from None
        except (ValueError, TypeError):
            raise DeliveryFailure('telegram_response_invalid') from None
        if not isinstance(body, dict) or not body.get('ok'):
            code = body.get('error_code', 0) if isinstance(body, dict) else 0
            raise DeliveryFailure('telegram_rejected', code == 429 or (isinstance(code, int) and code >= 500))
        return body.get('result')

    def _telegram_proxy_response(self, url, payload):
        proxy = self.config.telegram_proxy_url
        match = re.fullmatch(r'socks5h://(?:127\.0\.0\.1|\[::1\]):([0-9]{1,5})', proxy) if isinstance(proxy, str) else None
        if not match or not 1 <= int(match[1]) <= 65535:
            raise DeliveryFailure('telegram_proxy_invalid')
        if not isinstance(payload, dict) or '\0' in url:
            raise DeliveryFailure('telegram_request_invalid')

        def quoted(value):
            # curl --config has its own quoting rules, independent of shell/JSON.
            escaped = (value.replace('\\', '\\\\').replace('"', '\\"').replace('\t', '\\t')
                       .replace('\n', '\\n').replace('\r', '\\r').replace('\v', '\\v'))
            return '"' + escaped + '"'

        config = '\n'.join(key + ' = ' + quoted(value) for key, value in (
            ('url', url), ('proxy', proxy), ('request', 'POST'),
            ('header', 'Content-Type: application/json'), ('data', json.dumps(payload, ensure_ascii=True)))) + '\n'
        # -q must be the first option: user curlrc could otherwise enable tracing,
        # redirects or additional requests. Secrets and request data use stdin;
        # the subprocess also gets no inherited credentials/proxy/TLS-debug env.
        command = ['/usr/bin/curl', '--disable', '--config', '-', '--silent', '--show-error',
                   '--max-time', '15', '--connect-timeout', '5', '--fail-with-body',
                   '--max-filesize', str(1024 * 1024), '--noproxy', '', '--globoff',
                   '--proto', '=https', '--write-out', '\n%{http_code}']
        try:
            with tempfile.TemporaryFile() as response:
                result = subprocess.run(command, input=config.encode('utf-8'), stdout=response,
                    stderr=subprocess.DEVNULL, timeout=20, check=False,
                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
                if result.returncode == 63:
                    raise DeliveryFailure('telegram_response_invalid')
                if result.returncode not in (0, 22):
                    raise DeliveryFailure('telegram_delivery_uncertain')
                size = response.seek(0, os.SEEK_END)
                if size < 4 or size > 1024 * 1024 + 4:
                    raise DeliveryFailure('telegram_response_invalid')
                response.seek(0)
                raw = response.read(1024 * 1024 + 4)
        except (OSError, subprocess.SubprocessError):
            raise DeliveryFailure('telegram_delivery_uncertain') from None
        if not re.fullmatch(rb'\n[0-9]{3}', raw[-4:]):
            raise DeliveryFailure('telegram_response_invalid')
        code = int(raw[-3:])
        if not 200 <= code < 300:
            raise DeliveryFailure('telegram_http_' + str(code), code == 429 or code >= 500,
                                  60 if code == 429 else 0)
        if result.returncode != 0:
            raise DeliveryFailure('telegram_response_invalid')
        return raw[:-4]

    def send_telegram(self, recipient, content, reply_markup=None):
        payload = _telegram_message(content, reply_markup)
        payload.update({'chat_id': _private_chat_id(recipient), 'link_preview_options': {'is_disabled': True}})
        result = self.telegram_request('sendMessage', payload)
        if not isinstance(result, dict) or type(result.get('message_id')) is not int or result['message_id'] <= 0:
            raise DeliveryFailure('telegram_response_invalid')
        return str(result['message_id'])

    def answer_callback_query(self, callback_query_id, text='', show_alert=False):
        result = self.telegram_request('answerCallbackQuery', _telegram_callback(callback_query_id, text, show_alert))
        if result is not True:
            raise DeliveryFailure('telegram_response_invalid')
        return 'telegram_callback_accepted'


def _verification_message(db, row, config, timestamp):
    raw = _token(db, row['candidate_id'], 'email', row['recipient'], timestamp, VERIFY_SECONDS)
    parsed = urllib.parse.urlsplit(config.public_url)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if key != 'verify']
    query.append(('verify', raw))
    link = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), ''))
    return ('Подтверждение email HR Vision',
            'Подтвердите email для уведомлений HR Vision. Откройте ссылку и войдите в аккаунт, из которого запросили подтверждение.\n\n'
            + link + '\n\nСсылка действует 30 минут. Она не выполняет вход в аккаунт. Если вы не запрашивали письмо, ничего делать не нужно.')


def process_pending(connect, settings=None, limit=10, now=None, transport=None, telegram_renderer=None):
    config, timestamp = _config(settings), _now(now)
    sender = transport if transport is not None else Transport(config)
    counts = {'delivered': 0, 'pending': 0, 'error': 0, 'disabled': 0, 'unverified': 0, 'unbound': 0}
    for _ in range(_number(limit, 10, 0, 100)):
        timestamp = _now(now)
        db = connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute("""UPDATE cn_outbox SET status='error',last_error='delivery_uncertain',lease_id=NULL,lease_until=NULL
                WHERE status='sending' AND lease_until<?""", (timestamp,))
            row = _dict(db.execute("""SELECT * FROM cn_outbox WHERE status='pending' AND next_attempt_at<=?
                ORDER BY CASE WHEN event_type='telegram.callback' THEN 0 ELSE 1 END,created_at,rowid LIMIT 1""", (timestamp,)))
            if not row:
                db.commit()
                break
            reason = _delivery_block_reason(db, row)
            if reason:
                db.execute("UPDATE cn_outbox SET status='error',last_error=? WHERE id=?", (reason, row['id']))
                db.commit()
                counts['error'] += 1
                continue
            if row['event_type'] == 'booking.confirmed':
                state = _booking_readiness(db, row, config, timestamp)
            elif row['event_type'] in BOT_EVENTS:
                state = _bot_readiness(db, row['candidate_id'], row['recipient'], config)
            else:
                recipient = _recipient(db, row['candidate_id'])
                current = recipient['email'] if row['channel'] == 'email' else recipient['telegram_chat_id']
                state = _readiness(recipient, row['channel'], config, row['event_type'] == 'email.verification', db)
                if current != row['recipient']:
                    state = 'error'
            if state != 'pending':
                db.execute('UPDATE cn_outbox SET status=?,last_error=? WHERE id=?',
                           (state, 'recipient_changed' if state == 'error' else None, row['id']))
                db.commit()
                counts[state] += 1
                continue
            lease = secrets.token_hex(16)
            db.execute("UPDATE cn_outbox SET status='sending',attempts=attempts+1,lease_id=?,lease_until=? WHERE id=?",
                       (lease, timestamp + LEASE_SECONDS, row['id']))
            reply_markup, bot_payload = None, None
            if row['event_type'] == 'booking.confirmed':
                subject, content = _booking_message(db, row, timestamp)
            elif row['event_type'] in BOT_EVENTS:
                bot_payload = json.loads(row['payload_json'])
                subject, content = '', bot_payload.get('text', '')
                reply_markup = bot_payload.get('reply_markup')
            elif row['event_type'] == 'email.verification':
                subject, content = _verification_message(db, row, config, timestamp)
            else:
                subject, body = EVENTS[row['event_type']]
                content = body + '\n\n' + config.public_url
                if row['channel'] == 'telegram' and telegram_renderer is not None:
                    rendered = telegram_renderer(db, row, config, timestamp)
                    if rendered is not None:
                        require(isinstance(rendered, dict), 'Некорректное сообщение бота.', 400)
                        rendered = _telegram_message(rendered.get('text'), rendered.get('reply_markup'))
                        content, reply_markup = rendered['text'], rendered.get('reply_markup')
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        try:
            if row['event_type'] == 'telegram.callback':
                provider_ref = sender.answer_callback_query(bot_payload['callback_query_id'], content,
                                                             bot_payload.get('show_alert', False))
            elif row['channel'] == 'email':
                provider_ref = sender.send_email(row['recipient'], subject, content, row['id'])
            elif reply_markup is not None:
                provider_ref = sender.send_telegram(row['recipient'], content, reply_markup=reply_markup)
            else:
                provider_ref = sender.send_telegram(row['recipient'], content)
            state, error, delay = 'delivered', None, 0
        except DeliveryFailure as failure:
            state = 'pending' if failure.retryable and row['attempts'] + 1 < MAX_ATTEMPTS else 'error'
            error, delay, provider_ref = failure.code, max(failure.retry_after, 30 * 2 ** row['attempts']), None
        except Exception:
            state, error, delay, provider_ref = 'error', 'delivery_uncertain', 0, None
        db = connect()
        try:
            with db:
                db.execute('''UPDATE cn_outbox SET status=?,last_error=?,next_attempt_at=?,delivered_at=?,
                    provider_ref=?,lease_id=NULL,lease_until=NULL WHERE id=? AND lease_id=?''',
                    (state, error, timestamp + delay, timestamp if state == 'delivered' else None,
                     provider_ref, row['id'], lease))
            counts[state] += 1
        finally:
            db.close()
    return counts


def poll_telegram_updates(connect, settings=None, now=None, transport=None, update_handler=None):
    config, timestamp = _config(settings), _now(now)
    if not (config.telegram_configured and config.telegram_polling):
        return {'status': 'disabled', 'processed': 0}
    sender = transport if transport is not None else Transport(config)
    db = connect()
    try:
        row = db.execute("SELECT value FROM cn_worker_state WHERE name='telegram_offset'").fetchone()
    finally:
        db.close()
    offset = int(row[0]) if row else 0
    updates = sender.telegram_request('getUpdates', {'offset': offset, 'limit': 50, 'timeout': 0,
                                                    'allowed_updates': ['message', 'callback_query']})
    if not isinstance(updates, list):
        raise DeliveryFailure('telegram_response_invalid')
    processed = 0
    for update in updates:
        db = connect()
        try:
            with db:
                _consume_telegram_update(db, update, timestamp, config, update_handler)
                next_offset = update['update_id'] + 1
                db.execute("INSERT INTO cn_worker_state VALUES ('telegram_offset',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                           (str(max(offset, next_offset)),))
            offset = max(offset, next_offset)
            processed += 1
        finally:
            db.close()
    return {'status': 'ready', 'processed': processed}


_worker_lock = threading.Lock()
_worker_thread = None


def start_worker(connect, interval=5, stop_event=None, update_handler=None, telegram_renderer=None):
    """Start once from server __main__, not imports. Off means no thread/network.

    A daemon exits with the API process. Passing an Event permits orderly local
    shutdown/tests. Any lease interrupted during process shutdown is quarantined
    as an uncertain delivery on next start rather than silently sent twice.
    """
    global _worker_thread
    if not settings().enabled:
        return None
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return _worker_thread
        stop = stop_event if stop_event is not None else threading.Event()
        delay = _number(interval, 5, 2, 60)
        def run():
            last_error_at = 0
            while not stop.is_set():
                try:
                    config = settings()
                    if not config.enabled:
                        return
                    counts = process_pending(connect, settings=config, limit=5, telegram_renderer=telegram_renderer)
                    import recruiter_notifications
                    recruiter_counts = recruiter_notifications.process_pending(connect, limit=5)
                    polled = poll_telegram_updates(connect, settings=config, update_handler=update_handler)
                    if polled.get('processed', 0) > 0:
                        # Commands enqueue replies in the polling transaction.
                        # Drain them in this same cycle so callbacks do not wait
                        # for the worker's next interval behind a loading spinner.
                        replies = process_pending(connect, settings=config, limit=5, telegram_renderer=telegram_renderer)
                        for state, count in replies.items():
                            counts[state] = counts.get(state, 0) + count
                    if any(counts.values()) or any(recruiter_counts.values()) or polled.get('processed'):
                        print(json.dumps({'worker': 'candidate_notifications', 'outbox': counts,
                                          'recruiterOutbox': recruiter_counts, 'telegram': polled}), flush=True)
                except Exception:
                    # Provider/SQLite exception reprs may contain contacts, auth, URLs.
                    # Keep the worker alive and emit at most one generic error/minute.
                    if time.monotonic() - last_error_at >= 60:
                        print(json.dumps({'worker': 'candidate_notifications', 'status': 'error',
                                          'code': 'delivery_cycle_failed'}), flush=True)
                        last_error_at = time.monotonic()
                if stop.wait(delay):
                    return
        _worker_thread = threading.Thread(target=run, name='HRCandidateDelivery', daemon=True)
        _worker_thread.start()
        return _worker_thread


def main():
    parser = argparse.ArgumentParser(description='HR Vision candidate notification worker; delivery is opt-in via environment.')
    parser.add_argument('--db', required=True)
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--interval', type=int, default=5)
    args = parser.parse_args()
    def connect():
        db = sqlite3.connect(args.db, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        return db
    db = connect()
    try:
        with db:
            init(db)
            import recruiter_notifications
            recruiter_notifications.init(db)
    finally:
        db.close()
    os.chmod(args.db, 0o600)
    while True:
        config = settings()
        counts = process_pending(connect, settings=config)
        recruiter_counts = recruiter_notifications.process_pending(connect)
        try:
            polled = poll_telegram_updates(connect, settings=config)
        except DeliveryFailure as failure:
            polled = {'status': 'error', 'code': failure.code}
        # Safe counters only. No identities, URLs, token, email, provider response or credential.
        if not args.loop or any(counts.values()) or any(recruiter_counts.values()) or polled.get('processed') or polled.get('status') == 'error':
            print(json.dumps({'outbox': counts, 'recruiterOutbox': recruiter_counts, 'telegram': polled}), flush=True)
        if not args.loop:
            break
        time.sleep(max(2, min(args.interval, 60)))


if __name__ == '__main__':
    main()
