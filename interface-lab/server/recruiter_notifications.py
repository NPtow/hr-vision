"""Booking notices for the recruiter who owns the selected slot.

Called inside the booking transaction: enqueue_booking never commits or contacts a
provider. Identity and destinations come from rc_accounts and explicit channel
verification, never from candidate input. The existing candidate notification
worker dispatches this outbox as well, under HR_NOTIFICATIONS_ENABLED=1.

Email uses the shared TLS SMTP configuration and a signed-in ownership check.
Telegram uses the same bot, with recruiter-only rc_ link capabilities. A new bot
link must be opened in a private chat. No usernames/chat IDs are guessed. Disabled
or unverified old events are not silently replayed after channels are connected.
SMTP/Telegram do not offer exactly-once delivery after a process crash: uncertain
sends are quarantined for explicit retry, never automatically sent twice.
"""
from dataclasses import replace
import hashlib
import json
import os
import re
import secrets
import urllib.parse
from zoneinfo import ZoneInfo

import candidate_notifications as delivery
from domain import require, timestamp as parse_timestamp


TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
LINK_RE = re.compile(r'^rc_[A-Za-z0-9_-]{43}$')
MOSCOW = ZoneInfo('Europe/Moscow')


def settings(env=None):
    env = os.environ if env is None else env
    default = env.get('HR_ORIGIN', 'https://hr-vision.158-160-179-53.sslip.io').rstrip('/') + \
        '/iframe.html?id=hr-vision-product--manager&viewMode=story&workspace=recruiter'
    return replace(delivery.settings(env), public_url=env.get('HR_RECRUITER_PUBLIC_URL', default))


def _config(value):
    return settings() if value is None else value


def init(db):
    # Individual execute calls retain the caller's booking transaction.
    for statement in [
        '''CREATE TABLE IF NOT EXISTS rn_channels (
            recruiter_id TEXT PRIMARY KEY REFERENCES rc_accounts(id), email TEXT NOT NULL,
            email_verified INTEGER NOT NULL DEFAULT 0, telegram_chat_id TEXT UNIQUE,
            telegram_verified_at INTEGER, updated_at INTEGER NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS rn_tokens (
            digest TEXT PRIMARY KEY, recruiter_id TEXT NOT NULL REFERENCES rc_accounts(id),
            purpose TEXT NOT NULL, recipient TEXT NOT NULL, created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL, used_at INTEGER)''',
        '''CREATE TABLE IF NOT EXISTS rn_outbox (
            id TEXT PRIMARY KEY, recruiter_id TEXT NOT NULL REFERENCES rc_accounts(id),
            event_key TEXT NOT NULL, event_type TEXT NOT NULL, interview_id TEXT,
            channel TEXT NOT NULL, recipient TEXT, payload TEXT NOT NULL,
            created_at INTEGER NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL, last_error TEXT, delivered_at INTEGER,
            lease_id TEXT, lease_until INTEGER, provider_ref TEXT,
            UNIQUE(recruiter_id,event_key,channel))''',
        'CREATE INDEX IF NOT EXISTS rn_outbox_ready ON rn_outbox(status,next_attempt_at)',
        'CREATE INDEX IF NOT EXISTS rn_outbox_owner ON rn_outbox(recruiter_id,created_at)',
        '''CREATE TABLE IF NOT EXISTS rn_telegram_updates (
            update_id INTEGER PRIMARY KEY, received_at INTEGER NOT NULL)''',
    ]:
        db.execute(statement)


