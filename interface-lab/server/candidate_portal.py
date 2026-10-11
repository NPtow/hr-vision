"""Personal candidate accounts, independent from the legacy team test personas.

SQLite is authoritative. A person, their recruiter interview and their applications
are separate records. Candidate approval of a vacancy never confirms a meeting.
"""
import datetime as dt
from contextlib import contextmanager
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

from domain import Problem, require, text, timestamp, iso, now

COOKIE = '__Host-hr_candidate'
SESSION_SECONDS = 30 * 86400
PROFILE_FIELDS = {'name', 'city', 'desiredRole', 'about', 'telegram', 'geographies',
                  'availability', 'consentReuse', 'autoApprove', 'desiredSalary', 'workMode'}


class ClosingConnection(sqlite3.Connection):
    """Keep SQLite's transaction context semantics and release its file handle."""
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def uid(prefix):
    return prefix + '_' + secrets.token_hex(12)


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def normalize_city(value):
    return ' '.join(value.casefold().replace('ё', 'е').split())


def password_hash(password, salt=None):
    require(isinstance(password, str) and 10 <= len(password) <= 128,
            'Пароль должен содержать от 10 до 128 символов.', 400)
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$' + salt.hex() + '$' + digest.hex()


def password_matches(password, stored):
    # Apply the same bounded work to unknown identities. Never fall back to a test actor.
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        return False
    try:
        kind, n, r, p, salt, digest = stored.split('$')
        if (kind, n, r, p) != ('scrypt', '16384', '8', '1'):
            return False
        expected = password_hash(password, bytes.fromhex(salt)).split('$')[-1]
        return hmac.compare_digest(expected, digest)
    except (ValueError, TypeError):
        return False


def email_value(value):
    value = text(value, 254).casefold()
    require(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value) is not None,
            'Укажите корректную почту.', 400)
    return value


def interval(start, end=None, *, future=False):
    begin = timestamp(start)
    finish = timestamp(end) if end is not None else begin + dt.timedelta(minutes=30)
    require(1800 <= (finish - begin).total_seconds() <= 12 * 3600,
            'Интервал должен быть от 30 минут до 12 часов.', 400)
    if future:
        require(begin > now(), 'Выберите будущее время.', 400)
    require(begin < now() + dt.timedelta(days=370), 'Выберите время в пределах года.', 400)
    return {'start': iso(begin), 'end': iso(finish)}


def overlaps(a, b):
    return timestamp(a['start']) < timestamp(b['end']) and timestamp(b['start']) < timestamp(a['end'])


def profile_value(body, previous=None):
    old = previous or {}
    # Ignore no unknown fields: a client must never be able to overwrite a dossier,
    # actor, email, interview status or server-assigned person/application identity.
    unexpected = set(body) - PROFILE_FIELDS
    require(not unexpected, 'В запросе есть недоступные поля профиля.', 400)
    result = {}
    for key, limit, mandatory in [('name', 140, True), ('city', 120, True),
                                  ('desiredRole', 200, True), ('about', 1500, False),
                                  ('telegram', 100, False), ('desiredSalary', 120, False)]:
        result[key] = text(body.get(key, old.get(key, '')), limit, required=mandatory)
    result['workMode'] = body.get('workMode', old.get('workMode', 'any'))
    require(result['workMode'] in ('office', 'hybrid', 'remote', 'any'), 'Выберите формат работы.', 400)
    geography = body.get('geographies', old.get('geographies', []))
    require(isinstance(geography, list) and 1 <= len(geography) <= 20,
            'Укажите подходящие города.', 400)
    result['geographies'] = list(dict.fromkeys(text(city, 120) for city in geography))
    availability = body.get('availability', old.get('availability', []))
    require(isinstance(availability, list) and 1 <= len(availability) <= 40,
            'Укажите от 1 до 40 удобных интервалов.', 400)
    result['availability'] = []
    for item in availability:
        require(isinstance(item, dict) and set(item) == {'start', 'end'}, 'Проверьте удобное время.', 400)
        normalized = interval(item['start'], item['end'])
        if normalized not in result['availability']:
            result['availability'].append(normalized)
    result['availability'].sort(key=lambda item: item['start'])
    for key in ('consentReuse', 'autoApprove'):
        result[key] = body.get(key, old.get(key, False))
        require(isinstance(result[key], bool), 'Подтвердите выбранный режим.', 400)
    require(not result['autoApprove'] or result['consentReuse'],
            'Для автосогласования нужно согласие на использование интервью.', 400)
    return result


