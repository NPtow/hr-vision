"""Recurring recruiter availability, expanded into the existing booking slots.

The caller owns the SQLite transaction and its scheduling lock. Rules are immutable;
materialization only appends free, future slots and never rewrites booked history.
"""
import datetime as dt
import hashlib
import json
import re
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


def _block(row):
    return {'id': row[0], 'recruiterName': row[1], 'startDate': row[3],
            'weekdays': json.loads(row[4]), 'startTime': row[5], 'endTime': row[6],
            'timezone': row[7], 'createdAt': row[8]}


def blocks(db):
    return [_block(row) for row in db.execute('''SELECT id,recruiter_name,recruiter_key,
      start_date,weekdays,start_time,end_time,timezone,created_at
      FROM cp_availability_blocks ORDER BY recruiter_key,start_date,start_time,id''')]


def _time(value):
    require(isinstance(value, str) and re.fullmatch(r'(?:[01][0-9]|2[0-3]):(?:00|30)', value),
            'Время должно быть в формате ЧЧ:ММ с шагом 30 минут.', 400)
    return dt.time.fromisoformat(value)


def _id(prefix, values):
    encoded = json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()
    return prefix + hashlib.sha256(encoded).hexdigest()[:32]


def add_block(db, body):
    require(isinstance(body, dict) and set(body) == FIELDS,
            'Укажите рекрутера, дату начала, дни недели, время и часовой пояс.', 400)
    recruiter = ' '.join(text(body['recruiterName'], 140).split())
    key = recruiter_key(recruiter)
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
    identifier = _id('availability_', [key, day_text, weekdays,
                                       body['startTime'], body['endTime'], TIMEZONE])
    existing = db.execute('''SELECT id,recruiter_name,recruiter_key,start_date,weekdays,
      start_time,end_time,timezone,created_at FROM cp_availability_blocks
      WHERE recruiter_key=?''', (key,)).fetchall()
    for row in existing:
        if row[0] == identifier:
            return _block(row)
        # Rules have no end date. Shared weekdays will therefore eventually coincide,
        # even when their first dates or current generated horizons differ.
        require(not (set(weekdays).intersection(json.loads(row[4]))
                     and body['startTime'] < row[6] and row[5] < body['endTime']),
                'У рекрутера уже есть расписание на эти дни и время.', 409)
    require(len(existing) < MAX_BLOCKS_PER_RECRUITER,
            'У рекрутера может быть не больше 20 интервалов расписания.', 409)
    row = (identifier, recruiter, key, day_text, json.dumps(weekdays, separators=(',', ':')),
           body['startTime'], body['endTime'], TIMEZONE, iso(instant))
    db.execute('INSERT INTO cp_availability_blocks VALUES (?,?,?,?,?,?,?,?,?)', row)
    return _block(row)


def materialize(db):
    """Append 28 calendar days from each rule's effective start; return insert count."""
    instant = now()
    today = instant.astimezone(MOSCOW).date()
    occupied = {}
    for start, end, recruiter in db.execute('SELECT start,end,recruiter_name FROM cp_recruiter_slots'):
        finish = timestamp(end)
        if finish > instant:
            occupied.setdefault(recruiter_key(recruiter), []).append((timestamp(start), finish))
    created = 0
    for block in blocks(db):
        first_day = max(today, dt.date.fromisoformat(block['startDate']))
        key = recruiter_key(block['recruiterName'])
        intervals = occupied.setdefault(key, [])
        for offset in range(HORIZON_DAYS):
            day = first_day + dt.timedelta(days=offset)
            if day.weekday() not in block['weekdays']:
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
                        intervals.append((start, finish))
                        created += 1
                start = finish
    return created
