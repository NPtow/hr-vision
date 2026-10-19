"""Transactional meeting lifecycle notices on the existing shared outbox.

No collector/backfill: only canonical transition hooks create immutable snapshots.
Role/channel eligibility is captured once and revalidated at claim; missing channels
and uncertain deliveries are terminal and are never revived by linking or retry.
"""
import json
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import candidate_notifications as delivery
import meeting_reminders as reminders
from domain import timestamp

EVENTS = frozenset(('interview.booked', 'meeting.proposed', 'meeting.confirmed', 'meeting.completed'))


def supported(db, event, key):
    if event not in EVENTS or 'manager_id' not in delivery._columns(db, 'cp_meetings'):
        return False
    if event == 'interview.booked':
        return True
    # Legacy team-only portals keep their original candidate notice contract.
    if not delivery._columns(db, 'em_workspaces'):
        return False
    identity = key[len(event) + 1:] if key.startswith(event + ':') else ''
    return bool(db.execute("SELECT 1 FROM cp_meetings m JOIN em_workspaces w ON m.manager_id='workspace:' || w.id WHERE m.id=? AND w.kind='employer'", (identity,)).fetchone())


def record(db, event, identity):
    if event == 'interview.booked':
        rows = reminders.records(db, 'interview', identity)
        return rows[0] if rows else None
    rows = reminders._rows(db, '''SELECT m.id,m.application_id,m.start,m.end,m.revision,m.created_at,
        m.manager_id,a.candidate_id,a.vacancy_id,v.workspace_id,'employer' AS kind,
        NULL AS employer_id FROM cp_meetings m JOIN cp_applications a ON a.id=m.application_id
        JOIN em_vacancies v ON v.vacancy_id=a.vacancy_id AND v.archived_at IS NULL
        JOIN em_workspaces w ON w.id=v.workspace_id AND w.kind='employer'
        WHERE m.id=? AND m.status=? AND a.archived_at IS NULL
        AND m.manager_id='workspace:' || v.workspace_id
        AND NOT EXISTS (SELECT 1 FROM cp_meetings newer WHERE newer.application_id=a.id
            AND newer.revision>m.revision)''',
        (identity, {'meeting.proposed': 'pending', 'meeting.confirmed': 'confirmed', 'meeting.completed': 'completed'}[event]))
    return rows[0] if rows else None


def _eligible(db, saved, event, role):
    if saved['kind'] == 'interview':
        return True
    cid, aid = saved['candidate_id'], saved['application_id']
    if delivery._application_delivery_error(db, cid, aid):
        return False
    if 'status' in delivery._columns(db, 'cp_applications'):
        if db.execute('SELECT status FROM cp_applications WHERE id=?', (aid,)).fetchone()[0] != 'accepted':
            return False
    review = db.execute('SELECT state FROM em_reviews WHERE application_id=? AND workspace_id=?',
                        (aid, saved['workspace_id'])).fetchone()
    # Proposal is queued before mark_selected in the same outer transaction.
    if not review or review[0] not in ('proposed', 'selected'):
        return False
    state = db.execute('SELECT decision,after_interest,after_meeting_id FROM cp_employer_workflows WHERE application_id=?', (aid,)).fetchone()
    if state and state[0] == 'declined':
        return False
    if event == 'meeting.completed':
        if role == 'candidate':
            if state and state[2] == saved['id'] and state[1] != 'pending':
                return False
            if db.execute("SELECT 1 FROM cp_offer_versions WHERE application_id=? AND status IN ('sent','accepted')", (aid,)).fetchone():
                return False
        elif db.execute('SELECT 1 FROM cp_meeting_feedback WHERE meeting_id=? AND application_id=? AND submitted IS NOT NULL', (saved['id'], aid)).fetchone():
            return False
    return True