class CandidatePortal:
    def __init__(self, connect, lock, media, origin, notifications=None):
        self.connect, self.lock, self.media, self.origin = connect, lock, media, origin
        self.notifications = notifications
        self._media_guard = threading.Lock()
        self._media_locks = {}
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))
        with self.connect() as db:
            self.init(db)

    @contextmanager
    def media_operation(self, meeting_id):
        # Serialise Daily room renewal and closing per meeting, not all API state.
        # Reference counts include waiters, so an entry is never replaced mid-wait.
        with self._media_guard:
            item = self._media_locks.setdefault(meeting_id, [threading.Lock(), 0])
            item[1] += 1
        try:
            with item[0]:
                yield
        finally:
            with self._media_guard:
                item[1] -= 1
                if not item[1]:
                    del self._media_locks[meeting_id]

    def init(self, db):
        db.executescript('''
        CREATE TABLE IF NOT EXISTS cp_candidates (
          id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
          profile TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cp_sessions (
          token_hash TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
          expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS cp_rate_limits (
          key TEXT PRIMARY KEY, count INTEGER NOT NULL, reset_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS cp_consent_log (
          id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
          created_at TEXT NOT NULL, reuse INTEGER NOT NULL, auto_approve INTEGER NOT NULL,
          version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cp_recruiter_slots (
          id TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL,
          recruiter_name TEXT NOT NULL, candidate_id TEXT UNIQUE REFERENCES cp_candidates(id));
        CREATE TABLE IF NOT EXISTS cp_interviews (
          id TEXT PRIMARY KEY, candidate_id TEXT UNIQUE NOT NULL REFERENCES cp_candidates(id),
          slot_id TEXT UNIQUE NOT NULL REFERENCES cp_recruiter_slots(id),
          status TEXT NOT NULL, room TEXT, created_at TEXT NOT NULL, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS cp_vacancies (
          id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cp_applications (
          id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
          vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id), status TEXT NOT NULL,
          approved_automatically INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
          decided_at TEXT, UNIQUE(candidate_id, vacancy_id));
        CREATE TABLE IF NOT EXISTS cp_meetings (
          id TEXT PRIMARY KEY, application_id TEXT UNIQUE NOT NULL REFERENCES cp_applications(id),
          start TEXT NOT NULL, end TEXT NOT NULL, status TEXT NOT NULL, room TEXT,
          confirmed_at TEXT, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS cp_internal_profiles (
          candidate_id TEXT PRIMARY KEY REFERENCES cp_candidates(id), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cp_media_consents (
          meeting_id TEXT NOT NULL, actor_id TEXT NOT NULL, consented_at TEXT NOT NULL,
          version TEXT NOT NULL, PRIMARY KEY(meeting_id,actor_id));
        CREATE INDEX IF NOT EXISTS cp_application_candidate ON cp_applications(candidate_id);
        ''')
        if self.notifications:
            self.notifications.init(db)

    @staticmethod
    def handles(path):
        return path.startswith('/api/hr/candidate/') or path.startswith('/api/hr/recruiter/')

    def event(self, db, cid, event_type, entity_id):
        if self.notifications:
            self.notifications.enqueue(db, cid, event_type + ':' + entity_id, event_type)

    def channels(self, db=None, cid=None):
        if not self.notifications:
            return {'email': False, 'telegram': False}
        if cid is not None:
            return self.notifications.public_status(db, cid)
        return self.notifications.configuration_public()

    def session_candidate(self, db, headers, required=True):
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get('Cookie', ''))
            token = cookie[COOKIE].value if COOKIE in cookie else ''
        except Exception:
            token = ''
        row = None
        if re.fullmatch(r'[A-Za-z0-9_-]{40,100}', token):
            row = db.execute('SELECT candidate_id FROM cp_sessions WHERE token_hash=? AND expires>?',
                             (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        if required:
            require(row is not None, 'Войдите в свой кабинет.', 401)
        return row[0] if row else None

    def session_cookie(self, db, cid):
        token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM cp_sessions WHERE expires<=?', (int(time.time()),))
        db.execute('INSERT INTO cp_sessions VALUES (?,?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), cid, int(time.time()) + SESSION_SECONDS))
        # __Host cookies require Path=/ and no Domain. It remains inaccessible to JS.
        return f'{COOKIE}={token}; Path=/; Max-Age={SESSION_SECONDS}; Secure; HttpOnly; SameSite=Lax'

    def clear_session(self, db, headers):
        cookie = SimpleCookie()
        cookie.load(headers.get('Cookie', ''))
        if COOKIE in cookie:
            digest = hashlib.sha256(cookie[COOKIE].value.encode()).hexdigest()
            db.execute('DELETE FROM cp_sessions WHERE token_hash=?', (digest,))
        return f'{COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax'

    def rate_limit(self, key, limit, seconds):
        # Commit even a failed password attempt; rolling back auth must not erase it.
        digest = hashlib.sha256(key.encode()).hexdigest()
        tick = int(time.time())
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM cp_rate_limits WHERE reset_at<=?', (tick,))
            row = db.execute('SELECT count,reset_at FROM cp_rate_limits WHERE key=?', (digest,)).fetchone()
            require(not row or row[0] < limit, 'Слишком много попыток. Попробуйте позже.', 429)
            if row:
                db.execute('UPDATE cp_rate_limits SET count=count+1 WHERE key=?', (digest,))
            else:
                db.execute('INSERT INTO cp_rate_limits VALUES (?,?,?)', (digest, 1, tick + seconds))

    def person(self, db, cid):
        row = db.execute('SELECT id,email,profile,created_at FROM cp_candidates WHERE id=?', (cid,)).fetchone()
        require(row is not None, 'Кандидат не найден.', 404)
        return {'id': row[0], 'email': row[1], **json.loads(row[2]), 'createdAt': row[3]}

    def interview(self, db, cid):
        row = db.execute('''SELECT i.id,i.status,i.room,s.start,s.end,s.recruiter_name
          FROM cp_interviews i JOIN cp_recruiter_slots s ON i.slot_id=s.id WHERE i.candidate_id=?''', (cid,)).fetchone()
        if not row:
            return None
        return {'id': row[0], 'status': row[1], 'room': json.loads(row[2]) if row[2] else None,
                'start': row[3], 'end': row[4], 'recruiterName': row[5]}

    def meeting(self, db, app_id):
        row = db.execute('SELECT id,start,end,status,room FROM cp_meetings WHERE application_id=?', (app_id,)).fetchone()
        return ({'id': row[0], 'start': row[1], 'end': row[2], 'status': row[3],
                 'room': json.loads(row[4]) if row[4] else None} if row else None)

    def application(self, db, app_id, cid=None):
        row = db.execute('''SELECT a.id,a.candidate_id,a.status,a.approved_automatically,v.body
          FROM cp_applications a JOIN cp_vacancies v ON a.vacancy_id=v.id WHERE a.id=?''', (app_id,)).fetchone()
        require(row is not None and (cid is None or row[1] == cid), 'Вакансия не найдена.', 404)
        return {'id': row[0], 'candidateId': row[1], 'status': row[2],
                'approvedAutomatically': bool(row[3]), 'vacancy': json.loads(row[4]),
                'meeting': self.meeting(db, app_id)}

    def candidate_state(self, db, cid, internal=False):
        result = self.person(db, cid)
        result['interview'] = self.interview(db, cid)
        if result['interview']:
            result['interview'].pop('room', None)
        result['applications'] = []
        for row in db.execute('SELECT id FROM cp_applications WHERE candidate_id=? ORDER BY created_at DESC,rowid DESC', (cid,)):
            application = self.application(db, row[0], cid)
            application.pop('candidateId', None)
            if application['meeting']:
                application['meeting'].pop('room', None)
            result['applications'].append(application)
        result['videoConfigured'] = self.media.configured
        result['channels'] = self.channels(db, cid)
        if internal:
            profile = db.execute('SELECT body FROM cp_internal_profiles WHERE candidate_id=?', (cid,)).fetchone()
            result['internalProfile'] = json.loads(profile[0]) if profile else None
        return result

    def slots(self, db, internal=False):
        rows = db.execute('SELECT id,start,end,recruiter_name,candidate_id FROM cp_recruiter_slots ORDER BY start')
        return [{'id': row[0], 'start': row[1], 'end': row[2], 'recruiterName': row[3],
                 **({'booked': bool(row[4]), 'candidateId': row[4]} if internal else {})}
                for row in rows if internal or (not row[4] and timestamp(row[1]) > now())]

    def recruiter_state(self, db):
        return {'candidates': [self.candidate_state(db, row[0], internal=True)
                               for row in db.execute('SELECT id FROM cp_candidates ORDER BY created_at DESC,rowid DESC')],
                'slots': self.slots(db, internal=True),
                'vacancies': [json.loads(row[0]) for row in db.execute('SELECT body FROM cp_vacancies ORDER BY created_at DESC,rowid DESC')],
                'videoConfigured': self.media.configured}

    @staticmethod
    def auto_matches(person, vacancy):
        if not person['autoApprove'] or not person['consentReuse']:
            return False
        if normalize_city(person['desiredRole']) != normalize_city(vacancy['title']):
            return False
        # The first vacancy bridge has no structured compensation/hybrid schedule.
        # Unknown requirements therefore always need a personal decision.
        if person['desiredSalary'] or person['workMode'] == 'hybrid':
            return False
        if vacancy['remote'] and person['workMode'] != 'remote':
            return False
        if not vacancy['remote'] and person['workMode'] == 'remote':
            return False
        cities = {normalize_city(city) for city in person['geographies']}
        # No guessed geography or inferred willingness to relocate.
        return normalize_city(vacancy['city']) in cities

    def register(self, db, body):
        require(set(body) <= PROFILE_FIELDS | {'email', 'password'}, 'Проверьте поля регистрации.', 400)
        email = email_value(body.get('email'))
        require(db.execute('SELECT 1 FROM cp_candidates WHERE email=?', (email,)).fetchone() is None,
                'Не удалось создать аккаунт. Если вы уже регистрировались, войдите.', 409)
        profile = profile_value({key: value for key, value in body.items() if key in PROFILE_FIELDS})
        require(profile['consentReuse'], 'Подтвердите использование интервью для подбора вакансий.', 400)
        cid, created = uid('person'), iso(now())
        db.execute('INSERT INTO cp_candidates VALUES (?,?,?,?,?,?)',
                   (cid, email, password_hash(body.get('password')), dump(profile), created, created))
        db.execute('INSERT INTO cp_consent_log VALUES (?,?,?,?,?,?)',
                   (uid('consent'), cid, created, 1, int(profile['autoApprove']), 'candidate-onboarding-v1'))
        if self.notifications:
            self.notifications.set_email(db, cid, email, verified=False)
        self.event(db, cid, 'candidate.registered', cid)
        return cid

    def profile(self, db, cid, body):
        previous = self.person(db, cid)
        profile = profile_value(body, previous)
        changed = any(profile[key] != previous[key] for key in ('consentReuse', 'autoApprove'))
        db.execute('UPDATE cp_candidates SET profile=?,updated_at=? WHERE id=?', (dump(profile), iso(now()), cid))
        if changed:
            db.execute('INSERT INTO cp_consent_log VALUES (?,?,?,?,?,?)',
                       (uid('consent'), cid, iso(now()), int(profile['consentReuse']),
                        int(profile['autoApprove']), 'candidate-preferences-v1'))

    def add_slot(self, db, body):
        times = interval(body.get('start'), future=True)
        recruiter = text(body.get('recruiterName'), 140)
        for row in db.execute('SELECT start,end,recruiter_name FROM cp_recruiter_slots'):
            require(normalize_city(row[2]) != normalize_city(recruiter) or not overlaps(times, {'start': row[0], 'end': row[1]}),
                    'У рекрутера уже есть слот на это время.', 409)
        db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)',
                   (uid('slot'), times['start'], times['end'], recruiter))

    def book(self, db, cid, body):
        slot = db.execute('SELECT id,start,end,candidate_id FROM cp_recruiter_slots WHERE id=?', (body.get('slotId'),)).fetchone()
        require(slot is not None, 'Слот не найден.', 404)
        existing = db.execute('SELECT slot_id FROM cp_interviews WHERE candidate_id=?', (cid,)).fetchone()
        if existing:
            require(existing[0] == slot[0], 'Вы уже записаны. Для переноса свяжитесь с командой.', 409)
            return
        require(slot[3] is None and timestamp(slot[1]) > now(), 'Это время уже недоступно. Выберите другое.', 409)
        require(self.person(db, cid)['consentReuse'], 'Подтвердите согласие в профиле.', 400)
        self.ensure_candidate_free(db, cid, {'start': slot[1], 'end': slot[2]})
        iid = uid('interview')
        db.execute('UPDATE cp_recruiter_slots SET candidate_id=? WHERE id=? AND candidate_id IS NULL', (cid, slot[0]))
        db.execute('INSERT INTO cp_interviews VALUES (?,?,?,\'booked\',NULL,?,NULL)', (iid, cid, slot[0], iso(now())))
        self.event(db, cid, 'interview.booked', iid)

    def ensure_candidate_free(self, db, cid, times):
        interview = self.interview(db, cid)
        require(not interview or interview['status'] == 'completed' or not overlaps(times, interview),
                'На это время у кандидата уже назначено интервью.', 409)
        for row in db.execute('''SELECT m.start,m.end FROM cp_meetings m JOIN cp_applications a
          ON m.application_id=a.id WHERE a.candidate_id=? AND m.status IN ('pending','confirmed','live')''', (cid,)):
            require(not overlaps(times, {'start': row[0], 'end': row[1]}), 'Это время кандидата уже занято.', 409)

    def add_vacancy(self, db, body):
        vacancy = {key: text(body.get(key, ''), limit, required=key != 'description')
                   for key, limit in [('title', 200), ('company', 200), ('city', 120), ('description', 10000)]}
        require(isinstance(body.get('remote'), bool), 'Укажите формат вакансии.', 400)
        vacancy.update(id=uid('vacancy'), remote=body['remote'])
        db.execute('INSERT INTO cp_vacancies VALUES (?,?,?)', (vacancy['id'], dump(vacancy), iso(now())))

    def propose_application(self, db, body):
        cid = body.get('candidateId')
        person = self.person(db, cid)
        interview = self.interview(db, cid)
        require(interview is not None and interview['status'] == 'completed', 'Сначала завершите первичное интервью.', 409)
        require(person['consentReuse'], 'Кандидат не разрешил использовать интервью для новых вакансий.', 409)
        row = db.execute('SELECT body FROM cp_vacancies WHERE id=?', (body.get('vacancyId'),)).fetchone()
        require(row is not None, 'Вакансия не найдена.', 404)
        vacancy = json.loads(row[0])
        existing = db.execute('SELECT id FROM cp_applications WHERE candidate_id=? AND vacancy_id=?', (cid, vacancy['id'])).fetchone()
        if existing:
            return
        auto = self.auto_matches(person, vacancy)
        aid = uid('application')
        db.execute('INSERT INTO cp_applications VALUES (?,?,?,?,?,?,?)',
                   (aid, cid, vacancy['id'], 'accepted' if auto else 'pending', int(auto), iso(now()), iso(now()) if auto else None))
        self.event(db, cid, 'application.proposed', aid)

    def decide_application(self, db, cid, body):
        application = self.application(db, body.get('applicationId'), cid)
        decision = body.get('decision')
        require(decision in ('accepted', 'declined'), 'Выберите решение.', 400)
        if application['status'] == decision:
            return
        require(application['status'] == 'pending', 'Решение уже сохранено.', 409)
        require(decision != 'accepted' or self.person(db, cid)['consentReuse'], 'Подтвердите использование интервью в профиле.', 409)
        db.execute('UPDATE cp_applications SET status=?,approved_automatically=0,decided_at=? WHERE id=?',
                   (decision, iso(now()), application['id']))

    def propose_meeting(self, db, body):
        application = self.application(db, body.get('applicationId'))
        require(application['status'] == 'accepted', 'Сначала кандидат должен согласовать вакансию.', 409)
        require(application['meeting'] is None, 'Встреча уже назначена. Для переноса свяжитесь с кандидатом.', 409)
        times = interval(body.get('start'), future=True)
        person = self.person(db, application['candidateId'])
        require(any(timestamp(slot['start']) <= timestamp(times['start']) and timestamp(slot['end']) >= timestamp(times['end'])
                    for slot in person['availability']), 'Выберите время из доступности кандидата.', 409)
        self.ensure_candidate_free(db, person['id'], times)
        for row in db.execute("SELECT start,end FROM cp_meetings WHERE status IN ('pending','confirmed','live')"):
            require(not overlaps(times, {'start': row[0], 'end': row[1]}), 'У нанимающего уже есть встреча на это время.', 409)
        mid = uid('meeting')
        db.execute("INSERT INTO cp_meetings VALUES (?,?,?,?, 'pending',NULL,NULL,NULL)",
                   (mid, application['id'], times['start'], times['end']))
        self.event(db, person['id'], 'meeting.proposed', mid)

    def confirm_meeting(self, db, cid, body):
        application = self.application(db, body.get('applicationId'), cid)
        meeting = application['meeting']
        require(meeting is not None and meeting['id'] == body.get('meetingId'), 'Встреча изменилась. Обновите кабинет.', 409)
        require(application['status'] == 'accepted', 'Сначала согласуйте вакансию.', 409)
        if meeting['status'] == 'confirmed':
            return
        require(meeting['status'] == 'pending' and timestamp(meeting['start']) > now(), 'Это время уже недоступно.', 409)
        db.execute("UPDATE cp_meetings SET status='confirmed',confirmed_at=? WHERE id=?", (iso(now()), meeting['id']))
        self.event(db, cid, 'meeting.confirmed', meeting['id'])

    def join(self, cid, application_id, consent, owner=False):
        require(consent is True, 'Подтвердите запись разговора.', 400)
        # Provider I/O is deliberately outside the shared SQLite transaction.
        with self.lock, self.connect() as db:
            person = self.person(db, cid)
            if application_id:
                application = self.application(db, application_id, cid)
                meeting = application['meeting']
                require(application['status'] == 'accepted' and meeting and meeting['status'] in ('confirmed', 'live'),
                        'Кандидат должен подтвердить время встречи.', 409)
            else:
                meeting = self.interview(db, cid)
                require(meeting and meeting['status'] in ('booked', 'live'), 'Сначала запишитесь к рекрутеру.', 409)
            require(timestamp(meeting['start']) - dt.timedelta(minutes=15) <= now() <= timestamp(meeting['end']) + dt.timedelta(minutes=90),
                    'Комната откроется за 15 минут до встречи.', 409)
            mid = meeting['id']
        with self.media_operation(mid):
            return self._join_room(cid, application_id, owner, mid, person)

    def _join_room(self, cid, application_id, owner, mid, person):
        table = 'cp_meetings' if application_id else 'cp_interviews'
        with self.lock, self.connect() as db:
            current = db.execute(f'SELECT status FROM {table} WHERE id=?', (mid,)).fetchone()
            allowed = ('confirmed', 'live') if application_id else ('booked', 'live')
            require(current and current[0] in allowed, 'Интервью уже завершено.', 409)
        room = self.media.room(mid)
        token = self.media.request('/meeting-tokens', {'properties': {
            'room_name': room['name'], 'user_name': 'Рекрутер HR Vision' if owner else person['name'],
            'user_id': 'recruiter' if owner else cid, 'is_owner': bool(owner),
            'exp': int(time.time()) + 3600, 'eject_at_token_exp': True}})
        require(isinstance(token.get('token'), str) and token['token'], 'Видеосервис не выдал доступ.', 502)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            table = 'cp_meetings' if application_id else 'cp_interviews'
            current = db.execute(f'SELECT status FROM {table} WHERE id=?', (mid,)).fetchone()
            require(current and current[0] != 'completed', 'Интервью уже завершено.', 409)
            # Candidate prejoin alone cannot mark a human interview as live.
            db.execute(f'UPDATE {table} SET room=?,status=? WHERE id=?',
                       (dump(room), 'live' if owner else current[0], mid))
            db.execute('INSERT OR IGNORE INTO cp_media_consents VALUES (?,?,?,?)',
                       (mid, 'recruiter' if owner else cid, iso(now()), 'recording-v1'))
        return {'url': room['url'], 'token': token['token']}

    def complete(self, cid, application_id=None):
        with self.lock, self.connect() as db:
            self.person(db, cid)
            meeting = self.application(db, application_id, cid)['meeting'] if application_id else self.interview(db, cid)
            require(meeting is not None, 'Интервью не найдено.', 404)
            if meeting['status'] == 'completed':
                return
            require(meeting['status'] == 'live' and meeting.get('room'), 'Сначала проведите интервью на платформе.', 409)
            mid, room = meeting['id'], meeting['room']
        with self.media_operation(mid):
            return self._complete_room(cid, application_id, mid)

    def _complete_room(self, cid, application_id, mid):
        with self.lock, self.connect() as db:
            table = 'cp_meetings' if application_id else 'cp_interviews'
            row = db.execute(f'SELECT status,room FROM {table} WHERE id=?', (mid,)).fetchone()
            require(row, 'Интервью не найдено.', 404)
            if row[0] == 'completed':
                return
            require(row[0] == 'live' and row[1], 'Сначала проведите интервью на платформе.', 409)
            room = json.loads(row[1])
        self.media.finish(room)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            table = 'cp_meetings' if application_id else 'cp_interviews'
            changed = db.execute(f"UPDATE {table} SET status='completed',completed_at=? WHERE id=? AND status='live'",
                                 (iso(now()), mid)).rowcount
            if changed and not application_id:
                self.event(db, cid, 'interview.completed', mid)

    def materials(self, cid):
        with self.connect() as db:
            meeting = self.interview(db, cid)
            require(meeting and meeting['status'] == 'completed', 'Материалы доступны после интервью.', 409)
            room = meeting['room']
        require(room, 'Комната интервью не найдена.', 409)
        recordings, transcripts = [], []
        records = self.media.request('/recordings?' + urllib.parse.urlencode({'room_name': room['name'], 'limit': 100}))
        for item in records.get('data', []):
            if item.get('room_name') == room['name'] and item.get('status') == 'finished':
                access = self.media.request('/recordings/' + urllib.parse.quote(item['id'], safe='') + '/access-link')
                url = access.get('download_link')
                require(isinstance(url, str) and url.startswith('https://'), 'Не удалось получить запись.', 502)
                recordings.append({'id': item['id'], 'url': url, 'duration': item.get('duration')})
        result = self.media.request('/transcript?' + urllib.parse.urlencode({'roomId': room['id'], 'limit': 100}))
        for item in result.get('data', []):
            if item.get('roomId') == room['id'] and item.get('status') == 't_finished':
                access = self.media.request('/transcript/' + urllib.parse.quote(item['transcriptId'], safe='') + '/access-link')
                url = access.get('download_link') or access.get('link')
                require(isinstance(url, str) and url.startswith('https://'), 'Не удалось получить расшифровку.', 502)
                with urllib.request.urlopen(url, timeout=18) as response:
                    content = response.read(1024 * 1024 + 1)
                require(len(content) <= 1024 * 1024, 'Расшифровка слишком большая.', 502)
                transcripts.append({'id': item['transcriptId'], 'text': content.decode('utf-8')})
        return {'status': 'ready' if recordings else 'processing', 'recordings': recordings, 'transcripts': transcripts}

    def handle(self, method, raw_path, headers, body, ip, manager_actor):
        path = urllib.parse.urlsplit(raw_path).path
        require(self.handles(path), 'Не найдено.', 404)
        require(method in ('GET', 'POST'), 'Метод не поддерживается.', 405)
        if method == 'POST':
            require(headers.get('Origin') in (None, self.origin) and headers.get('Sec-Fetch-Site') != 'cross-site',
                    'Другой источник запроса.', 403)
            require(headers.get('Content-Type', '').split(';', 1)[0].strip() == 'application/json', 'Нужен JSON.', 415)
            require(isinstance(body, dict), 'Некорректный запрос.', 400)
        auth = path.rsplit('/', 1)[-1]
        is_recruiter = path.startswith('/api/hr/recruiter/')
        if method == 'POST' and path in ('/api/hr/candidate/register', '/api/hr/candidate/login'):
            # app.py resolves this from a validated, explicitly trusted ingress
            # header or the socket peer. Clients cannot invent a forwarding chain.
            self.rate_limit('auth-ip:' + ip, 40, 900)
            email_key = str(body.get('email', '')).strip().casefold()[:254]
            if auth == 'register':
                self.rate_limit('register-email:' + email_key, 5, 3600)
            else:
                self.rate_limit('auth-email:' + email_key, 15, 900)
        elif method == 'POST':
            with self.connect() as db:
                if is_recruiter:
                    require(manager_actor(db) == 'manager', 'Доступно только команде рекрутинга.', 403)
                    rate_identity = 'recruiter'
                else:
                    rate_identity = self.session_candidate(db, headers)
            self.rate_limit('write-account:' + rate_identity, 240, 60)
        response_headers = {}
        provider_action = False

        with self.lock, self.connect() as db:
            if method == 'POST':
                db.execute('BEGIN IMMEDIATE')
            if is_recruiter:
                require(manager_actor(db) == 'manager', 'Доступно только команде рекрутинга.', 403)
                cid = body.get('candidateId') if body else None
            else:
                cid = self.session_candidate(db, headers, required=path not in (
                    '/api/hr/candidate/session', '/api/hr/candidate/register', '/api/hr/candidate/login'))
            if method == 'GET':
                if path == '/api/hr/candidate/session':
                    return {'authenticated': bool(cid), 'candidate': self.candidate_state(db, cid) if cid else None,
                            'videoConfigured': self.media.configured, 'channels': self.channels()}, {}
                if path == '/api/hr/candidate/state':
                    return self.candidate_state(db, cid), {}
                if path == '/api/hr/candidate/slots':
                    return {'slots': self.slots(db)}, {}
                if path == '/api/hr/candidate/notifications':
                    return self.channels(db, cid), {}
                if path == '/api/hr/recruiter/state':
                    return self.recruiter_state(db), {}
                raise Problem('Не найдено.', 404)

            if path == '/api/hr/candidate/register':
                cid = self.register(db, body)
                response_headers['Set-Cookie'] = self.session_cookie(db, cid)
            elif path == '/api/hr/candidate/login':
                email = email_value(body.get('email'))
                row = db.execute('SELECT id,password_hash FROM cp_candidates WHERE email=?', (email,)).fetchone()
                valid = password_matches(body.get('password'), row[1] if row else self.dummy_hash)
                require(row and valid, 'Неверная почта или пароль.', 401)
                cid = row[0]
                response_headers['Set-Cookie'] = self.session_cookie(db, cid)
            elif path == '/api/hr/candidate/logout':
                return {'ok': True}, {'Set-Cookie': self.clear_session(db, headers)}
            elif path == '/api/hr/candidate/profile':
                self.profile(db, cid, body)
            elif path == '/api/hr/candidate/book':
                self.book(db, cid, body)
            elif path == '/api/hr/candidate/application/decision':
                self.decide_application(db, cid, body)
            elif path == '/api/hr/candidate/meeting/confirm':
                self.confirm_meeting(db, cid, body)
            elif path == '/api/hr/candidate/telegram/link':
                require(self.notifications, 'Telegram пока не подключён.', 503)
                return self.notifications.create_telegram_link(db, cid), {}
            elif path == '/api/hr/candidate/email/request':
                require(self.notifications, 'Почта пока не подключена.', 503)
                return self.notifications.request_email_verification(db, cid), {}
            elif path == '/api/hr/candidate/email/verify':
                require(self.notifications, 'Почта пока не подключена.', 503)
                return self.notifications.verify_email(db, cid, body.get('token')), {}
            elif path == '/api/hr/recruiter/slots':
                self.add_slot(db, body)
            elif path == '/api/hr/recruiter/vacancies':
                self.add_vacancy(db, body)
            elif path == '/api/hr/recruiter/applications':
                self.propose_application(db, body)
            elif path == '/api/hr/recruiter/meetings':
                self.propose_meeting(db, body)
            elif path == '/api/hr/recruiter/profile':
                self.person(db, cid)
                profile = {'summary': text(body.get('summary', ''), 12000, required=False),
                           'evaluation': text(body.get('evaluation', ''), 12000, required=False), 'updatedAt': iso(now())}
                db.execute('INSERT INTO cp_internal_profiles VALUES (?,?) ON CONFLICT(candidate_id) DO UPDATE SET body=excluded.body',
                           (cid, dump(profile)))
            elif path in ('/api/hr/candidate/interview/join', '/api/hr/candidate/meeting/join',
                          '/api/hr/recruiter/interview/join', '/api/hr/recruiter/meeting/join',
                          '/api/hr/recruiter/interview/complete', '/api/hr/recruiter/meeting/complete',
                          '/api/hr/recruiter/interview/materials'):
                if '/meeting/' in path:
                    application = self.application(db, body.get('applicationId'), None if is_recruiter else cid)
                    cid = application['candidateId']
                    if not is_recruiter:
                        require(application['meeting'] and application['meeting']['id'] == body.get('meetingId'),
                                'Встреча изменилась. Обновите кабинет.', 409)
                else:
                    self.person(db, cid)
                # The external provider work below does not hold the state lock.
                provider_action = True
            else:
                raise Problem('Не найдено.', 404)
            if not provider_action:
                return (self.recruiter_state(db) if is_recruiter else self.candidate_state(db, cid)), response_headers

        if path.endswith('/join'):
            return self.join(cid, body.get('applicationId') if '/meeting/' in path else None,
                             body.get('consent'), owner=is_recruiter), {}
        if path.endswith('/materials'):
            return self.materials(cid), {}
        self.complete(cid, body.get('applicationId') if '/meeting/' in path else None)
        with self.connect() as db:
            return self.recruiter_state(db), {}
