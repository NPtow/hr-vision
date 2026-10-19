"""Bounded reminder scheduling on the existing role outboxes and single worker.

No provider calls, account grants, token issuance or historical replay. A claimed
send whose outcome is unknown remains quarantined by the existing dispatcher.
"""
import hashlib
import json
import secrets
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import candidate_notifications as delivery
import recruiter_notifications as recruiter
from domain import timestamp
from candidate_booking import CALL_GRACE_SECONDS

EVENT = 'meeting.reminder'
OFFSETS = (86400, 900)
CATCH_UP = 600
MOSCOW = ZoneInfo('Europe/Moscow')


def _seconds(value):
    return timestamp(value).timestamp()


def _rows(db, sql, args=()):
    cursor = db.execute(sql, args)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def records(db, kind, identity=None, *, navigation=False):
    """Only canonical current participants; never a client-supplied recipient."""
    if kind == 'interview':
        if 'created_at' not in delivery._columns(db, 'cp_interviews') or not delivery._columns(db, 'rc_accounts'):
            return []
        return _rows(db, '''SELECT i.id,i.candidate_id,i.created_at,s.start,s.end,o.recruiter_id,
            'interview' AS kind,1 AS revision FROM cp_interviews i
            JOIN cp_recruiter_slots s ON s.id=i.slot_id AND s.candidate_id=i.candidate_id
            JOIN cp_recruiter_slot_owners o ON o.slot_id=s.id
            JOIN rc_accounts r ON r.id=o.recruiter_id AND r.active=1
            JOIN cp_candidates c ON c.id=i.candidate_id
            WHERE i.status IN ''' + ("('booked','live')" if navigation else "('booked')") + (' AND i.id=?' if identity else ''), (identity,) if identity else ())
    if not delivery._columns(db, 'em_reviews') or 'manager_id' not in delivery._columns(db, 'cp_meetings'):
        return []
    return _rows(db, '''SELECT m.id,a.candidate_id,m.start,m.end,m.revision,m.created_at,m.confirmed_at,
        m.manager_id,a.id AS application_id,a.vacancy_id,v.workspace_id,e.id AS employer_id,
        'employer' AS kind FROM cp_meetings m JOIN cp_applications a ON a.id=m.application_id
        JOIN em_vacancies v ON v.vacancy_id=a.vacancy_id AND v.archived_at IS NULL
        JOIN em_reviews r ON r.application_id=a.id AND r.workspace_id=v.workspace_id AND r.state='selected'
        JOIN em_accounts e ON e.workspace_id=v.workspace_id AND e.active=1 AND e.is_team=0
        JOIN em_workspaces w ON w.id=e.workspace_id AND w.kind='employer'
        JOIN cp_candidates c ON c.id=a.candidate_id
        WHERE m.status='confirmed' AND a.archived_at IS NULL
        AND m.manager_id='workspace:' || v.workspace_id ''' + (' AND m.id=?' if identity else ''), (identity,) if identity else ())


def _booking(db, record, tick, config):
    if not delivery._columns(db, 'cp_booking_profile_consents'):
        return None
    consent = delivery._dict(db.execute('''SELECT c.invitation_id,c.contact_email,b.token_hash
        FROM cp_booking_profile_consents c JOIN cp_booking_invitations b
        ON b.id=c.invitation_id AND b.candidate_id=c.candidate_id
        WHERE c.interview_id=? AND c.candidate_id=? AND b.revoked_at IS NULL AND b.expires>?''',
        (record['id'], record['candidate_id'], tick)))
    if not consent:
        return None
    # The exact personal URL persisted at booking is reused; no new capability.
    rows = db.execute('''SELECT payload_json FROM cn_outbox WHERE candidate_id=?
        AND event_key=? AND event_type='booking.confirmed' ORDER BY rowid''',
        (record['candidate_id'], 'booking:' + consent['invitation_id'])).fetchall()
    for raw, in rows:
        payload = json.loads(raw)
        if payload.get('interviewId') != record['id'] or payload.get('invitationId') != consent['invitation_id']:
            continue
        url = payload.get('url', '')
        parts = urlsplit(url)
        origins = (config.product_origin, *config.product_aliases)
        token = parse_qs(parts.fragment).get('booking', [''])[0]
        if (parts.scheme == 'https' and 'https://' + parts.netloc in origins and not parts.username and not parts.password
                and parts.path == '/iframe.html' and parse_qs(parts.query).get('cp') == ['invite-booking']
                and hashlib.sha256(token.encode()).hexdigest() == consent['token_hash']):
            return {**consent, 'url': url}
    return None


