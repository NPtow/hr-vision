"""Candidate email / Telegram delivery with a durable, channel-specific outbox.

No network calls occur in init, enqueue or HTTP hooks. app.py starts a daemon only
in its __main__ server entrypoint and only when HR_NOTIFICATIONS_ENABLED=1.
Alternatively run one dedicated worker (do not run both intentionally):
    python candidate_notifications.py --db /var/lib/hr-vision-api/journey.sqlite3 --loop

Environment (all off until HR_NOTIFICATIONS_ENABLED=1):
  HR_CANDIDATE_PUBLIC_URL: candidate portal URL, otherwise HR_ORIGIN + candidate story.
  HR_SMTP_HOST, HR_SMTP_PORT (587), HR_SMTP_FROM, HR_SMTP_USER, HR_SMTP_PASSWORD,
  HR_SMTP_SECURITY=starttls|ssl. TLS and certificate validation cannot be disabled.
  HR_TELEGRAM_BOT_TOKEN, HR_TELEGRAM_BOT_USERNAME.
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

Only generic notices and an authenticated portal link are sent. Verification emails
are the sole exception allowed to an unverified address: a generic ownership check,
with no person/profile/vacancy data. Tokens are generated at dispatch, SHA256-only
at rest, single-use, and redeemable only by the same authenticated candidate.
"""
import argparse
from dataclasses import dataclass, field
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
    ]
    for statement in statements:
        db.execute(statement)


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


def _readiness(recipient, channel, config, verification=False):
    if channel == 'email':
        if not config.email_configured:
            return 'disabled'
        return 'pending' if verification or recipient['email_verified'] else 'unverified'
    if not config.telegram_configured:
        return 'disabled'
    return 'pending' if recipient['telegram_chat_id'] else 'unbound'


def _safe(row):
    return {key: row[key] for key in ('id', 'event_type', 'channel', 'status', 'attempts',
                                     'created_at', 'delivered_at', 'last_error')}


