"""Personal candidate accounts, independent from the legacy team test personas.

SQLite is authoritative. A person, their recruiter interview and their applications
are separate records. Candidate approval of a vacancy never confirms a meeting.
"""
import datetime as dt
import base64
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
import uuid

from domain import Problem, require, text, timestamp, iso, now
from candidate_resume import parse_upload, salary_value, start_date_value
import candidate_resume_model
import candidate_invites as invites
import recruiter_availability as availability
import interview_scheduler
import employer_workflow
import recruiter_access
import recruiter_notifications

COOKIE = '__Host-hr_candidate'
SESSION_SECONDS = 30 * 86400
MEDIA_WAIT_SECONDS = 30 * 60
PROFILE_FIELDS = {'name', 'city', 'desiredRole', 'about', 'telegram', 'geographies',
                  'availability', 'consentReuse', 'autoApprove', 'desiredSalary', 'workMode',
                  'salaryMin', 'salaryMax', 'latestStartDate', 'residence', 'employmentType', 'resumeReviewed'}


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
    except (ValueError, TypeError, AttributeError):
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


def migrate_meetings(db):
    """Retain every meeting ID while removing the former one-per-application cap.

    Run only during startup, outside request transactions. Rebuilding the parent
    under its original name keeps feedback/media references pointed at the same
    immutable IDs. A failed migration restores both table and FK enforcement.
    """
    columns = {row[1] for row in db.execute('PRAGMA table_info(cp_meetings)')}
    if 'manager_id' in columns:
        return
    require(not db.in_transaction, 'Миграция встреч требует отдельной транзакции.', 500)
    foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()[0]
    require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'Нарушена целостность встреч.', 500)
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE cp_meetings_next (
          id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES cp_applications(id),
          start TEXT NOT NULL, end TEXT NOT NULL, status TEXT NOT NULL, room TEXT,
          confirmed_at TEXT, completed_at TEXT,
          manager_id TEXT NOT NULL DEFAULT 'recruiter:team',
          revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0), created_at TEXT NOT NULL,
          cancelled_at TEXT, cancellation_reason TEXT)''')
        db.execute('''INSERT INTO cp_meetings_next
          (id,application_id,start,end,status,room,confirmed_at,completed_at,created_at)
          SELECT id,application_id,start,end,status,room,confirmed_at,completed_at,start FROM cp_meetings''')
        db.execute('DROP TABLE cp_meetings')
        db.execute('ALTER TABLE cp_meetings_next RENAME TO cp_meetings')
        require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'Нарушена целостность встреч.', 500)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=' + str(foreign_keys))


def migrate_search(db):
    """Preserve application IDs and foreign keys across successive search periods."""
    if 'searching' not in {row[1] for row in db.execute('PRAGMA table_info(cp_candidates)')}:
        db.execute('ALTER TABLE cp_candidates ADD COLUMN searching INTEGER NOT NULL DEFAULT 1 CHECK(searching IN (0,1))')
    if 'archived_at' not in {row[1] for row in db.execute('PRAGMA table_info(cp_applications)')}:
        require(not db.in_transaction, 'Миграция поиска требует отдельной транзакции.', 500)
        foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()[0]
        db.execute('PRAGMA foreign_keys=OFF')
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE cp_applications_next (
              id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
              vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id), status TEXT NOT NULL,
              approved_automatically INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
              decided_at TEXT, archived_at TEXT)''')
            db.execute('''INSERT INTO cp_applications_next
              SELECT id,candidate_id,vacancy_id,status,approved_automatically,created_at,decided_at,NULL
              FROM cp_applications''')
            db.execute('DROP TABLE cp_applications')
            db.execute('ALTER TABLE cp_applications_next RENAME TO cp_applications')
            require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'Нарушена целостность подборов.', 500)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.execute('PRAGMA foreign_keys=' + str(foreign_keys))
    db.execute('CREATE INDEX IF NOT EXISTS cp_application_candidate ON cp_applications(candidate_id)')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS cp_application_one_active
      ON cp_applications(candidate_id,vacancy_id) WHERE archived_at IS NULL''')


@contextmanager
def meeting_transaction(db):
    """Serialize calendar reads and keep a failed mutation atomic in its caller."""
    started = not db.in_transaction
    if started:
        db.execute('BEGIN IMMEDIATE')
    name = 'meeting_' + secrets.token_hex(8)
    db.execute('SAVEPOINT ' + name)
    try:
        db.execute('UPDATE cp_meetings SET status=status WHERE 0')
        yield
    except Exception:
        db.execute('ROLLBACK TO ' + name)
        db.execute('RELEASE ' + name)
        if started:
            db.rollback()
        raise
    else:
        db.execute('RELEASE ' + name)


def profile_value(body, previous=None):
    old = previous or {}
    # Ignore no unknown fields: a client must never be able to overwrite a dossier,
    # actor, email, interview status or server-assigned person/application identity.
    unexpected = set(body) - PROFILE_FIELDS
    require(not unexpected, 'В запросе есть недоступные поля профиля.', 400)
    result = {}
    for key, limit, mandatory in [('name', 140, False), ('city', 120, False),
                                  ('desiredRole', 200, False), ('about', 1500, False),
                                  ('telegram', 100, False), ('desiredSalary', 120, False), ('residence', 200, False)]:
        result[key] = text(body.get(key, old.get(key, '')), limit, required=mandatory)
    result['workMode'] = body.get('workMode', old.get('workMode', 'any'))
    require(result['workMode'] in ('office', 'hybrid', 'remote', 'any'), 'Выберите формат работы.', 400)
    result['employmentType'] = body.get('employmentType', old.get('employmentType', ''))
    require(result['employmentType'] in ('', 'full-time', 'part-time', 'project', 'internship'),
            'Выберите тип занятости.', 400)
    result['resumeReviewed'] = body.get('resumeReviewed', old.get('resumeReviewed', False))
    require(isinstance(result['resumeReviewed'], bool), 'Подтвердите данные резюме.', 400)
    geography = body.get('geographies', old.get('geographies', []))
    require(isinstance(geography, list) and len(geography) <= 20,
            'Проверьте список городов.', 400)
    result['geographies'] = list(dict.fromkeys(text(city, 120) for city in geography))
    availability = body.get('availability', old.get('availability', []))
    require(isinstance(availability, list) and len(availability) <= 40,
            'Укажите не больше 40 удобных интервалов.', 400)
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
    for key in ('salaryMin', 'salaryMax'):
        result[key] = salary_value(body.get(key, old.get(key)))
    require(result['salaryMin'] is None or result['salaryMax'] is None or result['salaryMin'] <= result['salaryMax'],
            'Верхняя граница зарплаты должна быть не меньше нижней.', 400)
    result['latestStartDate'] = start_date_value(body.get('latestStartDate', old.get('latestStartDate', '')),
                                                future='latestStartDate' in body)
    return result


class CandidatePortal:
    def __init__(self, connect, lock, media, origin, notifications=None, invitation_lookup=None, *, origins=None,
                 recruiter_origin=None):
        self.connect, self.lock, self.media, self.origin = connect, lock, media, origin
        self.origins = frozenset(origins if origins is not None else (origin,))
        self.recruiter_origin = recruiter_origin or origin
        require(self.recruiter_origin in self.origins, 'Recruiter origin must be an allowed product origin.')
        self.notifications = notifications
        self.invitation_lookup = invitation_lookup
        # Optional personal-booking adapter; no booking schema is required here.
        self.booking_invitation_lookup = None
        self.booking_invitation_store = None
        self.candidate_access = None
        self._media_guard = threading.Lock()
        self._media_locks = {}
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))
        with self.connect() as db:
            self.init(db)
        self.resume_model = candidate_resume_model.ResumeModel(self.connect, self.lock)

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
        CREATE TABLE IF NOT EXISTS cp_resumes (
          candidate_id TEXT PRIMARY KEY REFERENCES cp_candidates(id), filename TEXT NOT NULL,
          mime TEXT NOT NULL, content BLOB NOT NULL, text_content TEXT NOT NULL,
          extracted TEXT NOT NULL, uploaded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cp_media_consents (
          meeting_id TEXT NOT NULL, actor_id TEXT NOT NULL, consented_at TEXT NOT NULL,
          version TEXT NOT NULL, PRIMARY KEY(meeting_id,actor_id));
        CREATE TABLE IF NOT EXISTS cp_media_state (
          meeting_id TEXT PRIMARY KEY, kind TEXT NOT NULL, room_id TEXT NOT NULL,
          body TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS cp_application_candidate ON cp_applications(candidate_id);
        ''')
        invites.migrate_accounts(db)
        candidate_resume_model.init(db)
        migrate_meetings(db)
        migrate_search(db)
        invites.init(db)
        availability.init(db)
        recruiter_access.init(db)
        recruiter_notifications.init(db)
        db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS cp_meeting_one_active
          ON cp_meetings(application_id) WHERE status IN ('pending','confirmed','live')''')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS cp_meeting_revision ON cp_meetings(application_id,revision)')
        db.execute('CREATE INDEX IF NOT EXISTS cp_meeting_calendar ON cp_meetings(manager_id,status,start,end)')
        employer_workflow.init(db)
        db.execute('''CREATE TABLE IF NOT EXISTS cp_workflow_events (
          event_key TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
          application_id TEXT NOT NULL REFERENCES cp_applications(id),
          body TEXT NOT NULL, created_at TEXT NOT NULL)''')
        if self.notifications:
            self.notifications.init(db)

    @staticmethod
    def handles(path):
        return path.startswith('/api/hr/candidate/') or path.startswith('/api/hr/recruiter/')

    @staticmethod
    def recruiter_principal(db, headers, manager_actor):
        """Return a named recruiter or the explicitly authenticated legacy team.

        Callers still enforce candidate and schedule scope; a named identity is
        never silently translated into the legacy manager's global authority.
        """
        recruiter = recruiter_access.authenticate(db, headers, required=False)
        if recruiter:
            return {**recruiter, 'legacy': False}
        # Invalid or revoked recruiter credentials cannot fall back to a broad
        # team credential retained elsewhere in the same browser.
        if recruiter_access.token(headers):
            raise Problem('Войдите в кабинет рекрутера.', 401)
        require(manager_actor(db) == 'manager', 'Доступно только команде рекрутинга.', 403)
        return {'id': 'recruiter:team', 'name': 'Команда HR Vision', 'email': None, 'legacy': True}

    @staticmethod
    def recruiter_candidate(db, principal, cid, *, allow_prepared=False):
        if principal['legacy']:
            return
        row = db.execute('''SELECT i.id FROM cp_interviews i
            JOIN cp_recruiter_slot_owners o ON o.slot_id=i.slot_id
            WHERE i.candidate_id=? AND o.recruiter_id=?''', (cid, principal['id'])).fetchone()
        if not row and allow_prepared:
            row = db.execute('''SELECT candidate_id FROM cp_recruiter_prepared_owners
                WHERE candidate_id=? AND recruiter_id=? AND NOT EXISTS (
                SELECT 1 FROM cp_interviews WHERE candidate_id=?)''', (cid, principal['id'], cid)).fetchone()
        require(row, 'Интервью не найдено.', 404)

    @staticmethod
    def recruiter_slot(db, principal, slot_id):
        if not principal['legacy']:
            require(db.execute('SELECT 1 FROM cp_recruiter_slot_owners WHERE slot_id=? AND recruiter_id=?',
                               (slot_id, principal['id'])).fetchone(), 'Слот не найден.', 404)

    def recruiter_auth(self, method, path, headers, body, ip, manager_actor, team_grant):
        route = 'accounts' if path == '/api/hr/recruiter/accounts' else path.removeprefix('/api/hr/recruiter/auth/')
        require((method == 'GET' and route == 'session') or
                (method == 'POST' and route in ('register', 'login', 'logout', 'profile', 'accounts', 'activate')),
                'Не найдено.', 404)

        def can_register(db):
            if team_grant is True:
                return True
            try:
                return manager_actor(db) == 'manager'
            except Problem:
                return False

        if route in ('register', 'login', 'accounts', 'activate'):
            # Commit attempt counters independently from a failed password check.
            with self.lock, self.connect() as db:
                recruiter_access.consume_auth_limits(db, route, body, ip)
            with self.lock, self.connect() as db:
                allowed = can_register(db)
                if route == 'accounts':
                    return recruiter_access.provision(db, body, self.recruiter_origin, authorized=allowed), {}
                if route == 'register':
                    result = recruiter_access.register(db, body, authorized=allowed)
                else:
                    result = getattr(recruiter_access, route)(db, body)
                result['registrationAllowed'] = allowed
                result['canManageAccounts'] = allowed
                return result, {'Set-Cookie': recruiter_access.session_cookie(result['token'])}
        with self.lock, self.connect() as db:
            if route == 'logout':
                require(not body, 'Проверьте поля выхода.', 400)
                return {'ok': True}, {'Set-Cookie': recruiter_access.logout(db, headers)}
            if route == 'profile':
                result = recruiter_access.update_profile(db, headers, body)
            else:
                result = recruiter_access.session(db, headers)
            result['registrationAllowed'] = can_register(db)
            result['canManageAccounts'] = result['registrationAllowed']
            return result, {}

    def event(self, db, cid, event_type, entity_id):
        active = db.execute('SELECT email FROM cp_candidates WHERE id=?', (cid,)).fetchone()
        if self.notifications and active and active[0]:
            self.notifications.enqueue(db, cid, event_type + ':' + entity_id, event_type)

    def channels(self, db=None, cid=None):
        if not self.notifications:
            return {'email': False, 'telegram': False}
        if cid is not None:
            active = db.execute('SELECT email FROM cp_candidates WHERE id=?', (cid,)).fetchone()
            if not active or not active[0]:
                return {'email': False, 'telegram': False}
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
        row = db.execute('SELECT id,email,profile,created_at,searching FROM cp_candidates WHERE id=?', (cid,)).fetchone()
        require(row is not None, 'Кандидат не найден.', 404)
        return {'id': row[0], 'email': row[1], 'accountActive': row[1] is not None,
                **json.loads(row[2]), 'createdAt': row[3], 'searching': bool(row[4])}

    def interview(self, db, cid):
        row = db.execute('''SELECT i.id,i.status,i.room,s.start,s.end,s.recruiter_name,i.completed_at
          FROM cp_interviews i JOIN cp_recruiter_slots s ON i.slot_id=s.id WHERE i.candidate_id=?''', (cid,)).fetchone()
        if not row:
            return None
        return {'id': row[0], 'status': row[1], 'room': json.loads(row[2]) if row[2] else None,
                'start': row[3], 'end': row[4], 'recruiterName': row[5], 'completedAt': row[6],
                'recording': invites.recording(db, row[0])}

    def meeting(self, db, app_id, meeting_id=None):
        row = db.execute('''SELECT id,start,end,status,room,completed_at,revision,confirmed_at,cancelled_at,cancellation_reason
          FROM cp_meetings WHERE application_id=?''' + (' AND id=?' if meeting_id is not None else '') +
          ' ORDER BY revision DESC LIMIT 1', (app_id, meeting_id) if meeting_id is not None else (app_id,)).fetchone()
        return ({'id': row[0], 'start': row[1], 'end': row[2], 'status': row[3],
                 'room': json.loads(row[4]) if row[4] else None, 'completedAt': row[5],
                 'revision': row[6], 'confirmedAt': row[7], 'cancelledAt': row[8],
                 'cancellationReason': row[9]} if row else None)

    def application(self, db, app_id, cid=None):
        from vacancy_import import candidate_vacancy
        row = db.execute('''SELECT a.id,a.candidate_id,a.status,a.approved_automatically,v.body,a.archived_at
          FROM cp_applications a JOIN cp_vacancies v ON a.vacancy_id=v.id WHERE a.id=?''', (app_id,)).fetchone()
        require(row is not None and (cid is None or row[1] == cid), 'Вакансия не найдена.', 404)
        return {'id': row[0], 'candidateId': row[1], 'status': row[2],
                'approvedAutomatically': bool(row[3]), 'vacancy': candidate_vacancy(json.loads(row[4])), 'archivedAt': row[5],
                'meeting': self.meeting(db, app_id),
                'meetings': [self.meeting(db, app_id, item[0]) for item in db.execute(
                    'SELECT id FROM cp_meetings WHERE application_id=? ORDER BY revision DESC', (app_id,))]}

    def candidate_state(self, db, cid, internal=False, *, recruiter_view=False):
        result = self.person(db, cid)
        result['interview'] = self.interview(db, cid)
        result['resume'] = self.resume_metadata(db, cid)
        result['onboarding'] = self.onboarding(result)
        if result['interview']:
            result['interview'].pop('room', None)
        result['applications'] = []
        applications = [] if recruiter_view else db.execute(
            'SELECT id FROM cp_applications WHERE candidate_id=? ORDER BY created_at DESC,rowid DESC', (cid,))
        for row in applications:
            application = self.application(db, row[0], cid)
            application.pop('candidateId', None)
            if application['meeting']:
                application['meeting'].pop('room', None)
                # Existing candidate/recruiter cards know only active/completed
                # meetings. Keep cancelled rows in history, not as a confirmed
                # appointment in those cards; no interface change is required.
                if application['meeting']['status'] in ('cancelled', 'declined'):
                    application['meeting'] = None
            for meeting in application['meetings']:
                meeting.pop('room', None)
            workflow = employer_workflow.load(db, row[0], actor='manager' if internal else 'candidate', candidate_id=cid)
            application['workflow'] = workflow
            application.update({key: workflow[key] for key in ('offer', 'decision', 'afterInterest', 'messages')})
            application['workflowRevision'] = workflow['revision']
            result['applications'].append(application)
        result['videoConfigured'] = self.media.configured
        result['channels'] = {'email': False, 'telegram': False} if recruiter_view else self.channels(db, cid)
        if internal:
            result['intake'] = invites.intake(db, cid)
            profile = db.execute('SELECT body FROM cp_internal_profiles WHERE candidate_id=?', (cid,)).fetchone()
            result['internalProfile'] = json.loads(profile[0]) if profile else None
        return result

    def set_searching(self, db, cid, body):
        require(isinstance(body, dict) and set(body) == {'searching'} and type(body['searching']) is bool,
                'Укажите, ищете ли вы работу.', 400)
        with meeting_transaction(db):
            person = self.person(db, cid)
            if not body['searching']:
                live = db.execute("SELECT 1 FROM cp_interviews WHERE candidate_id=? AND status='live'", (cid,)).fetchone()
                live = live or db.execute('''SELECT 1 FROM cp_meetings m JOIN cp_applications a ON a.id=m.application_id
                  WHERE a.candidate_id=? AND m.status='live' ''', (cid,)).fetchone()
                require(not live, 'Сначала завершите текущий звонок, затем остановите поиск.', 409)
                changed = iso(now())
                db.execute('''UPDATE cp_meetings SET status='cancelled',cancelled_at=?,cancellation_reason='search_stopped'
                  WHERE status IN ('pending','confirmed') AND application_id IN
                  (SELECT id FROM cp_applications WHERE candidate_id=? AND archived_at IS NULL)''', (changed, cid))
                db.execute('UPDATE cp_applications SET archived_at=? WHERE candidate_id=? AND archived_at IS NULL', (changed, cid))
                db.execute('''UPDATE cp_offer_versions SET status='withdrawn' WHERE status='sent'
                  AND application_id IN (SELECT id FROM cp_applications WHERE candidate_id=? AND archived_at IS NOT NULL)''', (cid,))
            if person['searching'] != body['searching']:
                db.execute('UPDATE cp_candidates SET searching=?,updated_at=? WHERE id=?',
                           (int(body['searching']), iso(now()), cid))

    def require_application_active(self, db, application):
        require(not application['archivedAt'] and self.person(db, application['candidateId'])['searching'],
                'Этот подбор завершён после остановки поиска. Новые предложения появятся после возобновления.', 409)

    def slots(self, db, internal=False, recruiter_id=None):
        availability.materialize(db)
        allowed = availability.open_slot_ids(db)
        rows = db.execute('''SELECT s.id,s.start,s.end,COALESCE(a.name,s.recruiter_name),s.candidate_id
            FROM cp_recruiter_slots s LEFT JOIN cp_recruiter_slot_owners o ON o.slot_id=s.id
            LEFT JOIN rc_accounts a ON a.id=o.recruiter_id
            WHERE ''' + ('(o.recruiter_id IS NULL OR a.active=1)' if internal else 'a.active=1') +
            (' AND o.recruiter_id=?' if recruiter_id else '') + ' ORDER BY s.start',
            (recruiter_id,) if recruiter_id else ())
        return [{'id': row[0], 'start': row[1], 'end': row[2], 'recruiterName': row[3],
                 **({'booked': bool(row[4]), 'candidateId': row[4]} if internal else {})}
                for row in rows if (row[0] in allowed or row[4])
                and (internal or (not row[4] and timestamp(row[1]) > now()))]

    def booking_slots(self, db):
        return interview_scheduler.public_slots(db)

    def recruiter_state(self, db, principal=None):
        personal = principal and not principal['legacy']
        if personal:
            rows = db.execute('''SELECT c.id FROM cp_candidates c JOIN cp_interviews i ON i.candidate_id=c.id
                JOIN cp_recruiter_slot_owners o ON o.slot_id=i.slot_id WHERE o.recruiter_id=?
                ORDER BY c.created_at DESC,c.rowid DESC''', (principal['id'],)).fetchall()
        else:
            rows = db.execute('SELECT id FROM cp_candidates ORDER BY created_at DESC,rowid DESC').fetchall()
        candidates = []
        for row in rows:
            candidate = self.candidate_state(db, row[0], internal=True, recruiter_view=bool(personal))
            from recruiter_sources import candidate_sources
            candidate.update(candidate_sources(db, row[0]))
            candidates.append(candidate)
        if personal:
            vacancy_ids = {app['vacancy']['id'] for person in candidates for app in person['applications']}
            vacancies = [json.loads(row[0]) for row in db.execute('SELECT body FROM cp_vacancies WHERE id IN (' +
                ','.join('?' for _ in vacancy_ids) + ')', tuple(vacancy_ids))] if vacancy_ids else []
        else:
            vacancies = [json.loads(row[0]) for row in db.execute('SELECT body FROM cp_vacancies ORDER BY created_at DESC,rowid DESC')]
        return {'candidates': candidates,
                'slots': self.slots(db, internal=True, recruiter_id=principal['id'] if personal else None),
                'availabilityBlocks': availability.blocks(db, recruiter_id=principal['id'] if personal else None),
                'availabilityDates': availability.date_overrides(db, principal['id']) if personal else [],
                'vacancies': vacancies,
                'videoConfigured': self.media.configured}

    @staticmethod
    def auto_matches(person, vacancy):
        if not person['autoApprove'] or not person['consentReuse']:
            return False
        if normalize_city(person['desiredRole']) != normalize_city(vacancy['title']):
            return False
        # The first vacancy bridge has no structured compensation/hybrid schedule.
        # Unknown requirements therefore always need a personal decision.
        if person['desiredSalary'] or person.get('salaryMin') or person.get('salaryMax') or person['workMode'] == 'hybrid':
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
        require(not body.get('resumeReviewed'), 'Сначала загрузите резюме.', 400)
        email = email_value(body.get('email'))
        require(db.execute('SELECT 1 FROM cp_candidates WHERE email=?', (email,)).fetchone() is None,
                'Не удалось создать аккаунт. Если вы уже регистрировались, войдите.', 409)
        profile = profile_value({key: value for key, value in body.items() if key in PROFILE_FIELDS})
        require(profile['consentReuse'], 'Подтвердите использование интервью для подбора вакансий.', 400)
        cid, created = uid('person'), iso(now())
        db.execute('INSERT INTO cp_candidates(id,email,password_hash,profile,created_at,updated_at) VALUES (?,?,?,?,?,?)',
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
        if body.get('resumeReviewed'):
            require(self.resume_metadata(db, cid), 'Сначала загрузите резюме.', 409)
            require(all(profile.get(key) for key in ('salaryMin', 'salaryMax', 'latestStartDate')),
                    'Укажите желаемую зарплату и дату выхода на работу.', 400)
        changed = any(profile[key] != previous[key] for key in ('consentReuse', 'autoApprove'))
        db.execute('UPDATE cp_candidates SET profile=?,updated_at=? WHERE id=?', (dump(profile), iso(now()), cid))
        if changed:
            db.execute('INSERT INTO cp_consent_log VALUES (?,?,?,?,?,?)',
                       (uid('consent'), cid, iso(now()), int(profile['consentReuse']),
                        int(profile['autoApprove']), 'candidate-preferences-v1'))

    def resume_metadata(self, db, cid):
        row = db.execute('SELECT filename,mime,length(content),extracted,uploaded_at FROM cp_resumes WHERE candidate_id=?', (cid,)).fetchone()
        return ({'filename': row[0], 'mime': row[1], 'size': row[2],
                 'extractedFields': list(json.loads(row[3])), 'uploadedAt': row[4]} if row else None)

    @staticmethod
    def onboarding(person):
        missing = [key for key in ('salaryMin', 'salaryMax', 'latestStartDate') if not person.get(key)]
        if person.get('latestStartDate'):
            try:
                start_date_value(person['latestStartDate'], future=True)
            except Problem:
                missing.append('latestStartDate')
        # An existing appointment is a commitment. Migration must never hide it.
        step = 'complete' if person.get('interview') else 'resume' if not person.get('resume') else 'preferences' if missing or not person.get('resumeReviewed') else 'complete'
        return {'step': step, 'missingFields': missing}

    def save_resume(self, db, cid, resume):
        previous = self.person(db, cid)
        fields = dict(resume['fields'])
        if '_profileBefore' in resume:
            # Do not overwrite preferences edited in another tab during the model request.
            fields = {key: value for key, value in fields.items()
                      if previous.get(key) == resume['_profileBefore'].get(key)}
        if fields.get('salaryMin') and 'salaryMax' not in fields and previous.get('salaryMax') and fields['salaryMin'] > previous['salaryMax']:
            fields['salaryMax'] = None
        if fields.get('salaryMax') and 'salaryMin' not in fields and previous.get('salaryMin') and fields['salaryMax'] < previous['salaryMin']:
            fields['salaryMin'] = None
        # Sparse updates preserve prior user preferences and meeting availability.
        profile = profile_value({**fields, 'resumeReviewed': False}, previous)
        uploaded = iso(now())
        db.execute('''INSERT INTO cp_resumes VALUES (?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET
          filename=excluded.filename,mime=excluded.mime,content=excluded.content,text_content=excluded.text_content,
          extracted=excluded.extracted,uploaded_at=excluded.uploaded_at''',
                   (cid, resume['filename'], resume['mime'], resume['content'], resume['text'], dump(resume['fields']), uploaded))
        db.execute('UPDATE cp_candidates SET profile=?,updated_at=? WHERE id=?', (dump(profile), uploaded, cid))

    def download_resume(self, db, cid):
        row = db.execute('SELECT filename,mime,content FROM cp_resumes WHERE candidate_id=?', (cid,)).fetchone()
        require(row, 'Резюме ещё не загружено.', 404)
        return {'filename': row[0], 'mime': row[1], 'contentBase64': base64.b64encode(row[2]).decode('ascii')}

    def add_slot(self, db, body, *, principal=None):
        availability.materialize(db)
        times = interval(body.get('start'), future=True)
        personal = principal and not principal['legacy']
        recruiter = principal['name'] if personal else text(body.get('recruiterName'), 140)
        for row in db.execute('''SELECT s.start,s.end,s.recruiter_name,o.recruiter_id FROM cp_recruiter_slots s
            LEFT JOIN cp_recruiter_slot_owners o ON o.slot_id=s.id'''):
            same = row[3] == principal['id'] if personal else normalize_city(row[2]) == normalize_city(recruiter)
            require(not same or not overlaps(times, {'start': row[0], 'end': row[1]}),
                    'У рекрутера уже есть слот на это время.', 409)
        slot_id = uid('slot')
        db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)',
                   (slot_id, times['start'], times['end'], recruiter))
        if personal:
            db.execute('INSERT INTO cp_recruiter_slot_owners VALUES (?,?)', (slot_id, principal['id']))

    def book(self, db, cid, body, *, prepared=False, pooled=False):
        availability.materialize(db)
        requested = body.get('slotId')
        existing = db.execute('SELECT slot_id FROM cp_interviews WHERE candidate_id=?', (cid,)).fetchone()
        if existing:
            matches = interview_scheduler.same_window(db, requested, existing[0]) if pooled else existing[0] == requested
            require(matches, 'Вы уже записаны. Для переноса свяжитесь с командой.', 409)
            return
        chosen = interview_scheduler.select_slot(db, requested) if pooled else requested
        slot = db.execute('SELECT id,start,end,candidate_id FROM cp_recruiter_slots WHERE id=?', (chosen,)).fetchone()
        require(slot is not None, 'Слот не найден.', 404)
        owner = db.execute('''SELECT o.recruiter_id,a.active FROM cp_recruiter_slot_owners o
            JOIN rc_accounts a ON a.id=o.recruiter_id WHERE o.slot_id=?''', (slot[0],)).fetchone()
        require((owner and owner[1] == 1) or (prepared and not owner),
                'Это время уже недоступно. Выберите другое.', 409)
        require(slot[0] in availability.open_slot_ids(db), 'Это время уже недоступно. Выберите другое.', 409)
        person = self.candidate_state(db, cid)
        require(person['resume'] is not None, 'Сначала загрузите резюме.', 409)
        if prepared:
            require(invites.intake(db, cid), 'Профиль не подготовлен рекрутером.', 409)
        else:
            require(person['onboarding']['step'] == 'complete', 'Укажите желаемую зарплату и дату выхода на работу.', 409)
            require(person['consentReuse'], 'Подтвердите согласие в профиле.', 400)
        require(slot[3] is None and timestamp(slot[1]) > now(), 'Это время уже недоступно. Выберите другое.', 409)
        self.ensure_candidate_free(db, cid, {'start': slot[1], 'end': slot[2]})
        iid = uid('interview')
        reserved = db.execute('UPDATE cp_recruiter_slots SET candidate_id=? WHERE id=? AND candidate_id IS NULL', (cid, slot[0]))
        require(reserved.rowcount == 1, 'Это время уже недоступно. Выберите другое.', 409)
        db.execute('INSERT INTO cp_interviews VALUES (?,?,?,\'booked\',NULL,?,NULL)', (iid, cid, slot[0], iso(now())))
        if owner:
            recruiter_notifications.enqueue_booking(db, owner[0], iid)
        if person['accountActive']:
            self.event(db, cid, 'interview.booked', iid)

    def ensure_candidate_free(self, db, cid, times, exclude_meeting_id=None):
        interview = self.interview(db, cid)
        require(not interview or interview['status'] == 'completed' or not overlaps(times, interview),
                'На это время у кандидата уже назначено интервью.', 409)
        for row in db.execute('''SELECT m.id,m.start,m.end FROM cp_meetings m JOIN cp_applications a
          ON m.application_id=a.id WHERE a.candidate_id=? AND m.status IN ('pending','confirmed','live')
          AND (a.archived_at IS NULL OR m.status='live')''', (cid,)):
            if row[0] != exclude_meeting_id:
                require(not overlaps(times, {'start': row[1], 'end': row[2]}), 'Это время кандидата уже занято.', 409)

    def add_vacancy(self, db, body):
        vacancy = {key: text(body.get(key, ''), limit, required=key != 'description')
                   for key, limit in [('title', 200), ('company', 200), ('city', 120), ('description', 10000)]}
        require(isinstance(body.get('remote'), bool), 'Укажите формат вакансии.', 400)
        vacancy.update(id=uid('vacancy'), remote=body['remote'])
        db.execute('INSERT INTO cp_vacancies VALUES (?,?,?)', (vacancy['id'], dump(vacancy), iso(now())))

    def propose_application(self, db, body):
        with meeting_transaction(db):
            cid = body.get('candidateId')
            person = self.person(db, cid)
            require(person['searching'], 'Кандидат остановил поиск работы.', 409)
            interview = self.interview(db, cid)
            require(interview is not None and interview['status'] == 'completed', 'Сначала завершите первичное интервью.', 409)
            require(person['consentReuse'], 'Кандидат не разрешил использовать интервью для новых вакансий.', 409)
            row = db.execute('SELECT body FROM cp_vacancies WHERE id=?', (body.get('vacancyId'),)).fetchone()
            require(row is not None, 'Вакансия не найдена.', 404)
            vacancy = json.loads(row[0])
            existing = db.execute('SELECT id FROM cp_applications WHERE candidate_id=? AND vacancy_id=? AND archived_at IS NULL', (cid, vacancy['id'])).fetchone()
            if existing:
                return
            auto = self.auto_matches(person, vacancy)
            aid = uid('application')
            db.execute('INSERT INTO cp_applications(id,candidate_id,vacancy_id,status,approved_automatically,created_at,decided_at) VALUES (?,?,?,?,?,?,?)',
                       (aid, cid, vacancy['id'], 'accepted' if auto else 'pending', int(auto), iso(now()), iso(now()) if auto else None))
            self.event(db, cid, 'application.proposed', aid)

    def decide_application(self, db, cid, body):
        with meeting_transaction(db):
            application = self.application(db, body.get('applicationId'), cid)
            self.require_application_active(db, application)
            decision = body.get('decision')
            require(decision in ('accepted', 'declined'), 'Выберите решение.', 400)
            if application['status'] == decision:
                return
            require(application['status'] == 'pending', 'Решение уже сохранено.', 409)
            require(decision != 'accepted' or self.person(db, cid)['consentReuse'], 'Подтвердите использование интервью в профиле.', 409)
            db.execute('UPDATE cp_applications SET status=?,approved_automatically=0,decided_at=? WHERE id=?',
                       (decision, iso(now()), application['id']))
            import employer_notifications
            employer_notifications.collect(db)

    def _application_open_for_meeting(self, db, application):
        self.require_application_active(db, application)
        require(application['status'] == 'accepted', 'Сначала кандидат должен согласовать вакансию.', 409)
        person = self.person(db, application['candidateId'])
        require(person['consentReuse'], 'Кандидат не разрешил использовать интервью для вакансий.', 409)
        workflow = employer_workflow.load(db, application['id'])
        require(workflow['afterInterest'] != 'no',
                'Кандидат отказался продолжать после встречи. Новое назначение недоступно без его согласия.', 409)
        require(workflow['decision'] != 'declined' and not workflow['closedBy']
                and application['vacancy'].get('status') not in ('closed', 'archived'),
                'Работа по вакансии завершена.', 409)
        return person

    def _meeting_time(self, db, application, body, manager_id, exclude_meeting_id=None):
        person = self._application_open_for_meeting(db, application)
        require(isinstance(manager_id, str) and 1 <= len(manager_id) <= 200, 'Не определён календарь работодателя.', 400)
        times = interval(body.get('start'), body.get('end'), future=True)
        require((timestamp(times['end']) - timestamp(times['start'])).total_seconds() == 1800,
                'Встреча длится 30 минут.', 400)
        require(any(timestamp(slot['start']) <= timestamp(times['start']) and timestamp(slot['end']) >= timestamp(times['end'])
                    for slot in person['availability']), 'Выберите время из доступности кандидата.', 409)
        self.ensure_candidate_free(db, person['id'], times, exclude_meeting_id)
        for row in db.execute('''SELECT m.id,m.start,m.end FROM cp_meetings m JOIN cp_applications a ON a.id=m.application_id
          WHERE m.manager_id=? AND m.status IN ('pending','confirmed','live')
          AND (a.archived_at IS NULL OR m.status='live')''', (manager_id,)):
            if row[0] != exclude_meeting_id:
                require(not overlaps(times, {'start': row[1], 'end': row[2]}), 'У нанимающего уже есть встреча на это время.', 409)
        return times

    @staticmethod
    def _meeting_revision(meeting, body, *, required=False):
        require(meeting and meeting['id'] == body.get('meetingId'), 'Встреча изменилась. Обновите кабинет.', 409)
        if required or 'revision' in body:
            require(type(body.get('revision')) is int and body['revision'] == meeting['revision'],
                    'Время встречи изменилось. Обновите кабинет.', 409)

    def _insert_meeting(self, db, application, times, manager_id):
        revision = db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM cp_meetings WHERE application_id=?',
                              (application['id'],)).fetchone()[0]
        mid = uid('meeting')
        db.execute('''INSERT INTO cp_meetings
          (id,application_id,start,end,status,manager_id,revision,created_at)
          VALUES (?,?,?,?,'pending',?,?,?)''',
          (mid, application['id'], times['start'], times['end'], manager_id, revision, iso(now())))
        return self.meeting(db, application['id'], mid)

    def propose_meeting(self, db, body, *, manager_id='recruiter:team'):
        with meeting_transaction(db):
            application = self.application(db, body.get('applicationId'))
            require(not application['meeting'] or application['meeting']['status'] in ('completed', 'cancelled', 'declined'),
                    'Встреча уже назначена. Используйте перенос встречи.', 409)
            times = self._meeting_time(db, application, body, manager_id)
            meeting = self._insert_meeting(db, application, times, manager_id)
            self.event(db, application['candidateId'], 'meeting.proposed', meeting['id'])
            return meeting

    def reschedule_meeting(self, db, body, *, manager_id='recruiter:team'):
        with meeting_transaction(db):
            application = self.application(db, body.get('applicationId'))
            meeting = application['meeting']
            self._meeting_revision(meeting, body, required=True)
            owner = db.execute('SELECT manager_id FROM cp_meetings WHERE id=?', (meeting['id'],)).fetchone()
            require(owner and owner[0] == manager_id, 'Встреча не найдена.', 404)
            require(meeting['status'] in ('pending', 'confirmed'), 'Эту встречу уже нельзя перенести.', 409)
            times = self._meeting_time(db, application, body, manager_id, meeting['id'])
            if times['start'] == meeting['start'] and times['end'] == meeting['end']:
                return meeting
            db.execute('''UPDATE cp_meetings SET status='cancelled',cancelled_at=?,cancellation_reason='rescheduled'
              WHERE id=?''', (iso(now()), meeting['id']))
            next_meeting = self._insert_meeting(db, application, times, manager_id)
            self.event(db, application['candidateId'], 'meeting.rescheduled', next_meeting['id'])
            return next_meeting

    def cancel_meeting(self, db, body, *, manager_id='recruiter:team'):
        with meeting_transaction(db):
            application = self.application(db, body.get('applicationId'))
            self.require_application_active(db, application)
            meeting = application['meeting']
            self._meeting_revision(meeting, body, required=True)
            owner = db.execute('SELECT manager_id FROM cp_meetings WHERE id=?', (meeting['id'],)).fetchone()
            require(owner and owner[0] == manager_id, 'Встреча не найдена.', 404)
            if meeting['status'] == 'cancelled':
                return meeting
            require(meeting['status'] in ('pending', 'confirmed'), 'Текущую встречу уже нельзя отменить.', 409)
            db.execute('''UPDATE cp_meetings SET status='cancelled',cancelled_at=?,cancellation_reason='manager'
              WHERE id=?''', (iso(now()), meeting['id']))
            self.event(db, application['candidateId'], 'meeting.cancelled', meeting['id'])
            return self.meeting(db, application['id'], meeting['id'])

    def confirm_meeting(self, db, cid, body):
        with meeting_transaction(db):
            application = self.application(db, body.get('applicationId'), cid)
            meeting = application['meeting']
            self._meeting_revision(meeting, body)
            self._application_open_for_meeting(db, application)
            if meeting['status'] == 'confirmed':
                return
            require(meeting['status'] == 'pending' and timestamp(meeting['start']) > now(), 'Это время уже недоступно.', 409)
            self.ensure_candidate_free(db, cid, meeting, meeting['id'])
            db.execute("UPDATE cp_meetings SET status='confirmed',confirmed_at=? WHERE id=?", (iso(now()), meeting['id']))
            self.event(db, cid, 'meeting.confirmed', meeting['id'])

    def decline_meeting(self, db, cid, body):
        with meeting_transaction(db):
            application = self.application(db, body.get('applicationId'), cid)
            self.require_application_active(db, application)
            meeting = application['meeting']
            self._meeting_revision(meeting, body)
            if meeting['status'] == 'declined':
                return
            require(meeting['status'] in ('pending', 'confirmed') and timestamp(meeting['start']) > now(),
                    'Это время уже недоступно.', 409)
            db.execute('''UPDATE cp_meetings SET status='declined',cancelled_at=?,cancellation_reason='candidate'
              WHERE id=?''', (iso(now()), meeting['id']))
            self.event(db, cid, 'meeting.declined', meeting['id'])

    def workflow_events(self, db, events):
        """Durable events share the caller transaction; no delivery is implied."""
        for event in events:
            inserted = db.execute('INSERT OR IGNORE INTO cp_workflow_events VALUES (?,?,?,?,?)',
              (event['eventKey'], event['candidateId'], event['applicationId'], dump(event), iso(now())))
            if inserted.rowcount and event.get('audience', 'candidate') == 'candidate':
                self.event(db, event['candidateId'], event['type'], event['entityId'])

    def create_prepared(self, db, resume, contact_email=''):
        contact_email = email_value(contact_email) if contact_email else ''
        profile = profile_value(resume['fields'])
        return invites.create_prepared(db, profile, contact_email=contact_email, cv=resume)

    def guest_state(self, db, capability):
        person = self.person(db, capability['candidateId'])
        meeting = self.interview(db, person['id'])
        require(meeting and meeting['id'] == capability['interviewId'], 'Встреча не найдена.', 404)
        meeting.pop('room', None)
        return {'name': person['name'], 'interview': meeting, 'videoConfigured': self.media.configured}

    def claim_state(self, db, capability):
        person = self.person(db, capability['candidateId'])
        if capability['consumed'] or person['accountActive']:
            return {'claimed': True, 'candidate': None}
        # A profile capability grants this person's public profile and CV, not the
        # recruiter's internal assessment, applications or notification settings.
        person['resume'] = self.resume_metadata(db, person['id'])
        person['interview'] = self.interview(db, person['id'])
        if person['interview']:
            person['interview'].pop('room', None)
        person['onboarding'] = self.onboarding(person)
        return {'claimed': False, 'candidate': person}

    def claim(self, db, capability, body):
        require(set(body) <= {'token', 'email', 'password', 'consentReuse', 'profile'}, 'Проверьте поля регистрации.', 400)
        cid = capability['candidateId']
        person = self.person(db, cid)
        require(not person['accountActive'], 'Профиль уже активирован. Войдите в кабинет.', 409)
        email = email_value(body.get('email'))
        require(not db.execute('SELECT 1 FROM cp_candidates WHERE email=?', (email,)).fetchone(),
                'Эта почта уже зарегистрирована. Войдите в существующий аккаунт и обратитесь к рекрутеру.', 409)
        require(body.get('consentReuse') is True, 'Подтвердите использование интервью для подбора вакансий.', 400)
        edits = body.get('profile', {})
        require(isinstance(edits, dict), 'Проверьте профиль.', 400)
        require('consentReuse' not in edits, 'Подтвердите согласие отдельно.', 400)
        profile = profile_value({**edits, 'consentReuse': True}, person)
        if profile.get('resumeReviewed'):
            require(all(profile.get(key) for key in ('salaryMin', 'salaryMax', 'latestStartDate')),
                    'Укажите желаемую зарплату и дату выхода на работу.', 400)
        hashed = password_hash(body.get('password'))
        changed = iso(now())
        db.execute('UPDATE cp_candidates SET email=?,password_hash=?,profile=?,updated_at=? WHERE id=?',
                   (email, hashed, dump(profile), changed, cid))
        db.execute('UPDATE cp_invitations SET consumed_at=? WHERE id=?', (changed, capability['id']))
        db.execute('INSERT INTO cp_consent_log VALUES (?,?,?,?,?,?)',
                   (uid('consent'), cid, changed, 1, int(profile['autoApprove']), 'candidate-claim-v1'))
        if self.notifications:
            self.notifications.set_email(db, cid, email, verified=False)
        self.event(db, cid, 'candidate.registered', cid)
        return cid

    def capability_request(self, path, body, ip):
        # Capability values are accepted only in a JSON body, never in query logs.
        token = body.get('token')
        self.rate_limit('invitation-ip:' + ip, 120, 60)
        if path == '/api/hr/candidate/claim':
            self.rate_limit('claim-ip:' + ip, 20, 900)
        scope = 'call' if '/guest/' in path else 'claim'
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            capability = invites.resolve(db, token, scope, allow_consumed=path.endswith('/claim/state'))
            if scope == 'call':
                state = self.guest_state(db, capability)
                if path.endswith('/state'):
                    return state, {}
                cid = capability['candidateId']
            elif path.endswith('/state'):
                return self.claim_state(db, capability), {}
            elif path.endswith('/resume'):
                require(not self.person(db, capability['candidateId'])['accountActive'], 'Войдите в свой кабинет.', 409)
                return self.download_resume(db, capability['candidateId']), {}
            else:
                cid = self.claim(db, capability, body)
                return self.candidate_state(db, cid), {'Set-Cookie': self.session_cookie(db, cid)}
        return self.join(cid, None, None, guest_token=token), {}

    def join(self, cid, application_id, consent, owner=False, guest_token=None,
             meeting_id=None, owner_name=None, owner_id='recruiter', access_check=None):
        require(owner or guest_token is not None or consent is True, 'Подтвердите запись разговора.', 400)
        # Provider I/O is deliberately outside the shared SQLite transaction.
        with self.lock, self.connect() as db:
            if access_check:
                access_check(db)
            person = self.person(db, cid)
            if application_id:
                application = self.application(db, application_id, cid)
                self._application_open_for_meeting(db, application)
                meeting = application['meeting']
                require(meeting_id is None or meeting and meeting['id'] == meeting_id,
                        'Встреча изменилась. Обновите кабинет.', 409)
                require(application['status'] == 'accepted' and meeting and meeting['status'] in ('confirmed', 'live'),
                        'Кандидат должен подтвердить время встречи.', 409)
            else:
                meeting = self.interview(db, cid)
                require(meeting and meeting['status'] in ('booked', 'live'), 'Сначала запишитесь к рекрутеру.', 409)
            require(timestamp(meeting['start']) - dt.timedelta(minutes=15) <= now() <= timestamp(meeting['end']) + dt.timedelta(minutes=90),
                    'Комната откроется за 15 минут до встречи.', 409)
            mid = meeting['id']
        with self.media_operation(mid):
            return self._join_room(cid, application_id, owner, mid, person, consent, guest_token,
                                   owner_name, owner_id, access_check)

    def _join_room(self, cid, application_id, owner, mid, person, consent, guest_token,
                   owner_name=None, owner_id='recruiter', access_check=None):
        table = 'cp_meetings' if application_id else 'cp_interviews'
        token_expires = int(time.time()) + 3600
        with self.lock, self.connect() as db:
            if access_check:
                access_check(db)
            if application_id:
                self.require_application_active(db, self.application(db, application_id, cid))
            current = db.execute(f'SELECT status FROM {table} WHERE id=?', (mid,)).fetchone()
            allowed = ('confirmed', 'live') if application_id else ('booked', 'live')
            require(current and current[0] in allowed, 'Интервью уже завершено.', 409)
            if guest_token:
                capability = invites.resolve(db, guest_token, 'call')
                require(capability['candidateId'] == cid and capability['interviewId'] == mid, 'Ссылка недействительна.', 404)
                token_expires = min(token_expires, capability['expires'])
            prepared_intro = not application_id and invites.intake(db, cid) is not None
            can_record = not prepared_intro or invites.recording(db, mid)['startRequested']
        room = self.media.room(mid, recordable=can_record)
        if access_check:
            with self.connect() as db:
                access_check(db)
        token = self.media.request('/meeting-tokens', {'properties': {
            'room_name': room['name'], 'user_name': (owner_name or 'Рекрутер HR Vision') if owner else (person['name'] or 'Кандидат'),
            'user_id': owner_id if owner else cid, 'is_owner': bool(owner and can_record),
            'permissions': {'canAdmin': bool(owner and can_record)},
            'enable_recording_ui': bool(owner and can_record),
            'enable_live_captions_ui': bool(owner and can_record),
            # Candidates and guests use our device preview; owners keep the room setting.
            **({'enable_prejoin_ui': False} if not owner else {}),
            'exp': token_expires, 'eject_at_token_exp': True,
            'start_cloud_recording': False, 'auto_start_transcription': False}})
        require(isinstance(token.get('token'), str) and token['token'], 'Видеосервис не выдал доступ.', 502)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if access_check:
                access_check(db)
            table = 'cp_meetings' if application_id else 'cp_interviews'
            current = db.execute(f'SELECT status FROM {table} WHERE id=?', (mid,)).fetchone()
            require(current and current[0] in allowed, 'Встреча изменилась или завершена.', 409)
            if application_id:
                application = self.application(db, application_id, cid)
                self._application_open_for_meeting(db, application)
                require(application['status'] == 'accepted' and application['meeting']['id'] == mid,
                        'Встреча изменилась. Обновите кабинет.', 409)
            if guest_token:
                # Rotation/revocation during provider I/O must not issue access.
                invites.resolve(db, guest_token, 'call')
            # Candidate prejoin alone cannot mark a human interview as live.
            db.execute(f'UPDATE {table} SET room=?,status=? WHERE id=?',
                       (dump(room), 'live' if owner else current[0], mid))
            if not application_id:
                # A new explicitly controlled interview is distinguishable from
                # legacy calls whose capture request state was not persisted.
                db.execute('INSERT OR IGNORE INTO cp_recording_control VALUES (?,NULL,NULL,NULL,0,0)', (mid,))
            if consent is True and not owner and not guest_token:
                db.execute('INSERT OR IGNORE INTO cp_media_consents VALUES (?,?,?,?)',
                           (mid, cid, iso(now()), 'recording-v1'))
        return {'url': room['url'], 'token': token['token']}

    def start_interview(self, cid, oral_consent, *, retry_recording=False, retry_transcription=False, access_check=None):
        require(oral_consent is True, 'Сначала подтвердите полученное устное согласие.', 400)
        require(type(retry_recording) is bool and type(retry_transcription) is bool, 'Проверьте команду запуска записи.', 400)
        with self.connect() as db:
            if access_check:
                access_check(db)
            meeting = self.interview(db, cid)
            require(meeting and meeting['status'] == 'live' and meeting['room'], 'Сначала войдите в комнату интервью.', 409)
            mid = meeting['id']
        with self.media_operation(mid):
            with self.lock, self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if access_check:
                    access_check(db)
                meeting = self.interview(db, cid)
                require(meeting and meeting['status'] == 'live', 'Интервью уже завершено.', 409)
                person = self.person(db, cid)
                require(invites.intake(db, cid) or person['consentReuse'], 'Кандидат не дал согласие на интервью.', 409)
                stamp = iso(now())
                db.execute('''INSERT INTO cp_recording_control VALUES (?,?,?, ?,0,0)
                  ON CONFLICT(interview_id) DO UPDATE SET
                  consent_received_at=COALESCE(consent_received_at,excluded.consent_received_at),
                  consent_source=COALESCE(consent_source,excluded.consent_source),
                  start_requested_at=COALESCE(start_requested_at,excluded.start_requested_at)''',
                           (mid, stamp, 'recruiter_oral', stamp))
                if retry_recording:
                    db.execute('UPDATE cp_recording_control SET recording_requested=0 WHERE interview_id=?', (mid,))
                if retry_transcription:
                    db.execute('UPDATE cp_recording_control SET transcription_requested=0 WHERE interview_id=?', (mid,))
                control = invites.recording(db, mid)
                room_name = meeting['room']['name']
            if control['recordingRequested'] and control['transcriptionRequested']:
                return
            # REST acknowledges a requested start; only Daily participant events
            # prove recording has started. Persist partial progress for safe retry.
            room_path = '/rooms/' + urllib.parse.quote(room_name, safe='')
            self.media.request(room_path, {'properties': {'enable_recording': 'cloud',
                               'enable_transcription_storage': True, 'enable_live_captions_ui': True}})
            with self.connect() as db:
                if access_check:
                    access_check(db)
            if not control['recordingRequested']:
                self.media.request(room_path + '/recordings/start', {'type': 'cloud',
                                   'instanceId': str(uuid.uuid5(uuid.NAMESPACE_URL, 'hr-vision:' + mid)), 'maxDuration': 3600})
                with self.lock, self.connect() as db:
                    if access_check:
                        access_check(db)
                    db.execute('UPDATE cp_recording_control SET recording_requested=1 WHERE interview_id=?', (mid,))
            if not control['transcriptionRequested']:
                self.media.request(room_path + '/transcription/start', {'language': 'ru', 'model': 'nova-2'})
                with self.lock, self.connect() as db:
                    if access_check:
                        access_check(db)
                    db.execute('UPDATE cp_recording_control SET transcription_requested=1 WHERE interview_id=?', (mid,))

    def complete(self, cid, application_id=None, meeting_id=None, *, access_check=None):
        with self.lock, self.connect() as db:
            if access_check:
                access_check(db)
            self.person(db, cid)
            if application_id:
                self.require_application_active(db, self.application(db, application_id, cid))
                meeting = self.meeting(db, application_id, meeting_id)
            else:
                meeting = self.interview(db, cid)
            require(meeting is not None, 'Интервью не найдено.', 404)
            if meeting['status'] == 'completed':
                return
            require(meeting['status'] == 'live' and meeting.get('room'), 'Сначала проведите интервью на платформе.', 409)
            mid, room = meeting['id'], meeting['room']
        with self.media_operation(mid):
            return self._complete_room(cid, application_id, mid, access_check)

    def _complete_room(self, cid, application_id, mid, access_check=None):
        with self.lock, self.connect() as db:
            if access_check:
                access_check(db)
            table = 'cp_meetings' if application_id else 'cp_interviews'
            row = db.execute(f'SELECT status,room FROM {table} WHERE id=?', (mid,)).fetchone()
            require(row, 'Интервью не найдено.', 404)
            if row[0] == 'completed':
                return
            require(row[0] == 'live' and row[1], 'Сначала проведите интервью на платформе.', 409)
            room = json.loads(row[1])
            if not application_id:
                if invites.recording(db, mid)['startRequested']:
                    room = {**room, 'recordingInstanceId': str(uuid.uuid5(uuid.NAMESPACE_URL, 'hr-vision:' + mid))}
                elif invites.intake(db, cid):
                    # This server-controlled intro had recording disabled and
                    # non-owner participants. An unstated 400 from stop is not
                    # evidence of "already stopped"; do not send capture-stop
                    # for the one mode known never to have enabled capture.
                    room = {**room, 'captureNeverStarted': True}
        self.media.finish(room)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if access_check:
                access_check(db)
            table = 'cp_meetings' if application_id else 'cp_interviews'
            changed = db.execute(f"UPDATE {table} SET status='completed',completed_at=? WHERE id=? AND status='live'",
                                 (iso(now()), mid)).rowcount
            if changed and not application_id:
                self.event(db, cid, 'interview.completed', mid)

    def materials(self, cid, application_id=None, meeting_id=None, *, access_check=None):
        kind = 'meeting' if application_id else 'interview'
        with self.connect() as db:
            if access_check:
                access_check(db)
            self.person(db, cid)
            if application_id:
                self.application(db, application_id, cid)
                meeting = self.meeting(db, application_id, meeting_id)
            else:
                meeting = self.interview(db, cid)
            require(meeting and meeting['status'] == 'completed', 'Материалы доступны после встречи.', 409)
            require(meeting_id is None or meeting['id'] == meeting_id, 'Встреча изменилась. Обновите кабинет.', 409)
            mid = meeting['id']
        with self.media_operation(mid):
            with self.connect() as db:
                if access_check:
                    access_check(db)
                if application_id:
                    self.application(db, application_id, cid)
                current = self.meeting(db, application_id, mid) if application_id else self.interview(db, cid)
                require(current and current['id'] == mid and current['status'] == 'completed',
                        'Встреча изменилась. Обновите кабинет.', 409)
                room = current['room']
                require(room, 'Комната встречи не найдена.', 409)
                room_key = dump({'id': room['id'], 'name': room['name']})
                cached = db.execute('SELECT body FROM cp_media_state WHERE meeting_id=? AND kind=? AND room_id=?',
                                    (mid, kind, room_key)).fetchone()
                known = json.loads(cached[0]) if cached else {}
                control = db.execute('SELECT start_requested_at FROM cp_recording_control WHERE interview_id=?',
                                     (mid,)).fetchone() if not application_id else None
                never_requested = (control is not None and control[0] is None
                                   and invites.intake(db, cid) is not None)
                finished = timestamp(current['completedAt']) if current.get('completedAt') else None
                waiting = bool(finished and (now() - finished).total_seconds() < MEDIA_WAIT_SECONDS)
            # No SQLite transaction or shared state lock spans provider I/O.
            result = self.media.materials(room, known)
            for channel in ('video', 'transcription'):
                state = result[channel]
                if state['status'] == 'absent':
                    state['status'] = 'not_recorded' if never_requested else 'processing' if waiting else 'error'
                    if state['status'] == 'error':
                        state['error'] = ('Видеосервис пока не вернул файл. Это не подтверждает отсутствие записи. '
                                          'Повторите проверку позже или обратитесь к команде.')
                elif state['status'] == 'processing' and not waiting:
                    state['status'] = 'error'
                    state['error'] = 'Обработка заняла больше времени, чем ожидалось. Попробуйте получить материалы ещё раз.'
            statuses = {result[channel]['status'] for channel in ('video', 'transcription')}
            result['status'] = ('ready' if 'ready' in statuses else 'processing' if 'processing' in statuses
                                else 'error' if 'error' in statuses else 'not_recorded')
            # Persist only exact-room IDs, finite states and check time. Signed
            # links and transcript content are requested afresh for each viewer.
            durations = {item['id']: item.get('duration') for item in result['recordings']}
            metadata = {'recordings': [{'id': rid, 'duration': durations.get(rid)} for rid in result['video']['ids']],
                        'transcripts': [{'id': rid} for rid in result['transcription']['ids']],
                        'video': result['video'], 'transcription': result['transcription'],
                        'status': result['status'], 'checkedAt': result['checkedAt']}
            with self.lock, self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if access_check:
                    access_check(db)
                if application_id:
                    self.application(db, application_id, cid)
                latest = self.meeting(db, application_id, mid) if application_id else self.interview(db, cid)
                require(latest and latest['id'] == mid and latest['status'] == 'completed' and latest['room'] == room,
                        'Встреча изменилась. Обновите кабинет.', 409)
                db.execute('''INSERT INTO cp_media_state VALUES (?,?,?,?,?) ON CONFLICT(meeting_id)
                  DO UPDATE SET kind=excluded.kind,room_id=excluded.room_id,body=excluded.body,updated_at=excluded.updated_at''',
                           (mid, kind, room_key, dump(metadata), result['checkedAt']))
            return result

    def handle(self, method, raw_path, headers, body, ip, manager_actor, *, team_grant=False):
        path = urllib.parse.urlsplit(raw_path).path
        require(self.handles(path), 'Не найдено.', 404)
        require(method in ('GET', 'POST'), 'Метод не поддерживается.', 405)
        if path in ('/api/hr/recruiter/candidate-access/state', '/api/hr/recruiter/candidate-access/send'):
            require(method == 'POST', 'Нужен POST-запрос.', 405)
        if method == 'POST':
            require((headers.get('Origin') is None or headers.get('Origin') in self.origins) and
                    headers.get('Sec-Fetch-Site') != 'cross-site',
                    'Другой источник запроса.', 403)
            require(headers.get('Content-Type', '').split(';', 1)[0].strip() == 'application/json', 'Нужен JSON.', 415)
            require(isinstance(body, dict), 'Некорректный запрос.', 400)
        if path.startswith('/api/hr/recruiter/auth/') or path == '/api/hr/recruiter/accounts':
            return self.recruiter_auth(method, path, headers, body, ip, manager_actor, team_grant)
        auth = path.rsplit('/', 1)[-1]
        is_recruiter = path.startswith('/api/hr/recruiter/')
        principal = None
        if is_recruiter:
            with self.connect() as db:
                principal = self.recruiter_principal(db, headers, manager_actor)
            if not principal['legacy']:
                require(path in {
                    '/api/hr/recruiter/state', '/api/hr/recruiter/slots', '/api/hr/recruiter/availability',
                    '/api/hr/recruiter/availability/date',
                    '/api/hr/recruiter/candidates', '/api/hr/recruiter/interview/book',
                    '/api/hr/recruiter/invitations', '/api/hr/recruiter/invitations/revoke',
                    '/api/hr/recruiter/profile', '/api/hr/recruiter/interview/join',
                    '/api/hr/recruiter/interview/complete', '/api/hr/recruiter/interview/start',
                    '/api/hr/recruiter/interview/materials', '/api/hr/recruiter/notifications',
                    '/api/hr/recruiter/notifications/email/request', '/api/hr/recruiter/notifications/email/verify',
                    '/api/hr/recruiter/notifications/telegram/link', '/api/hr/recruiter/notifications/telegram/disconnect',
                    '/api/hr/recruiter/candidate-access/state', '/api/hr/recruiter/candidate-access/send'},
                    'Доступно только команде HR Vision.', 403)
        capability_paths = ('/api/hr/candidate/guest/state', '/api/hr/candidate/guest/join',
                            '/api/hr/candidate/claim/state', '/api/hr/candidate/claim/resume', '/api/hr/candidate/claim')
        if path in capability_paths:
            require(method == 'POST', 'Откройте персональную ссылку.', 405)
            return self.capability_request(path, body, ip)
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
                    principal = self.recruiter_principal(db, headers, manager_actor)
                    rate_identity = principal['id']
                else:
                    rate_identity = self.session_candidate(db, headers)
            self.rate_limit('write-account:' + rate_identity, 240, 60)
        response_headers = {}
        provider_action = False
        parsed_resume = None
        if method == 'POST' and path in ('/api/hr/candidate/resume', '/api/hr/recruiter/candidates'):
            # Identity was checked above. Parsing must not block unrelated SQLite writes.
            self.rate_limit('resume-account:' + rate_identity, 12, 3600)
            if is_recruiter:
                require(set(body) <= {'filename', 'contentBase64', 'contactEmail'}, 'Проверьте поля резюме.', 400)
            parsed_resume = parse_upload({key: body[key] for key in ('filename', 'contentBase64') if key in body}
                                         if is_recruiter else body)
            def authorize_resume(db):
                actor = self.recruiter_principal(db, headers, manager_actor)['id'] if is_recruiter else self.session_candidate(db, headers)
                require(actor == rate_identity, 'Войдите снова, чтобы загрузить резюме.', 401)
            with self.connect() as db:
                authorize_resume(db)
                if not is_recruiter:
                    parsed_resume['_profileBefore'] = self.person(db, rate_identity)
            parsed_resume = self.resume_model.generate(parsed_resume, rate_identity, authorize_resume)

        with self.lock, self.connect() as db:
            if method == 'POST':
                db.execute('BEGIN IMMEDIATE')
            if is_recruiter:
                principal = self.recruiter_principal(db, headers, manager_actor)
                cid = body.get('candidateId') if body else None
                if path in {
                    '/api/hr/recruiter/interview/book', '/api/hr/recruiter/invitations',
                    '/api/hr/recruiter/invitations/revoke', '/api/hr/recruiter/profile',
                    '/api/hr/recruiter/interview/join', '/api/hr/recruiter/interview/start',
                    '/api/hr/recruiter/interview/complete', '/api/hr/recruiter/interview/materials',
                    '/api/hr/recruiter/candidate-access/state', '/api/hr/recruiter/candidate-access/send'}:
                    self.recruiter_candidate(db, principal, cid,
                        allow_prepared=path in ('/api/hr/recruiter/interview/book', '/api/hr/recruiter/invitations',
                                                '/api/hr/recruiter/invitations/revoke', '/api/hr/recruiter/profile'))
            else:
                cid = self.session_candidate(db, headers, required=path not in (
                    '/api/hr/candidate/session', '/api/hr/candidate/register', '/api/hr/candidate/login'))
            if method == 'GET':
                if path == '/api/hr/candidate/session':
                    return {'authenticated': bool(cid), 'candidate': self.candidate_state(db, cid) if cid else None,
                            'videoConfigured': self.media.configured, 'channels': self.channels()}, {}
                if path == '/api/hr/candidate/state':
                    return self.candidate_state(db, cid), {}
                if path == '/api/hr/candidate/resume':
                    return self.download_resume(db, cid), {}
                if path == '/api/hr/candidate/slots':
                    return {'slots': self.booking_slots(db)}, {}
                if path == '/api/hr/candidate/notifications':
                    return self.channels(db, cid), {}
                if path == '/api/hr/recruiter/state':
                    return self.recruiter_state(db, principal), {}
                if path == '/api/hr/recruiter/notifications':
                    require(not principal['legacy'], 'Войдите в свой кабинет рекрутера.', 401)
                    return recruiter_notifications.public_status(db, principal['id']), {}
                raise Problem('Не найдено.', 404)

            if parsed_resume and parsed_resume.get('runId'):
                self.resume_model.assert_current(db, rate_identity, parsed_resume['runId'])

            if path in ('/api/hr/recruiter/candidate-access/state', '/api/hr/recruiter/candidate-access/send'):
                require(self.candidate_access is not None, 'Выдача доступа временно недоступна.', 503)
                allowed = {'candidateId'} if path.endswith('/state') else {'candidateId', 'requestId'}
                require(set(body) <= allowed, 'Проверьте поля запроса.', 400)
                if path.endswith('/state'):
                    return self.candidate_access.state(db, cid, principal=principal), {'Cache-Control': 'no-store'}
                return self.candidate_access.request(db, principal, cid,
                    {key: value for key, value in body.items() if key != 'candidateId'}), {'Cache-Control': 'no-store'}
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
            elif path == '/api/hr/candidate/search':
                self.set_searching(db, cid, body)
            elif path == '/api/hr/candidate/resume':
                self.save_resume(db, cid, parsed_resume)
            elif path == '/api/hr/candidate/book':
                self.book(db, cid, body, pooled=True)
            elif path == '/api/hr/candidate/application/decision':
                self.decide_application(db, cid, body)
            elif path == '/api/hr/candidate/meeting/confirm':
                self.confirm_meeting(db, cid, body)
            elif path == '/api/hr/candidate/meeting/decline':
                self.decline_meeting(db, cid, body)
            elif path == '/api/hr/candidate/workflow':
                result = employer_workflow.candidate_action(db, cid, body.get('applicationId'), body.get('action'), body)
                self.workflow_events(db, result['events'])
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
                self.add_slot(db, body, principal=principal)
            elif path == '/api/hr/recruiter/availability':
                availability.add_block(db, body, recruiter=principal if not principal['legacy'] else None)
            elif path == '/api/hr/recruiter/availability/date':
                availability.set_date_override(db, body, recruiter=principal)
            elif path in ('/api/hr/recruiter/notifications/email/request', '/api/hr/recruiter/notifications/email/verify',
                          '/api/hr/recruiter/notifications/telegram/link', '/api/hr/recruiter/notifications/telegram/disconnect'):
                require(not principal['legacy'], 'Войдите в свой кабинет рекрутера.', 401)
                if path.endswith('/email/request'):
                    require(not body, 'Проверьте поля запроса.', 400)
                    return recruiter_notifications.request_email_verification(db, principal['id']), {}
                if path.endswith('/email/verify'):
                    require(set(body) == {'token'}, 'Проверьте поля подтверждения.', 400)
                    return recruiter_notifications.verify_email(db, principal['id'], body.get('token')), {}
                require(not body, 'Проверьте поля запроса.', 400)
                if path.endswith('/telegram/disconnect'):
                    return recruiter_notifications.disconnect_telegram(db, principal['id']), {}
                return recruiter_notifications.create_telegram_link(db, principal['id']), {}
            elif path == '/api/hr/recruiter/candidates':
                cid = self.create_prepared(db, parsed_resume, body.get('contactEmail', ''))
                if not principal['legacy']:
                    db.execute('INSERT INTO cp_recruiter_prepared_owners VALUES (?,?)', (cid, principal['id']))
                return {'candidate': self.candidate_state(db, cid, internal=True, recruiter_view=not principal['legacy']),
                        'state': self.recruiter_state(db, principal)}, {}
            elif path == '/api/hr/recruiter/interview/book':
                self.recruiter_slot(db, principal, body.get('slotId'))
                self.book(db, cid, body, prepared=True)
            elif path == '/api/hr/recruiter/invitations':
                replace = body.get('replace', False)
                require(type(replace) is bool, 'Проверьте команду замены ссылки.', 400)
                self.person(db, cid)
                require(body.get('scope') in ('call', 'claim'), 'Выберите назначение ссылки.', 400)
                if not replace:
                    for lookup in (self.booking_invitation_lookup, self.invitation_lookup):
                        existing = lookup(db, cid, body.get('scope')) if lookup else None
                        if existing:
                            return existing, {}
                invitation = invites.issue(db, cid, body.get('scope'), self.origin)
                if self.booking_invitation_store:
                    self.booking_invitation_store(db, cid, body.get('scope'), invitation)
                return invitation, {}
            elif path == '/api/hr/recruiter/invitations/revoke':
                self.person(db, cid)
                invites.revoke(db, cid, body.get('scope'))
            elif path == '/api/hr/recruiter/vacancies':
                self.add_vacancy(db, body)
            elif path == '/api/hr/recruiter/applications':
                self.propose_application(db, body)
            elif path == '/api/hr/recruiter/meetings':
                self.propose_meeting(db, body)
            elif path == '/api/hr/recruiter/meeting/reschedule':
                self.reschedule_meeting(db, body)
            elif path == '/api/hr/recruiter/meeting/cancel':
                self.cancel_meeting(db, body)
            elif path == '/api/hr/recruiter/profile':
                self.person(db, cid)
                profile = {'summary': text(body.get('summary', ''), 12000, required=False),
                           'evaluation': text(body.get('evaluation', ''), 12000, required=False), 'updatedAt': iso(now())}
                db.execute('INSERT INTO cp_internal_profiles VALUES (?,?) ON CONFLICT(candidate_id) DO UPDATE SET body=excluded.body',
                           (cid, dump(profile)))
            elif path in ('/api/hr/candidate/interview/join', '/api/hr/candidate/meeting/join',
                          '/api/hr/recruiter/interview/join', '/api/hr/recruiter/meeting/join',
                          '/api/hr/recruiter/interview/complete', '/api/hr/recruiter/meeting/complete',
                          '/api/hr/recruiter/interview/materials', '/api/hr/recruiter/meeting/materials',
                          '/api/hr/candidate/interview/materials', '/api/hr/candidate/meeting/materials',
                          '/api/hr/recruiter/interview/start'):
                if '/meeting/' in path:
                    application = self.application(db, body.get('applicationId'), None if is_recruiter else cid)
                    require(not is_recruiter or cid is None or cid == application['candidateId'], 'Кандидат не найден.', 404)
                    cid = application['candidateId']
                    if not is_recruiter or path.endswith('/materials') or 'meetingId' in body:
                        exact = self.meeting(db, application['id'], body.get('meetingId')) if path.endswith('/materials') else application['meeting']
                        require(exact and exact['id'] == body.get('meetingId'),
                                'Встреча изменилась. Обновите кабинет.', 409)
                        self._meeting_revision(exact, body)
                else:
                    self.person(db, cid)
                # The external provider work below does not hold the state lock.
                provider_action = True
            else:
                raise Problem('Не найдено.', 404)
            if not provider_action:
                return (self.recruiter_state(db, principal) if is_recruiter else self.candidate_state(db, cid)), response_headers

        def provider_access(db):
            if is_recruiter:
                current_principal = self.recruiter_principal(db, headers, manager_actor)
                require(current_principal['id'] == principal['id'], 'Войдите в кабинет рекрутера.', 401)
                self.recruiter_candidate(db, current_principal, cid)
            else:
                require(self.session_candidate(db, headers) == cid, 'Войдите в свой кабинет.', 401)
            if '/meeting/' in path:
                application = self.application(db, body.get('applicationId'), cid)
                if not path.endswith('/materials'):
                    self.require_application_active(db, application)

        if path.endswith('/join'):
            return self.join(cid, body.get('applicationId') if '/meeting/' in path else None,
                             body.get('consent'), owner=is_recruiter, meeting_id=body.get('meetingId'),
                             owner_name=principal['name'] if principal and not principal['legacy'] else None,
                             owner_id=principal['id'] if principal and not principal['legacy'] else 'recruiter',
                             access_check=provider_access), {}
        if path.endswith('/materials'):
            return self.materials(cid, body.get('applicationId') if '/meeting/' in path else None,
                                  body.get('meetingId'), access_check=provider_access), {}
        if path.endswith('/start'):
            self.start_interview(cid, body.get('oralConsent'), retry_recording=body.get('retryRecording', False),
                                 retry_transcription=body.get('retryTranscription', False), access_check=provider_access)
            with self.connect() as db:
                provider_access(db)
                return self.recruiter_state(db, principal), {}
        self.complete(cid, body.get('applicationId') if '/meeting/' in path else None, body.get('meetingId'),
                      access_check=provider_access)
        with self.connect() as db:
            provider_access(db)
            return self.recruiter_state(db, principal), {}