def _recipient(db, recruiter_id, now=None, *, active=True):
    account = delivery._dict(db.execute('SELECT id,email,active FROM rc_accounts WHERE id=?', (recruiter_id,)))
    require(account and (account['active'] or not active), 'Рекрутер не найден.', 404)
    address, tick = delivery._email(account['email']), delivery._now(now)
    row = delivery._dict(db.execute('SELECT * FROM rn_channels WHERE recruiter_id=?', (recruiter_id,)))
    if row is None:
        db.execute('INSERT INTO rn_channels(recruiter_id,email,updated_at) VALUES (?,?,?)',
                   (recruiter_id, address, tick))
    elif row['email'] != address:
        db.execute('UPDATE rn_channels SET email=?,email_verified=0,updated_at=? WHERE recruiter_id=?',
                   (address, tick, recruiter_id))
        db.execute("UPDATE rn_tokens SET used_at=? WHERE recruiter_id=? AND purpose='email' AND used_at IS NULL",
                   (tick, recruiter_id))
    return delivery._dict(db.execute('SELECT * FROM rn_channels WHERE recruiter_id=?', (recruiter_id,)))


def public_status(db, recruiter_id, settings=None):
    recipient, config = _recipient(db, recruiter_id), _config(settings)
    address = recipient['email'].split('@')
    result = {
        'email': {'configured': config.email_configured, 'verified': bool(recipient['email_verified']),
                  'address': address[0][:1] + '***@' + address[1], 'lastDelivery': None},
        'telegram': {'configured': config.telegram_configured, 'linked': bool(recipient['telegram_chat_id']),
                     'canLink': bool(config.telegram_binding_available), 'lastDelivery': None},
    }
    for channel in result:
        readiness = delivery._readiness(recipient, channel, config)
        result[channel]['status'] = 'ready' if readiness == 'pending' else readiness
        row = delivery._dict(db.execute('''SELECT * FROM rn_outbox WHERE recruiter_id=? AND channel=?
            ORDER BY created_at DESC,rowid DESC LIMIT 1''', (recruiter_id, channel)))
        if row:
            result[channel]['lastDelivery'] = delivery._safe(row)
    return result