def public_status(db, candidate_id, settings=None):
    config, recipient = _config(settings), _recipient(db, candidate_id)
    address = recipient['email'].split('@')
    result = {
        'email': {'configured': config.email_configured, 'verified': bool(recipient['email_verified']),
                  'address': address[0][:1] + '***@' + address[1],
                  'status': _readiness(recipient, 'email', config), 'lastDelivery': None},
        'telegram': {'configured': config.telegram_configured, 'linked': bool(recipient['telegram_chat_id']),
                     'canLink': config.telegram_binding_available,
                     'status': _readiness(recipient, 'telegram', config), 'lastDelivery': None},
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
    state = _readiness(recipient, channel, config, verification)
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


def _consume_telegram_update(db, update, timestamp):
    require(isinstance(update, dict) and type(update.get('update_id')) is int, 'Некорректное обновление.', 400)
    message = update.get('message')
    content = message.get('text', '') if isinstance(message, dict) else ''
    if isinstance(content, str) and re.fullmatch(r'/start(?:@[A-Za-z0-9_]+)? rc_[A-Za-z0-9_-]{43}', content):
        # One bot/polling offset, separate identity namespaces and link tokens.
        import recruiter_notifications
        return recruiter_notifications.consume_telegram_update(db, update, timestamp)
    inserted = db.execute('INSERT OR IGNORE INTO cn_telegram_updates VALUES (?,?)', (update['update_id'], timestamp))
    if not inserted.rowcount:
        return {'status': 'duplicate'}
    message = update.get('message')
    if not isinstance(message, dict):
        return {'status': 'ignored'}
    chat, sender = message.get('chat'), message.get('from')
    if not (isinstance(chat, dict) and isinstance(sender, dict) and chat.get('type') == 'private'
            and type(chat.get('id')) is int and chat['id'] > 0
            and chat['id'] == sender.get('id') and not sender.get('is_bot')):
        return {'status': 'ignored'}
    content = message.get('text', '')
    match = re.fullmatch(r'/start(?:@[A-Za-z0-9_]+)? ([A-Za-z0-9_-]{40,64})', content) if isinstance(content, str) else None
    if not match:
        return {'status': 'ignored'}
    row = _dict(db.execute("SELECT * FROM cn_tokens WHERE digest=? AND purpose='telegram' AND used_at IS NULL AND expires_at>?",
                           (_hash(match[1]), timestamp)))
    if not row:
        return {'status': 'invalid_link'}
    chat_id = str(chat['id'])
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


def handle_telegram_update(db, update, supplied_secret='', settings=None, now=None):
    config = _config(settings)
    expected = config.telegram_webhook_secret
    require(config.telegram_configured and re.fullmatch(r'[A-Za-z0-9_-]{32,256}', expected)
            and isinstance(supplied_secret, str) and supplied_secret.isascii() and len(supplied_secret) <= 256
            and hmac.compare_digest(expected, supplied_secret), 'Нет доступа.', 403)
    return _consume_telegram_update(db, update, _now(now))


def retry(db, notification_id, settings=None, now=None, candidate_id=None):
    """Explicit retry; HTTP callers must check operator or matching candidate access."""
    config, timestamp = _config(settings), _now(now)
    row = _dict(db.execute('SELECT * FROM cn_outbox WHERE id=?', (notification_id,)))
    require(row and (candidate_id is None or row['candidate_id'] == candidate_id), 'Уведомление не найдено.', 404)
    require(row['status'] != 'sending', 'Отправка уже выполняется.', 409)
    if row['status'] == 'delivered':
        return _safe(row)
    recipient = _recipient(db, row['candidate_id'])
    state = _readiness(recipient, row['channel'], config, row['event_type'] == 'email.verification')
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
        request = urllib.request.Request('https://api.telegram.org/bot' + self.config.telegram_token + '/' + method,
            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        try:
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

    def send_telegram(self, recipient, content):
        result = self.telegram_request('sendMessage', {'chat_id': recipient, 'text': content,
            'link_preview_options': {'is_disabled': True}})
        if not isinstance(result, dict) or not result.get('message_id'):
            raise DeliveryFailure('telegram_response_invalid')
        return str(result['message_id'])


def _verification_message(db, row, config, timestamp):
    raw = _token(db, row['candidate_id'], 'email', row['recipient'], timestamp, VERIFY_SECONDS)
    parsed = urllib.parse.urlsplit(config.public_url)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if key != 'verify']
    query.append(('verify', raw))
    link = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), ''))
    return ('Подтверждение email HR Vision',
            'Подтвердите email для уведомлений HR Vision. Откройте ссылку и войдите в аккаунт, из которого запросили подтверждение.\n\n'
            + link + '\n\nСсылка действует 30 минут. Она не выполняет вход в аккаунт. Если вы не запрашивали письмо, ничего делать не нужно.')


def process_pending(connect, settings=None, limit=10, now=None, transport=None):
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
            row = _dict(db.execute("SELECT * FROM cn_outbox WHERE status='pending' AND next_attempt_at<=? ORDER BY created_at,rowid LIMIT 1", (timestamp,)))
            if not row:
                db.commit()
                break
            recipient = _recipient(db, row['candidate_id'])
            current = recipient['email'] if row['channel'] == 'email' else recipient['telegram_chat_id']
            state = _readiness(recipient, row['channel'], config, row['event_type'] == 'email.verification')
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
            if row['event_type'] == 'email.verification':
                subject, content = _verification_message(db, row, config, timestamp)
            else:
                subject, body = EVENTS[row['event_type']]
                content = body + '\n\n' + config.public_url
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        try:
            provider_ref = (sender.send_email(row['recipient'], subject, content, row['id']) if row['channel'] == 'email'
                            else sender.send_telegram(row['recipient'], content))
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


def poll_telegram_updates(connect, settings=None, now=None, transport=None):
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
    updates = sender.telegram_request('getUpdates', {'offset': offset, 'limit': 50, 'timeout': 0, 'allowed_updates': ['message']})
    if not isinstance(updates, list):
        raise DeliveryFailure('telegram_response_invalid')
    processed = 0
    for update in updates:
        db = connect()
        try:
            with db:
                _consume_telegram_update(db, update, timestamp)
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


def start_worker(connect, interval=5, stop_event=None):
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
                    counts = process_pending(connect, settings=config, limit=5)
                    import recruiter_notifications
                    recruiter_counts = recruiter_notifications.process_pending(connect, limit=5)
                    polled = poll_telegram_updates(connect, settings=config)
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
