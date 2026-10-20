"""Candidate-owned weekly rules and day overrides; never reschedule appointments."""
import datetime as dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from domain import Problem, iso, now, require, timestamp

HORIZON_DAYS = 28


def zone(value):
    require(isinstance(value, str) and 0 < len(value) <= 100, 'Выберите часовой пояс.', 400)
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise Problem('Выберите существующий часовой пояс.', 400)


def day(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'Проверьте дату.', 400)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise Problem('Проверьте дату.', 400)
    require(parsed < dt.date.max, 'Проверьте дату.', 400)
    return parsed


def times(value):
    require(isinstance(value, dict) and set(value) == {'startTime', 'endTime'}, 'Проверьте интервал.', 400)
    for key in ('startTime', 'endTime'):
        require(isinstance(value[key], str) and (re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value[key]) or (key == 'endTime' and value[key] == '24:00')), 'Проверьте время.', 400)
    start, end = [int(value[key][:2]) * 60 + int(value[key][3:]) for key in ('startTime', 'endTime')]
    minutes = end - start
    require(30 <= minutes <= 720, 'Интервал должен длиться от 30 минут до 12 часов в пределах дня.', 400)
    return dict(value)


def validate(value):
    require(isinstance(value, dict) and set(value) == {'weekly', 'dates'}, 'Проверьте расписание.', 400)
    require(isinstance(value['weekly'], list) and len(value['weekly']) <= 20, 'Можно сохранить до 20 недельных правил.', 400)
    require(isinstance(value['dates'], list) and len(value['dates']) <= 370, 'Слишком много отдельных дат.', 400)
    result, ids, dates = {'weekly': [], 'dates': []}, set(), set()
    for rule in value['weekly']:
        require(isinstance(rule, dict) and set(rule) == {'id', 'startDate', 'weekdays', 'startTime', 'endTime', 'timezone'}, 'Проверьте недельное правило.', 400)
        require(isinstance(rule['id'], str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', rule['id']) and rule['id'] not in ids, 'Правило повторяется.', 400)
        ids.add(rule['id'])
        day(rule['startDate']); zone(rule['timezone'])
        weekdays = rule['weekdays']
        require(isinstance(weekdays, list) and 1 <= len(weekdays) <= 7 and all(type(n) is int and 0 <= n <= 6 for n in weekdays) and len(set(weekdays)) == len(weekdays), 'Выберите дни недели.', 400)
        times({key: rule[key] for key in ('startTime', 'endTime')})
        result['weekly'].append({**rule, 'weekdays': sorted(weekdays)})
    for override in value['dates']:
        require(isinstance(override, dict) and set(override) == {'date', 'timezone', 'intervals'}, 'Проверьте расписание на дату.', 400)
        day(override['date']); zone(override['timezone'])
        key = (override['date'], override['timezone'])
        require(key not in dates, 'Дата повторяется.', 400)
        dates.add(key)
        require(isinstance(override['intervals'], list) and len(override['intervals']) <= 24, 'Слишком много интервалов.', 400)
        intervals = sorted([times(item) for item in override['intervals']], key=lambda item: item['startTime'])
        require(all(a['endTime'] <= b['startTime'] for a, b in zip(intervals, intervals[1:])), 'Интервалы не должны пересекаться.', 400)
        # Refuse nonexistent/repeated wall times on an explicitly edited date.
        for interval in intervals:
            require(window(override['date'], interval, override['timezone']) is not None, 'В это время переводят часы. Выберите другое время.', 400)
        result['dates'].append({**override, 'intervals': intervals})
    return result


def instant(date, time, timezone):
    date = day(date)
    if time == '24:00':
        date, time = date + dt.timedelta(days=1), '00:00'
    naive = dt.datetime.combine(date, dt.time.fromisoformat(time))
    tz = zone(timezone)
    matches = set()
    for fold in (0, 1):
        utc = naive.replace(tzinfo=tz, fold=fold).astimezone(dt.timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) == naive:
            matches.add(utc)
    return next(iter(matches)) if len(matches) == 1 else None


def window(date, interval, timezone):
    start = instant(date, interval['startTime'], timezone)
    end = instant(date, interval['endTime'], timezone)
    if start is None or end is None or not 1800 <= (end-start).total_seconds() <= 43200:
        return None
    return {'start': iso(start), 'end': iso(end)}


def project(profile, tick=None):
    """Expand a rolling horizon for the existing employer/candidate availability API."""
    schedule = profile.get('availabilitySchedule')
    if not schedule:
        return profile
    tick = tick or now()
    manual = profile.get('availabilityManual', profile.get('availability', []))
    windows = list(manual)
    for rule in schedule['weekly']:
        today = tick.astimezone(zone(rule['timezone'])).date()
        for offset in range(HORIZON_DAYS):
            current = today + dt.timedelta(days=offset)
            if current.isoformat() >= rule['startDate'] and current.weekday() in rule['weekdays']:
                item = window(current.isoformat(), rule, rule['timezone'])
                if item and timestamp(item['end']) > tick:
                    windows.append(item)
    # A date-specific override replaces the intersecting part of every rule,
    # including a rule authored in another zone and windows crossing midnight.
    for override in schedule['dates']:
        tz, date = zone(override['timezone']), day(override['date'])
        start = dt.datetime.combine(date, dt.time.min, tz).astimezone(dt.timezone.utc)
        end = dt.datetime.combine(date+dt.timedelta(days=1), dt.time.min, tz).astimezone(dt.timezone.utc)
        kept = []
        for item in windows:
            a, b = timestamp(item['start']), timestamp(item['end'])
            if b <= start or a >= end:
                kept.append(item)
            else:
                if a < start: kept.append({'start': iso(a), 'end': iso(start)})
                if b > end: kept.append({'start': iso(end), 'end': iso(b)})
        windows = kept + [item for interval in override['intervals'] if (item := window(override['date'], interval, override['timezone']))]
    merged = []
    for item in sorted(windows, key=lambda item: timestamp(item['start'])):
        if merged and timestamp(item['start']) <= timestamp(merged[-1]['end']):
            if timestamp(item['end']) > timestamp(merged[-1]['end']): merged[-1]['end'] = item['end']
        else:
            merged.append(dict(item))
    return {**profile, 'availabilityManual': manual, 'availability': merged}
