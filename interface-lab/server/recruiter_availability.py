"""Weekly and date-specific availability in the existing recruiter booking pool.

The caller owns the SQLite transaction and its scheduling lock. Editing an owned
rule preserves its identity and all booked/history slots. Materialization reconciles
only free future generated slots before appending the rolling horizon.
"""
import datetime as dt
import hashlib
import json
import re
import secrets
from zoneinfo import ZoneInfo

from domain import iso, now, require, text, timestamp

TIMEZONE = 'Europe/Moscow'
MOSCOW = ZoneInfo(TIMEZONE)
HORIZON_DAYS = 28
MAX_BLOCKS_PER_RECRUITER = 20
STEP = dt.timedelta(minutes=30)
FIELDS = {'recruiterName', 'startDate', 'weekdays', 'startTime', 'endTime', 'timezone'}


def recruiter_key(value):
    return ' '.join(value.casefold().replace('ё', 'е').split())


def init(db):
    # execute(), not executescript(): initialization must not commit caller writes.
    db.execute('''CREATE TABLE IF NOT EXISTS cp_availability_blocks (
      id TEXT PRIMARY KEY, recruiter_name TEXT NOT NULL, recruiter_key TEXT NOT NULL,
      start_date TEXT NOT NULL, weekdays TEXT NOT NULL, start_time TEXT NOT NULL,
      end_time TEXT NOT NULL, timezone TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(recruiter_key,start_date,weekdays,start_time,end_time,timezone))''')
    db.execute('''CREATE TABLE IF NOT EXISTS cp_generated_slots (
      slot_id TEXT PRIMARY KEY REFERENCES cp_recruiter_slots(id),
      block_id TEXT NOT NULL REFERENCES cp_availability_blocks(id))''')
    db.execute('''CREATE INDEX IF NOT EXISTS cp_availability_recruiter
      ON cp_availability_blocks(recruiter_key)''')
    # Separate ownership mappings preserve historical slot/block IDs and legacy
    # positional inserts. Old name-only schedules stay unassigned.
    db.execute('''CREATE TABLE IF NOT EXISTS cp_recruiter_block_owners (
      block_id TEXT PRIMARY KEY REFERENCES cp_availability_blocks(id),
      recruiter_id TEXT NOT NULL REFERENCES rc_accounts(id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS cp_recruiter_slot_owners (
      slot_id TEXT PRIMARY KEY REFERENCES cp_recruiter_slots(id),
      recruiter_id TEXT NOT NULL REFERENCES rc_accounts(id))''')
    db.execute('CREATE INDEX IF NOT EXISTS cp_slot_owner ON cp_recruiter_slot_owners(recruiter_id)')
    db.execute('CREATE INDEX IF NOT EXISTS cp_block_owner ON cp_recruiter_block_owners(recruiter_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS cp_availability_dates (
      recruiter_id TEXT NOT NULL REFERENCES rc_accounts(id), date TEXT NOT NULL,
      intervals TEXT NOT NULL, PRIMARY KEY(recruiter_id,date))''')
    # Retain provenance even when an override is removed: old links/bookings keep
    # their references, but retired free slots must never become bookable again.
    db.execute('''CREATE TABLE IF NOT EXISTS cp_date_generated_slots (
      slot_id TEXT PRIMARY KEY REFERENCES cp_recruiter_slots(id))''')


def _block(row):
    return {'id': row[0], 'recruiterName': row[1], 'startDate': row[3],
            'weekdays': json.loads(row[4]), 'startTime': row[5], 'endTime': row[6],
            'timezone': row[7], 'createdAt': row[8]}


def blocks(db, recruiter_id=None):
    return [_block(row) for row in db.execute('''SELECT id,recruiter_name,recruiter_key,
      start_date,weekdays,start_time,end_time,timezone,created_at
      FROM cp_availability_blocks''' + (''' WHERE id IN (
      SELECT block_id FROM cp_recruiter_block_owners WHERE recruiter_id=?)''' if recruiter_id else '') +
      ' ORDER BY recruiter_key,start_date,start_time,id', (recruiter_id,) if recruiter_id else ())]


def _time(value):
    require(isinstance(value, str) and re.fullmatch(r'(?:[01][0-9]|2[0-3]):(?:00|30)', value),
            'Время должно быть в формате ЧЧ:ММ с шагом 30 минут.', 400)
    return dt.time.fromisoformat(value)


def _id(prefix, values):
    encoded = json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()
    return prefix + hashlib.sha256(encoded).hexdigest()[:32]


def date_overrides(db, recruiter_id):
    return [{'date': day, 'intervals': json.loads(intervals)} for day, intervals in db.execute(
        'SELECT date,intervals FROM cp_availability_dates WHERE recruiter_id=? ORDER BY date', (recruiter_id,))]


def set_date_override(db, body, *, recruiter):
    require(recruiter and not recruiter.get('legacy'), 'Войдите в свой кабинет рекрутера.', 401)
    require(isinstance(body, dict) and set(body) == {'date', 'intervals'},
            'Укажите дату и интервалы для интервью.', 400)
    day = body['date']
    require(isinstance(day, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', day), 'Укажите дату.', 400)
    try:
        parsed = dt.date.fromisoformat(day)
    except ValueError:
        require(False, 'Некорректная дата.', 400)
    today = now().astimezone(MOSCOW).date()
    require(today <= parsed <= today + dt.timedelta(days=370), 'Выберите дату в пределах ближайшего года.', 400)
    ranges = body['intervals']
    # null restores the weekly rule; [] explicitly closes this date.
    if ranges is None:
        db.execute('DELETE FROM cp_availability_dates WHERE recruiter_id=? AND date=?', (recruiter['id'], day))
        return
    require(isinstance(ranges, list) and len(ranges) <= 24, 'Укажите не больше 24 интервалов.', 400)
    normalized = []
    for item in ranges:
        require(isinstance(item, dict) and set(item) == {'startTime', 'endTime'}, 'Проверьте интервал.', 400)
        start, end = _time(item['startTime']), _time(item['endTime'])
        minutes = end.hour * 60 + end.minute - start.hour * 60 - start.minute
        require(30 <= minutes <= 720, 'Интервал должен быть от 30 минут до 12 часов в пределах одного дня.', 400)
        normalized.append({'startTime': item['startTime'], 'endTime': item['endTime']})
    normalized.sort(key=lambda item: item['startTime'])
    require(all(left['endTime'] <= right['startTime'] for left, right in zip(normalized, normalized[1:])),
            'Интервалы не должны пересекаться.', 400)
    db.execute('''INSERT INTO cp_availability_dates VALUES (?,?,?)
      ON CONFLICT(recruiter_id,date) DO UPDATE SET intervals=excluded.intervals''',
      (recruiter['id'], day, json.dumps(normalized, separators=(',', ':'))))


def open_slot_ids(db):
    """Schedule and overlap policy shared by list reads and booking writes.

    Keep all stored rows and FK references when a date changes; hide obsolete
    free times instead of deleting history. Includes manual slots in date policy.
    """
    overrides = {(owner, day): json.loads(ranges) for owner, day, ranges in db.execute(
        'SELECT recruiter_id,date,intervals FROM cp_availability_dates')}
    generated = {row[0] for row in db.execute('SELECT slot_id FROM cp_date_generated_slots')}
    result, occupied = set(), {}
    rows = db.execute('''SELECT s.id,s.start,s.end,o.recruiter_id,s.candidate_id,s.recruiter_name
      FROM cp_recruiter_slots s LEFT JOIN cp_recruiter_slot_owners o ON o.slot_id=s.id
      ORDER BY (s.candidate_id IS NOT NULL) DESC,s.start,s.id''').fetchall()
    for sid, start, end, owner, candidate, name in rows:
        begin, finish = timestamp(start).astimezone(MOSCOW), timestamp(end).astimezone(MOSCOW)
        ranges = overrides.get((owner, begin.date().isoformat()))
        permitted = sid not in generated if ranges is None else any(
            dt.datetime.combine(begin.date(), dt.time.fromisoformat(item['startTime']), MOSCOW) <= begin
            and finish <= dt.datetime.combine(begin.date(), dt.time.fromisoformat(item['endTime']), MOSCOW)
            for item in ranges)
        intervals = occupied.setdefault('account:' + owner if owner else 'legacy:' + recruiter_key(name), [])
        # Restoring a template can leave historical date/manual rows at the same
        # time. Offer exactly one free slot, and never overlap a booked interview.
        if permitted and (candidate or not any(begin < right and left < finish for left, right in intervals)):
            result.add(sid)
            intervals.append((begin, finish))
        elif candidate:
            intervals.append((begin, finish))
    return result


def add_block(db, body, *, recruiter=None):
    require(isinstance(body, dict) and set(body) in (FIELDS, FIELDS | {'blockId'}),
            'Укажите рекрутера, дату начала, дни недели, время и часовой пояс.', 400)
    principal = recruiter
    editing = 'blockId' in body
    current = None
    if editing:
        require(principal, 'Войдите в свой кабинет рекрутера.', 401)
        identifier = text(body['blockId'], 100)
        current = db.execute('''SELECT b.id,b.recruiter_name,b.recruiter_key,b.start_date,
          b.weekdays,b.start_time,b.end_time,b.timezone,b.created_at
          FROM cp_availability_blocks b JOIN cp_recruiter_block_owners o ON o.block_id=b.id
          WHERE b.id=? AND o.recruiter_id=?''', (identifier, principal['id'])).fetchone()
        require(current, 'Расписание не найдено.', 404)
    recruiter = ' '.join(text(principal['name'] if principal else body['recruiterName'], 140).split())
    key = 'account:' + principal['id'] if principal else recruiter_key(recruiter)
    require(body['timezone'] == TIMEZONE, 'Пока доступен часовой пояс Europe/Moscow.', 400)
    day_text = body['startDate']
    require(isinstance(day_text, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', day_text),
            'Укажите дату начала в формате ГГГГ-ММ-ДД.', 400)
    try:
        first_day = dt.date.fromisoformat(day_text)
    except ValueError:
        require(False, 'Некорректная дата начала.', 400)
    instant = now()
    require(first_day <= instant.astimezone(MOSCOW).date() + dt.timedelta(days=370),
            'Выберите дату начала в пределах года.', 400)
    weekdays = body['weekdays']
    require(isinstance(weekdays, list) and 1 <= len(weekdays) <= 7
            and all(type(day) is int and 0 <= day <= 6 for day in weekdays),
            'Выберите дни недели от понедельника до воскресенья.', 400)
    weekdays = sorted(set(weekdays))
    start, end = _time(body['startTime']), _time(body['endTime'])
    minutes = end.hour * 60 + end.minute - start.hour * 60 - start.minute
    require(30 <= minutes <= 12 * 60,
            'Интервал в пределах одного дня должен быть от 30 минут до 12 часов.', 400)
    desired = (day_text, json.dumps(weekdays, separators=(',', ':')),
               body['startTime'], body['endTime'], TIMEZONE)
    identifier = current[0] if editing else _id('availability_', [key, day_text, weekdays,
                                                               body['startTime'], body['endTime'], TIMEZONE])
    existing = db.execute('''SELECT id,recruiter_name,recruiter_key,start_date,weekdays,
      start_time,end_time,timezone,created_at FROM cp_availability_blocks
      WHERE recruiter_key=?''' + (''' OR id IN (SELECT block_id FROM cp_recruiter_block_owners
      WHERE recruiter_id=?)''' if principal else ''), (key, principal['id']) if principal else (key,)).fetchall()
    for row in existing:
        if editing and row[0] == current[0]:
            continue
        if not editing and row[3:8] == desired:
            return _block(row)
        # Rules have no end date. Shared weekdays will therefore eventually coincide,
        # even when their first dates or current generated horizons differ.
        require(not (set(weekdays).intersection(json.loads(row[4]))
                     and body['startTime'] < row[6] and row[5] < body['endTime']),
                'У рекрутера уже есть расписание на эти дни и время.', 409)
    require(editing or len(existing) < MAX_BLOCKS_PER_RECRUITER,
            'У рекрутера может быть не больше 20 интервалов расписания.', 409)
    if editing:
        row = (identifier, recruiter, key, *desired, current[8])
        db.execute('''UPDATE cp_availability_blocks SET recruiter_name=?,recruiter_key=?,
          start_date=?,weekdays=?,start_time=?,end_time=?,timezone=? WHERE id=?''', (*row[1:8], identifier))
        return _block(row)
    # An edited rule keeps its original ID, even after its schedule changes.
    # Recreating the original interval must not collide with that retained ID.
    if db.execute('SELECT 1 FROM cp_availability_blocks WHERE id=?', (identifier,)).fetchone():
        identifier = 'availability_' + secrets.token_hex(16)
    row = (identifier, recruiter, key, *desired, iso(instant))
    db.execute('INSERT INTO cp_availability_blocks VALUES (?,?,?,?,?,?,?,?,?)', row)
    if principal:
        db.execute('INSERT INTO cp_recruiter_block_owners VALUES (?,?)', (identifier, principal['id']))
    return _block(row)


def _matches_rule(block, start, end):
    local = start.astimezone(MOSCOW)
    first = dt.datetime.combine(local.date(), dt.time.fromisoformat(block['startTime']), MOSCOW)
    last = dt.datetime.combine(local.date(), dt.time.fromisoformat(block['endTime']), MOSCOW)
    return (local.date() >= dt.date.fromisoformat(block['startDate'])
            and local.weekday() in block['weekdays'] and first <= start and end <= last
            and end - start == STEP and (start - first) % STEP == dt.timedelta())


def _prune_obsolete_free_slots(db, rules, instant):
    """Keep bookings/references/history; never touch manually created slots.

    Also runs after a booking is later released, so a retired time cannot re-enter
    the public pool on cancellation. IDs still in the edited rule remain stable.
    """
    for slot_id, start, end, block_id, slot_owner, block_owner in db.execute('''
      SELECT s.id,s.start,s.end,g.block_id,so.recruiter_id,bo.recruiter_id
      FROM cp_recruiter_slots s JOIN cp_generated_slots g ON g.slot_id=s.id
      LEFT JOIN cp_recruiter_slot_owners so ON so.slot_id=s.id
      LEFT JOIN cp_recruiter_block_owners bo ON bo.block_id=g.block_id
      WHERE s.candidate_id IS NULL AND NOT EXISTS
        (SELECT 1 FROM cp_interviews i WHERE i.slot_id=s.id)''').fetchall():
        begin, finish = timestamp(start), timestamp(end)
        rule = rules.get(block_id)
        if (begin <= instant or not rule or slot_owner != block_owner
                or _matches_rule(rule, begin, finish)):
            continue
        db.execute('DELETE FROM cp_generated_slots WHERE slot_id=?', (slot_id,))
        db.execute('DELETE FROM cp_recruiter_slot_owners WHERE slot_id=?', (slot_id,))
        db.execute('DELETE FROM cp_recruiter_slots WHERE id=?', (slot_id,))


def materialize(db):
    """Reconcile future free times and append 28 days; return insert count."""
    instant = now()
    today = instant.astimezone(MOSCOW).date()
    rules = blocks(db)
    _prune_obsolete_free_slots(db, {block['id']: block for block in rules}, instant)
    overrides = {(owner, day): json.loads(ranges) for owner, day, ranges in db.execute(
        'SELECT recruiter_id,date,intervals FROM cp_availability_dates')}
    allowed = open_slot_ids(db)
    occupied = {}
    for sid, start, end, recruiter, owner, candidate in db.execute('''SELECT s.id,s.start,s.end,s.recruiter_name,o.recruiter_id,s.candidate_id
      FROM cp_recruiter_slots s LEFT JOIN cp_recruiter_slot_owners o ON o.slot_id=s.id'''):
        finish = timestamp(end)
        if finish > instant and (candidate or sid in allowed):
            key = 'account:' + owner if owner else recruiter_key(recruiter)
            occupied.setdefault(key, []).append((timestamp(start), finish))
    created = 0
    for block in rules:
        first_day = max(today, dt.date.fromisoformat(block['startDate']))
        row = db.execute('SELECT recruiter_key FROM cp_availability_blocks WHERE id=?', (block['id'],)).fetchone()
        key = row[0]
        owner = db.execute('SELECT recruiter_id FROM cp_recruiter_block_owners WHERE block_id=?', (block['id'],)).fetchone()
        if owner:
            key = 'account:' + owner[0]
        intervals = occupied.setdefault(key, [])
        for offset in range(HORIZON_DAYS):
            day = first_day + dt.timedelta(days=offset)
            if day.weekday() not in block['weekdays'] or (owner and (owner[0], day.isoformat()) in overrides):
                continue
            start = dt.datetime.combine(day, dt.time.fromisoformat(block['startTime']), MOSCOW)
            end = dt.datetime.combine(day, dt.time.fromisoformat(block['endTime']), MOSCOW)
            while start + STEP <= end:
                finish = start + STEP
                if start > instant and not any(start < right and left < finish for left, right in intervals):
                    identifier = _id('slot_recurring_', [key, iso(start)])
                    cursor = db.execute('''INSERT INTO cp_recruiter_slots
                      (id,start,end,recruiter_name,candidate_id) VALUES (?,?,?,?,NULL)
                      ON CONFLICT(id) DO NOTHING''',
                      (identifier, iso(start), iso(finish), block['recruiterName']))
                    if cursor.rowcount:
                        db.execute('INSERT INTO cp_generated_slots (slot_id,block_id) VALUES (?,?)',
                                   (identifier, block['id']))
                        if owner:
                            db.execute('INSERT INTO cp_recruiter_slot_owners VALUES (?,?)', (identifier, owner[0]))
                        intervals.append((start, finish))
                        created += 1
                start = finish
    for (owner, day_text), ranges in overrides.items():
        day = dt.date.fromisoformat(day_text)
        if day < today:
            continue
        name = db.execute('SELECT name FROM rc_accounts WHERE id=?', (owner,)).fetchone()[0]
        key = 'account:' + owner
        intervals = occupied.setdefault(key, [])
        for item in ranges:
            start = dt.datetime.combine(day, dt.time.fromisoformat(item['startTime']), MOSCOW)
            end = dt.datetime.combine(day, dt.time.fromisoformat(item['endTime']), MOSCOW)
            while start + STEP <= end:
                finish = start + STEP
                if start > instant and not any(start < right and left < finish for left, right in intervals):
                    identifier = _id('slot_date_', [key, iso(start)])
                    cursor = db.execute('''INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)
                      ON CONFLICT(id) DO NOTHING''', (identifier, iso(start), iso(finish), name))
                    if cursor.rowcount:
                        db.execute('INSERT INTO cp_date_generated_slots VALUES (?)', (identifier,))
                        db.execute('INSERT INTO cp_recruiter_slot_owners VALUES (?,?)', (identifier, owner))
                        intervals.append((start, finish))
                        created += 1
                start = finish
    return created
