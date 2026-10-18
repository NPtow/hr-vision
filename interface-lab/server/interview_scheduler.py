"""Anonymous interview windows and capacity-weighted assignment.

Public IDs identify a time interval, never a recruiter. The booking caller must
hold a SQLite BEGIN IMMEDIATE transaction through selection, reservation and
outbox insertion. Availability rules remain owned by recruiter_availability.
"""
import datetime as dt
import hashlib
import json
from fractions import Fraction

import recruiter_availability as availability
from domain import iso, now, require, timestamp


def window_id(start, end):
    value = iso(timestamp(start)) + '/' + iso(timestamp(end))
    return 'window_' + hashlib.sha256(value.encode()).hexdigest()[:32]


def same_window(db, requested, physical_id):
    actual = db.execute('SELECT start,end FROM cp_recruiter_slots WHERE id=?', (physical_id,)).fetchone()
    if not actual:
        return False
    if requested == window_id(*actual):
        return True
    # Old clients may still send a physical slot ID. It specifies a time only;
    # public callers cannot use it to force a particular recruiter.
    legacy = db.execute('SELECT start,end FROM cp_recruiter_slots WHERE id=?', (requested,)).fetchone()
    return bool(legacy and window_id(*legacy) == window_id(*actual))


def _schedule(db):
    availability.materialize(db)
    allowed = availability.open_slot_ids(db)
    rows = db.execute('''SELECT s.id,s.start,s.end,s.candidate_id,o.recruiter_id
      FROM cp_recruiter_slots s JOIN cp_recruiter_slot_owners o ON o.slot_id=s.id
      JOIN rc_accounts a ON a.id=o.recruiter_id WHERE a.active=1''').fetchall()
    return [row for row in rows if (row[0] in allowed or row[3])
            and timestamp(row[2]) - timestamp(row[1]) == availability.STEP], allowed


def public_slots(db):
    rows, allowed = _schedule(db)
    instant = now()
    windows = {}
    for sid, start, end, candidate, owner in rows:
        if sid in allowed and not candidate and timestamp(start) > instant:
            key = window_id(start, end)
            windows[key] = {'id': key, 'start': iso(timestamp(start)), 'end': iso(timestamp(end))}
    return sorted(windows.values(), key=lambda slot: (slot['start'], slot['end']))


def _weekly_counts(db, rows, monday):
    """Count the whole calendar week from rules, overrides and commitments.

    Materialized rows are only a rolling booking horizon. Counting them alone
    would understate capacity at either edge of a week (or after first setup).
    No extra future slots are published as a side effect of this calculation.
    """
    end_day = monday + dt.timedelta(days=7)
    generated = {row[0] for row in db.execute('''SELECT slot_id FROM cp_generated_slots
      UNION SELECT slot_id FROM cp_date_generated_slots''')}
    capacity, load = {}, {}
    occupied = {}

    def include(owner, begin, finish):
        intervals = occupied.setdefault(owner, [])
        if not any(begin < right and left < finish for left, right in intervals):
            intervals.append((begin, finish))
            capacity[owner] = capacity.get(owner, 0) + 1

    for sid, start, end, candidate, owner in sorted(rows, key=lambda row: (not bool(row[3]), row[1], row[0])):
        begin, finish = timestamp(start), timestamp(end)
        if monday <= begin.astimezone(availability.MOSCOW).date() < end_day:
            if candidate:
                load[owner] = load.get(owner, 0) + 1
            if candidate or sid not in generated:
                include(owner, begin, finish)
    overrides = {(owner, day): json.loads(ranges) for owner, day, ranges in db.execute(
        'SELECT recruiter_id,date,intervals FROM cp_availability_dates WHERE date>=? AND date<?',
        (monday.isoformat(), end_day.isoformat()))}
    rule_owners = dict(db.execute('SELECT block_id,recruiter_id FROM cp_recruiter_block_owners'))
    owners = {row[4] for row in rows}
    rules = availability.blocks(db)
    for offset in range(7):
        day = monday + dt.timedelta(days=offset)
        for owner in owners:
            ranges = overrides.get((owner, day.isoformat()))
            if ranges is None:
                ranges = [rule for rule in rules if rule_owners.get(rule['id']) == owner
                          and rule['startDate'] <= day.isoformat() and day.weekday() in rule['weekdays']]
            for item in sorted(ranges, key=lambda item: item['startTime']):
                start = dt.datetime.combine(day, dt.time.fromisoformat(item['startTime']), availability.MOSCOW)
                end = dt.datetime.combine(day, dt.time.fromisoformat(item['endTime']), availability.MOSCOW)
                while start + availability.STEP <= end:
                    include(owner, start, start + availability.STEP)
                    start += availability.STEP
    return capacity, load


def select_slot(db, requested):
    rows, allowed = _schedule(db)
    legacy = db.execute('SELECT start,end FROM cp_recruiter_slots WHERE id=?', (requested,)).fetchone()
    key = window_id(*legacy) if legacy else requested
    instant = now()
    eligible = [row for row in rows if row[0] in allowed and not row[3]
                and timestamp(row[1]) > instant and window_id(row[1], row[2]) == key]
    require(eligible, 'Это время уже недоступно. Выберите другое.', 409)
    day = timestamp(eligible[0][1]).astimezone(availability.MOSCOW).date()
    monday = day - dt.timedelta(days=day.weekday())
    capacity, load = _weekly_counts(db, rows, monday)
    # rowid is monotonic for this append-only table, including assignments made
    # in the same second. Never-assigned recruiters win an otherwise equal tie.
    recent = dict(db.execute('''SELECT o.recruiter_id,MAX(i.rowid)
      FROM cp_interviews i JOIN cp_recruiter_slot_owners o ON o.slot_id=i.slot_id
      GROUP BY o.recruiter_id'''))
    def rank(row):
        owner = row[4]
        return (Fraction(load.get(owner, 0), max(1, capacity.get(owner, 0))),
                recent.get(owner, 0), owner, row[0])
    return min(eligible, key=rank)[0]