def enqueue(db, cid, key, event, channels, config, tick):
    identity = key[len(event) + 1:] if key.startswith(event + ':') else ''
    saved = record(db, event, identity)
    if not saved or saved['candidate_id'] != cid:
        return []
    people = [('candidate', cid, saved)]
    if event in ('meeting.confirmed', 'meeting.completed'):
        for account, in db.execute('SELECT id FROM em_accounts WHERE workspace_id=? AND active=1 AND is_team=0', (saved['workspace_id'],)):
            people.append(('employer', account, {**saved, 'employer_id': account}))
    result = []
    for role, person, snapshot in people:
        for channel in channels:
            state, target, binding = reminders.destination(db, snapshot, role, person, channel, tick, config)
            if not _eligible(db, snapshot, event, role):
                state = 'error'
            payload = {'lifecycle': True, 'record': snapshot, 'role': role, 'person': person, 'binding': binding}
            owner = 'employer:' + person if role == 'employer' else person
            db.execute('''INSERT OR IGNORE INTO cn_outbox
                (id,candidate_id,event_key,event_type,channel,recipient,payload_json,created_at,status,next_attempt_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (secrets.token_hex(16), owner, key, event, channel, target, json.dumps(payload), tick, state, tick))
            result.append(delivery._safe(delivery._dict(db.execute('SELECT * FROM cn_outbox WHERE candidate_id=? AND event_key=? AND channel=?', (owner, key, channel)))))
    return result


def is_lifecycle(row):
    return row['event_type'] in EVENTS and json.loads(row.get('payload_json') or '{}').get('lifecycle') is True


def prepare(db, row, config, tick):
    payload = json.loads(row['payload_json'])
    saved, role, person = payload['record'], payload['role'], payload['person']
    owner = 'employer:' + person if role == 'employer' else person
    current = record(db, row['event_type'], saved['id'])
    if current and role == 'employer':
        current['employer_id'] = person
    if row['candidate_id'] != owner or current != saved or not _eligible(db, saved, row['event_type'], role):
        return 'error', 'meeting_notice_no_longer_current'
    if row['event_type'] != 'meeting.completed' and timestamp(saved['end']).timestamp() <= tick:
        return 'error', 'meeting_notice_expired'
    if row['event_type'] == 'meeting.proposed' and timestamp(saved['start']).timestamp() <= tick:
        return 'error', 'meeting_notice_expired'
    state, target, binding = reminders.destination(db, saved, role, person, row['channel'], tick, config)
    if target != row['recipient'] or binding != payload['binding']:
        return 'error', 'meeting_notice_recipient_changed'
    return state, None


def message(db, row, config, tick):
    payload = json.loads(row['payload_json'])
    saved, role, event = payload['record'], payload['role'], row['event_type']
    start, end = (timestamp(saved[k]).astimezone(reminders.MOSCOW) for k in ('start', 'end'))
    title = {'interview.booked': 'Первичное интервью назначено', 'meeting.proposed': 'Предложено время встречи',
             'meeting.confirmed': 'Встреча подтверждена', 'meeting.completed': 'Как прошла встреча?'}[event]
    text = title + '\n\n' + start.strftime('%d.%m.%Y %H:%M') + '–' + end.strftime('%H:%M') + ' МСК'
    if role == 'employer':
        person = db.execute('SELECT profile FROM cp_candidates WHERE id=?', (saved['candidate_id'],)).fetchone()
        name = json.loads(person[0]).get('name', '') if person else ''
        if isinstance(name, str) and name.strip():
            text += '\nКандидат: ' + ' '.join(name.split())[:160]
    if saved['kind'] == 'employer' and delivery._columns(db, 'cp_vacancies'):
        vacancy = db.execute('SELECT body FROM cp_vacancies WHERE id=?', (saved['vacancy_id'],)).fetchone()
        body = json.loads(vacancy[0]) if vacancy else {}
        label = ' · '.join(' '.join(body[key].split())[:160] for key in ('company', 'title') if isinstance(body.get(key), str) and body[key].strip())
        if label:
            text += '\n' + label
    if event == 'meeting.completed':
        text += '\nСообщите, хотите ли вы продолжить.' if role == 'candidate' else '\nОставьте обратную связь по встрече с кандидатом.'
    label = 'Подтвердить время' if event == 'meeting.proposed' else 'Оставить обратную связь' if event == 'meeting.completed' else 'Открыть встречу'
    if role == 'employer':
        import employer_notifications
        link = reminders._url(employer_notifications.app_url(config, saved['vacancy_id']), screen='meeting',
                              pool='selected', applicationId=saved['application_id'], meetingId=saved['id'], stageId='meeting:' + saved['id'])
        if row['channel'] == 'email':
            parts = urlsplit(link)
            link = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode([(k,v) for k,v in parse_qsl(parts.query) if k != 'miniapp']), ''))
    elif event == 'interview.booked':
        link = reminders._url(config.public_url, cp='interview')
    else:
        # CandidateSearch already selects this application and exposes confirm/after.
        link = reminders._url(config.public_url, cp='home', application=saved['application_id'], meeting=saved['id'])
    button = {'text': label, 'web_app' if role == 'employer' else 'url': {'url': link} if role == 'employer' else link}
    markup = {'inline_keyboard': [[button]]} if row['channel'] == 'telegram' else None
    if row['channel'] == 'email':
        text += '\n\n' + label + ':\n' + link
    return title + ' · МашаДаша', text, markup