def _enqueue(db, recruiter_id, event_key, event_type, interview_id, payload, channel, config, tick):
    existing = delivery._dict(db.execute('''SELECT * FROM rn_outbox
        WHERE recruiter_id=? AND event_key=? AND channel=?''', (recruiter_id, event_key, channel)))
    if existing:
        require(existing['event_type'] == event_type and existing['interview_id'] == interview_id,
                'Событие уже существует с другим назначением.', 409)
        return delivery._safe(existing)
    recipient = _recipient(db, recruiter_id, tick)
    target = recipient['email'] if channel == 'email' else recipient['telegram_chat_id']
    state = delivery._readiness(recipient, channel, config, event_type == 'email.verification')
    identifier = secrets.token_hex(16)
    db.execute('''INSERT INTO rn_outbox
        (id,recruiter_id,event_key,event_type,interview_id,channel,recipient,payload,created_at,status,next_attempt_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
        (identifier, recruiter_id, event_key, event_type, interview_id, channel, target,
         json.dumps(payload, ensure_ascii=False, separators=(',', ':')), tick, state, tick))
    return delivery._safe(delivery._dict(db.execute('SELECT * FROM rn_outbox WHERE id=?', (identifier,))))


def _booking(db, recruiter_id, interview_id):
    # Neither recruiter identity nor candidate profile is trusted from request data.
    return delivery._dict(db.execute('''SELECT i.id,i.candidate_id,i.status,s.start,s.end,c.profile,
        intake.source FROM cp_interviews i JOIN cp_recruiter_slots s ON s.id=i.slot_id
        JOIN cp_recruiter_slot_owners owner ON owner.slot_id=s.id
        JOIN rc_accounts a ON a.id=owner.recruiter_id
        JOIN cp_candidates c ON c.id=i.candidate_id
        LEFT JOIN cp_intake intake ON intake.candidate_id=c.id
        WHERE i.id=? AND owner.recruiter_id=? AND a.active=1 AND s.candidate_id=i.candidate_id''',
        (interview_id, recruiter_id)))


def _plain(value, maximum):
    return ' '.join(value.split())[:maximum] if isinstance(value, str) else ''


def enqueue_booking(db, recruiter_id, interview_id, settings=None, now=None):
    """Atomic booking hook; wrong or missing ownership fails closed, no recipients guessed."""
    row = _booking(db, recruiter_id, interview_id)
    require(row and row['status'] == 'booked', 'Интервью рекрутера не найдено.', 404)
    profile = json.loads(row['profile'])
    payload = {'candidateName': _plain(profile.get('name'), 160) or 'Кандидат',
               'start': row['start'], 'end': row['end'],
               'source': {'hh': 'HH', 'manual': 'Добавлен рекрутером', None: 'Регистрация HR Vision'}.get(row['source'], 'HR Vision')}
    # Parse actual time now; corrupted scheduling data must not enqueue misleading notices.
    require(parse_timestamp(payload['end']) > parse_timestamp(payload['start']), 'Некорректное время интервью.', 409)
    config, tick = _config(settings), delivery._now(now)
    return [_enqueue(db, recruiter_id, 'interview.booked:' + interview_id, 'interview.booked',
                     interview_id, payload, channel, config, tick) for channel in ('email', 'telegram')]


def _token(db, recruiter_id, purpose, recipient, tick, ttl):
    raw = secrets.token_urlsafe(32)
    db.execute('UPDATE rn_tokens SET used_at=? WHERE recruiter_id=? AND purpose=? AND used_at IS NULL',
               (tick, recruiter_id, purpose))
    db.execute('INSERT INTO rn_tokens VALUES (?,?,?,?,?,?,NULL)',
               (hashlib.sha256(raw.encode()).hexdigest(), recruiter_id, purpose, recipient, tick, tick + ttl))
    return raw


def request_email_verification(db, recruiter_id, settings=None, now=None):
    recipient, config, tick = _recipient(db, recruiter_id, now), _config(settings), delivery._now(now)
    if recipient['email_verified']:
        return {'status': 'verified', 'queued': False, 'retryAfter': 0}
    recent = db.execute('''SELECT created_at FROM rn_outbox WHERE event_type='email.verification'
        AND (recruiter_id=? OR recipient=?) AND created_at>? ORDER BY created_at DESC''',
        (recruiter_id, recipient['email'], tick - 3600)).fetchall()
    wait = max(0, recent[0][0] + delivery.RESEND_COOLDOWN - tick) if recent else 0
    if len(recent) >= 5:
        wait = max(wait, recent[-1][0] + 3600 - tick)
    if wait:
        return {'status': 'rate_limited', 'queued': False, 'retryAfter': wait}
    result = _enqueue(db, recruiter_id, 'email.verification:' + secrets.token_hex(12),
                      'email.verification', None, {}, 'email', config, tick)
    return {'status': result['status'], 'queued': result['status'] == 'pending',
            'retryAfter': delivery.RESEND_COOLDOWN, 'notificationId': result['id']}


def verify_email(db, recruiter_id, token, settings=None, now=None):
    recipient, tick = _recipient(db, recruiter_id, now), delivery._now(now)
    require(isinstance(token, str) and TOKEN_RE.fullmatch(token), 'Ссылка подтверждения недействительна.', 400)
    updated = db.execute('''UPDATE rn_tokens SET used_at=? WHERE digest=? AND recruiter_id=?
        AND purpose='email' AND recipient=? AND expires_at>? AND used_at IS NULL''',
        (tick, hashlib.sha256(token.encode()).hexdigest(), recruiter_id, recipient['email'], tick))
    require(updated.rowcount == 1, 'Ссылка истекла или относится к другому аккаунту. Запросите новую.', 400)
    db.execute('UPDATE rn_channels SET email_verified=1,updated_at=? WHERE recruiter_id=?', (tick, recruiter_id))
    return public_status(db, recruiter_id, settings)


def create_telegram_link(db, recruiter_id, settings=None, now=None):
    _recipient(db, recruiter_id, now)
    config, tick = _config(settings), delivery._now(now)
    if not config.telegram_binding_available:
        return {'status': 'disabled', 'url': None, 'expiresAt': None}
    raw = _token(db, recruiter_id, 'telegram', '', tick, delivery.LINK_SECONDS)
    return {'status': 'ready', 'url': 'https://t.me/' + config.telegram_username + '?start=rc_' + raw,
            'expiresAt': tick + delivery.LINK_SECONDS}


def consume_telegram_update(db, update, tick):
    """Internal only: shared webhook validates provider secret / polling owns transport."""
    require(isinstance(update, dict) and type(update.get('update_id')) is int, 'Некорректное обновление.', 400)
    init(db)
    inserted = db.execute('INSERT OR IGNORE INTO rn_telegram_updates VALUES (?,?)', (update['update_id'], tick))
    if not inserted.rowcount:
        return {'status': 'duplicate'}
    message = update.get('message')
    if not isinstance(message, dict):
        return {'status': 'ignored'}
    chat, sender = message.get('chat'), message.get('from')
    if not (isinstance(chat, dict) and isinstance(sender, dict) and chat.get('type') == 'private'
            and type(chat.get('id')) is int and chat['id'] > 0 and chat['id'] == sender.get('id')
            and not sender.get('is_bot')):
        return {'status': 'ignored'}
    content = message.get('text', '')
    match = re.fullmatch(r'/start(?:@[A-Za-z0-9_]+)? rc_([A-Za-z0-9_-]{43})', content) if isinstance(content, str) else None
    if not match:
        return {'status': 'ignored'}
    row = delivery._dict(db.execute('''SELECT t.* FROM rn_tokens t JOIN rc_accounts a ON a.id=t.recruiter_id
        WHERE t.digest=? AND t.purpose='telegram' AND t.used_at IS NULL AND t.expires_at>? AND a.active=1''',
        (hashlib.sha256(match[1].encode()).hexdigest(), tick)))
    if not row:
        return {'status': 'invalid_link'}
    chat_id = str(chat['id'])
    taken = db.execute('SELECT recruiter_id FROM rn_channels WHERE telegram_chat_id=? AND recruiter_id<>?',
                      (chat_id, row['recruiter_id'])).fetchone()
    if taken:
        return {'status': 'chat_already_linked'}
    changed = db.execute('UPDATE rn_tokens SET used_at=? WHERE digest=? AND used_at IS NULL', (tick, row['digest']))
    if changed.rowcount != 1:
        return {'status': 'invalid_link'}
    db.execute('UPDATE rn_channels SET telegram_chat_id=?,telegram_verified_at=?,updated_at=? WHERE recruiter_id=?',
               (chat_id, tick, tick, row['recruiter_id']))
    return {'status': 'linked'}


def _verification_message(db, row, config, tick):
    raw = _token(db, row['recruiter_id'], 'email', row['recipient'], tick, delivery.VERIFY_SECONDS)
    parsed = urllib.parse.urlsplit(config.public_url)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
             if key != 'recruiterVerify']
    query.append(('recruiterVerify', raw))
    link = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), ''))
    return ('Подтверждение почты рекрутера HR Vision',
            'Подтвердите почту для уведомлений об интервью. Откройте ссылку и войдите в свой кабинет рекрутера.\n\n'
            + link + '\n\nСсылка действует 30 минут и не выполняет вход. Если вы не запрашивали письмо, ничего делать не нужно.')


def _booking_message(row, config):
    payload = json.loads(row['payload'])
    start, end = parse_timestamp(payload['start']).astimezone(MOSCOW), parse_timestamp(payload['end']).astimezone(MOSCOW)
    return ('Новое интервью HR Vision', 'У вас новое интервью.\n\nКандидат: ' + payload['candidateName']
            + '\nКогда: ' + start.strftime('%d.%m.%Y %H:%M') + '–' + end.strftime('%H:%M') + ' МСК'
            + '\nИсточник: ' + payload['source'] + '\n\nПодключиться к интервью можно в кабинете:\n' + config.public_url)


def retry(db, recruiter_id, notification_id, settings=None, now=None):
    """Own-account explicit retry; delivered events never send again."""
    config, tick = _config(settings), delivery._now(now)
    row = delivery._dict(db.execute('SELECT * FROM rn_outbox WHERE id=? AND recruiter_id=?', (notification_id, recruiter_id)))
    require(row, 'Уведомление не найдено.', 404)
    require(row['status'] != 'sending', 'Отправка уже выполняется.', 409)
    if row['status'] == 'delivered':
        return delivery._safe(row)
    recipient = _recipient(db, recruiter_id, tick)
    state = delivery._readiness(recipient, row['channel'], config, row['event_type'] == 'email.verification')
    target = recipient['email'] if row['channel'] == 'email' else recipient['telegram_chat_id']
    db.execute('''UPDATE rn_outbox SET status=?,recipient=?,attempts=0,next_attempt_at=?,last_error=NULL,
        lease_id=NULL,lease_until=NULL WHERE id=?''', (state, target, tick, notification_id))
    return delivery._safe(delivery._dict(db.execute('SELECT * FROM rn_outbox WHERE id=?', (notification_id,))))


def process_pending(connect, settings=None, limit=10, now=None, transport=None):
    config, tick = _config(settings), delivery._now(now)
    sender = transport if transport is not None else delivery.Transport(config)
    counts = {key: 0 for key in ('delivered', 'pending', 'error', 'disabled', 'unverified', 'unbound', 'cancelled')}
    for _ in range(delivery._number(limit, 10, 0, 100)):
        tick = delivery._now(now)
        db = connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            init(db)
            db.execute('''UPDATE rn_outbox SET status='error',last_error='delivery_uncertain',lease_id=NULL,lease_until=NULL
                WHERE status='sending' AND lease_until<?''', (tick,))
            row = delivery._dict(db.execute('''SELECT * FROM rn_outbox WHERE status='pending' AND next_attempt_at<=?
                ORDER BY created_at,rowid LIMIT 1''', (tick,)))
            if not row:
                db.commit()
                break
            active = db.execute('SELECT active FROM rc_accounts WHERE id=?', (row['recruiter_id'],)).fetchone()
            booking = _booking(db, row['recruiter_id'], row['interview_id']) if row['interview_id'] else None
            if not active or not active[0] or (row['interview_id'] and (not booking or booking['status'] != 'booked'
                    or parse_timestamp(booking['start']).timestamp() <= tick)):
                db.execute("UPDATE rn_outbox SET status='cancelled',last_error='event_no_longer_current' WHERE id=?", (row['id'],))
                db.commit()
                counts['cancelled'] += 1
                continue
            recipient = _recipient(db, row['recruiter_id'], tick)
            current = recipient['email'] if row['channel'] == 'email' else recipient['telegram_chat_id']
            state = delivery._readiness(recipient, row['channel'], config, row['event_type'] == 'email.verification')
            if current != row['recipient']:
                state = 'error'
            if state != 'pending':
                db.execute('UPDATE rn_outbox SET status=?,last_error=? WHERE id=?',
                           (state, 'recipient_changed' if state == 'error' else None, row['id']))
                db.commit()
                counts[state] += 1
                continue
            lease = secrets.token_hex(16)
            db.execute("UPDATE rn_outbox SET status='sending',attempts=attempts+1,lease_id=?,lease_until=? WHERE id=?",
                       (lease, tick + delivery.LEASE_SECONDS, row['id']))
            subject, content = (_verification_message(db, row, config, tick) if row['event_type'] == 'email.verification'
                                else _booking_message(row, config))
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
        except delivery.DeliveryFailure as failure:
            state = 'pending' if failure.retryable and row['attempts'] + 1 < delivery.MAX_ATTEMPTS else 'error'
            error, delay, provider_ref = failure.code, max(failure.retry_after, 30 * 2 ** row['attempts']), None
        except Exception:
            state, error, delay, provider_ref = 'error', 'delivery_uncertain', 0, None
        db = connect()
        try:
            with db:
                db.execute('''UPDATE rn_outbox SET status=?,last_error=?,next_attempt_at=?,delivered_at=?,
                    provider_ref=?,lease_id=NULL,lease_until=NULL WHERE id=? AND lease_id=?''',
                    (state, error, tick + delay, tick if state == 'delivered' else None, provider_ref, row['id'], lease))
            counts[state] += 1
        finally:
            db.close()
    return counts