def participants(db, record, tick, config):
    cid = record['candidate_id']
    account = db.execute('SELECT email FROM cp_candidates WHERE id=?', (cid,)).fetchone()
    if account and account[0]:
        people = [('candidate', cid)]
    elif record['kind'] == 'interview':
        people = [('booking', cid)]
    else:
        people = []
    people.append(('recruiter', record['recruiter_id']) if record['kind'] == 'interview'
                  else ('employer', record['employer_id']))
    return people


def destination(db, record, role, person, channel, tick, config):
    """Return readiness, destination and binding version without changing identity."""
    if (role, person) not in participants(db, record, tick, config):
        return 'error', None, None
    ready = 'pending' if (config.email_configured_for(role) if channel == 'email' else config.telegram_configured) else 'disabled'
    if role == 'booking':
        booking = _booking(db, record, tick, config)
        if not booking:
            return 'unverified', None, None
        if channel == 'email':
            return ready, booking['contact_email'], booking['invitation_id']
        bound = db.execute('SELECT chat_id,linked_at FROM cn_booking_telegram WHERE invitation_id=? AND candidate_id=?',
                           (booking['invitation_id'], person)).fetchone()
        return (ready, bound[0], [booking['invitation_id'], bound[1]]) if bound else ('unbound', None, None)
    if role == 'candidate':
        recipient = delivery._dict(db.execute('SELECT * FROM cn_recipients WHERE candidate_id=?', (person,)))
        account = db.execute('SELECT email FROM cp_candidates WHERE id=?', (person,)).fetchone()
        if not recipient or not account or recipient['email'] != account[0]:
            return 'unverified', None, None
        return (delivery._readiness(recipient, channel, config, db=db),
                recipient['email'] if channel == 'email' else recipient['telegram_chat_id'],
                recipient['email'] if channel == 'email' else recipient['telegram_verified_at'])
    if role == 'recruiter':
        recipient = recruiter._recipient(db, person, tick)
        return (recruiter._readiness(recipient, channel, config),
                recipient['email'] if channel == 'email' else recipient['telegram_chat_id'],
                recipient['email'] if channel == 'email' else [recipient['telegram_verified_at'], recipient['telegram_generation']])
    account = db.execute('SELECT email FROM em_accounts WHERE id=? AND active=1 AND is_team=0 AND workspace_id=?',
                         (person, record['workspace_id'])).fetchone()
    if not account:
        return 'error', None, None
    if channel == 'email':
        # Operational mail to the provisioned personal employer account, like recruiter booking notices.
        return ready, delivery._email(account[0]), account[0]
    bound = db.execute('SELECT chat_id,generation FROM en_bindings WHERE account_id=?', (person,)).fetchone()
    return (ready, bound[0], bound[1]) if bound else ('unbound', None, None)


def _enqueue(db, record, role, person, channel, offset, tick, config, *, navigation_key=None):
    state, recipient, binding = destination(db, record, role, person, channel, tick, config)
    payload = {'record': record, 'role': role, 'person': person, 'offset': offset, 'binding': binding}
    if navigation_key:
        payload['navigation'] = True
    key = navigation_key or 'reminder:' + hashlib.sha256(json.dumps([record, role, person, offset], sort_keys=True).encode()).hexdigest()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if role == 'recruiter':
        db.execute('''INSERT OR IGNORE INTO rn_outbox
            (id,recruiter_id,event_key,event_type,interview_id,channel,recipient,payload,created_at,status,next_attempt_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (secrets.token_hex(16), person, key, EVENT, record['id'], channel, recipient, encoded, tick, state, tick))
    else:
        # Employer rows use an isolated owner namespace, never a candidate identity.
        owner = 'employer:' + person if role == 'employer' else person
        db.execute('''INSERT OR IGNORE INTO cn_outbox
            (id,candidate_id,event_key,event_type,channel,recipient,payload_json,created_at,status,next_attempt_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (secrets.token_hex(16), owner, key, EVENT, channel, recipient, encoded, tick, state, tick))


def collect(db, config=None, tick=None):
    config, tick = delivery._config(config), delivery._now(tick)
    for kind in ('interview', 'employer'):
        for record in records(db, kind):
            start, end = _seconds(record['start']), _seconds(record['end'])
            if end <= start or start <= tick:
                continue
            created = max(_seconds(record['created_at']), _seconds(record.get('confirmed_at') or record['created_at']))
            for offset in OFFSETS:
                due = start - offset
                if not (due <= tick <= due + CATCH_UP and created <= due):
                    continue
                for role, person in participants(db, record, tick, config):
                    for channel in ('email', 'telegram'):
                        _enqueue(db, record, role, person, channel, offset, tick, config)


def prepare(db, row, config, tick):
    payload = json.loads(row.get('payload_json') or row.get('payload') or '{}')
    saved = payload['record']
    current = records(db, saved['kind'], saved['id'], navigation=payload.get('navigation', False))
    if not current or current[0] != saved:
        return 'error', 'reminder_no_longer_current'
    start, end = _seconds(saved['start']), _seconds(saved['end'])
    due = start - payload['offset']
    expired = end + CALL_GRACE_SECONDS <= tick if payload.get('navigation') else start <= tick or not due <= tick <= due + CATCH_UP
    if expired or end <= start:
        return 'error', 'reminder_expired'
    state, target, binding = destination(db, saved, payload['role'], payload['person'], row['channel'], tick, config)
    if target != row['recipient'] or binding != payload['binding']:
        return 'error', 'reminder_recipient_changed'
    return state, None


def _url(base, **params):
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


def message(db, row, config, tick):
    payload = json.loads(row.get('payload_json') or row.get('payload'))
    record, role = payload['record'], payload['role']
    start, end = timestamp(record['start']).astimezone(MOSCOW), timestamp(record['end']).astimezone(MOSCOW)
    title = 'Первичное интервью' if record['kind'] == 'interview' else 'Встреча с работодателем'
    text = ('Моя запись' if payload.get('navigation') else 'Напоминание: ' + title.lower()) + '\n\n'
    text += start.strftime('%d.%m.%Y %H:%M') + '–' + end.strftime('%H:%M') + ' МСК'
    if role == 'booking':
        link = _booking(db, record, tick, config)['url']
    elif role == 'recruiter':
        link = config.product_origin.rstrip('/') + '/iframe.html?' + urlencode({
            'id': 'hr-vision-product--manager', 'viewMode': 'story', 'workspace': 'recruiter'})
    elif role == 'employer':
        import employer_notifications
        link = _url(employer_notifications.app_url(config, record['vacancy_id']), screen='meeting',
                    pool='selected', applicationId=record['application_id'], meetingId=record['id'], stageId='meeting:' + record['id'])
        if row['channel'] == 'email':
            # Email opens the ordinary authenticated website; Telegram opens signed Mini App entry.
            parts = urlsplit(link)
            link = urlunsplit((parts.scheme, parts.netloc, parts.path,
                urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != 'miniapp']), ''))
    else:
        link = _url(config.public_url, cp='calendar')
    button = {'text': 'Открыть встречу', 'web_app': {'url': link}} if role == 'employer' else {'text': 'Открыть встречу', 'url': link}
    markup = {'inline_keyboard': [[button]]} if row['channel'] == 'telegram' else None
    if row['channel'] == 'email':
        text += '\n\nВремя и подключение к встрече:\n' + link
    return title + ' · МашаДаша', text, markup


def booking_navigation(db, chat, config, tick, key):
    """Only exact consented interviews bound to this chat; no account session."""
    found = False
    for record in records(db, 'interview', navigation=True):
        if ('booking', record['candidate_id']) not in participants(db, record, tick, config) or _seconds(record['end']) + CALL_GRACE_SECONDS <= tick:
            continue
        state, target, _ = destination(db, record, 'booking', record['candidate_id'], 'telegram', tick, config)
        if state == 'pending' and target == chat:
            _enqueue(db, record, 'booking', record['candidate_id'], 'telegram', 0, tick, config,
                     navigation_key=key + ':' + record['id'])
            found = True
    return found
