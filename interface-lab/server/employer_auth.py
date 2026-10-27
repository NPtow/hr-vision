"""Company accounts for employers, separate from candidates and recruiter grants.

Every employee has their own credentials and the same company permissions. A new
registration creates a new company; only a single-use invitation can add a member
to an existing one. No company is inferred from an email domain or display name.
"""
import hashlib
from http.cookies import SimpleCookie
import re
import secrets
import time
import urllib.parse

from candidate_portal import email_value, password_hash, password_matches, uid
from domain import Problem, require, text, iso, now


COOKIE = '__Host-hr_employer'
SESSION_SECONDS = 30 * 86400
INVITE_SECONDS = 86400
PREFIX = '/api/hr/employer/'
AUTH_PATHS = {'session', 'register', 'login', 'logout', 'team', 'team/invite',
              'team/join', 'team/join/preview'}


def token_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def opaque_token(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{40,100}', value) is not None


def cookie_token(headers):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get('Cookie', ''))
        value = cookie[COOKIE].value if COOKIE in cookie else ''
    except Exception:
        return ''
    return value if opaque_token(value) else ''


class EmployerAuth:
    def __init__(self, connect, lock, origin):
        self.connect, self.lock, self.origin = connect, lock, origin.rstrip('/')
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))
        with self.connect() as db:
            self.init(db)

    @staticmethod
    def init(db):
        db.executescript('''
        CREATE TABLE IF NOT EXISTS ep_companies (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ep_members (
          id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES ep_companies(id),
          email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ep_sessions (
          token_hash TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES ep_members(id),
          expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS ep_invites (
          token_hash TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES ep_companies(id),
          created_by TEXT NOT NULL REFERENCES ep_members(id), expires INTEGER NOT NULL,
          used_by TEXT REFERENCES ep_members(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ep_auth_rate_limits (
          key TEXT PRIMARY KEY, count INTEGER NOT NULL, reset_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS ep_members_company ON ep_members(company_id);
        CREATE INDEX IF NOT EXISTS ep_invites_company ON ep_invites(company_id);
        ''')

    @staticmethod
    def handles(raw_path):
        path = urllib.parse.urlsplit(raw_path).path
        return path.startswith(PREFIX) and path[len(PREFIX):] in AUTH_PATHS

    @staticmethod
    def identity(db, member_id):
        row = db.execute('''SELECT m.id,m.company_id,m.name,m.email,c.name
          FROM ep_members m JOIN ep_companies c ON c.id=m.company_id WHERE m.id=?''',
                         (member_id,)).fetchone()
        require(row is not None, 'Войдите в кабинет работодателя.', 401)
        return {'memberId': row[0], 'companyId': row[1], 'name': row[2], 'email': row[3],
                'company': {'id': row[1], 'name': row[4]}}

    def session(self, db, headers, required=True):
        token = cookie_token(headers)
        row = db.execute('SELECT member_id FROM ep_sessions WHERE token_hash=? AND expires>?',
                         (token_digest(token), int(time.time()))).fetchone() if token else None
        require(row is not None or not required, 'Войдите в кабинет работодателя.', 401)
        return self.identity(db, row[0]) if row else None

    def session_cookie(self, db, member_id, headers):
        self.clear_session(db, headers)
        token, tick = secrets.token_urlsafe(32), int(time.time())
        db.execute('DELETE FROM ep_sessions WHERE expires<=?', (tick,))
        db.execute('INSERT INTO ep_sessions VALUES (?,?,?)',
                   (token_digest(token), member_id, tick + SESSION_SECONDS))
        return f'{COOKIE}={token}; Path=/; Max-Age={SESSION_SECONDS}; Secure; HttpOnly; SameSite=Lax'

    @staticmethod
    def clear_session(db, headers):
        token = cookie_token(headers)
        if token:
            db.execute('DELETE FROM ep_sessions WHERE token_hash=?', (token_digest(token),))
        return f'{COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax'

    def rate_limit(self, key, limit, seconds):
        # A rejected login must still consume its attempt, independently of the
        # later request transaction. Persist only hashes, not attempted emails.
        key, tick = token_digest(key), int(time.time())
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM ep_auth_rate_limits WHERE reset_at<=?', (tick,))
            row = db.execute('SELECT count FROM ep_auth_rate_limits WHERE key=?', (key,)).fetchone()
            require(not row or row[0] < limit, 'Слишком много попыток. Попробуйте позже.', 429)
            if row:
                db.execute('UPDATE ep_auth_rate_limits SET count=count+1 WHERE key=?', (key,))
            else:
                db.execute('INSERT INTO ep_auth_rate_limits VALUES (?,?,?)', (key, 1, tick + seconds))

    def register(self, db, body):
        require(set(body) == {'companyName', 'name', 'email', 'password'}, 'Проверьте поля регистрации.', 400)
        company_name, name = text(body.get('companyName'), 200), text(body.get('name'), 140)
        email = email_value(body.get('email'))
        require(db.execute('SELECT 1 FROM ep_members WHERE email=?', (email,)).fetchone() is None,
                'Не удалось создать аккаунт. Если вы уже регистрировались, войдите.', 409)
        hashed = password_hash(body.get('password'))
        company_id, member_id, created = uid('company'), uid('member'), iso(now())
        db.execute('INSERT INTO ep_companies VALUES (?,?,?)', (company_id, company_name, created))
        db.execute('INSERT INTO ep_members VALUES (?,?,?,?,?,?)',
                   (member_id, company_id, email, name, hashed, created))
        return member_id

    def login(self, db, body):
        require(set(body) == {'email', 'password'}, 'Проверьте почту и пароль.', 400)
        email = email_value(body.get('email'))
        row = db.execute('SELECT id,password_hash FROM ep_members WHERE email=?', (email,)).fetchone()
        valid = password_matches(body.get('password'), row[1] if row else self.dummy_hash)
        require(row and valid, 'Неверная почта или пароль.', 401)
        return row[0]

    def invite(self, db, identity):
        token, tick = secrets.token_urlsafe(32), int(time.time())
        db.execute('DELETE FROM ep_invites WHERE expires<=?', (tick,))
        db.execute('INSERT INTO ep_invites VALUES (?,?,?,?,NULL,?)',
                   (token_digest(token), identity['companyId'], identity['memberId'],
                    tick + INVITE_SECONDS, iso(now())))
        # Fragments never travel in HTTP access logs or Referer headers.
        url = (self.origin + '/iframe.html?id=hr-vision-product--manager&viewMode=story'
               '&workspace=employer#employer-invite=' + token)
        return {'url': url, 'expiresAt': tick + INVITE_SECONDS}

    @staticmethod
    def invitation(db, token):
        require(opaque_token(token), 'Приглашение недействительно или уже использовано.', 410)
        row = db.execute('''SELECT i.company_id,c.name,i.expires,i.used_by FROM ep_invites i
          JOIN ep_companies c ON c.id=i.company_id WHERE i.token_hash=?''', (token_digest(token),)).fetchone()
        require(row and row[2] > int(time.time()) and row[3] is None,
                'Приглашение недействительно или уже использовано. Попросите новую ссылку.', 410)
        return {'company': {'id': row[0], 'name': row[1]}, 'expiresAt': row[2]}

    def join(self, db, body):
        require(set(body) == {'token', 'name', 'email', 'password'}, 'Проверьте поля приглашения.', 400)
        invitation = self.invitation(db, body.get('token'))
        name, email = text(body.get('name'), 140), email_value(body.get('email'))
        row = db.execute('SELECT id,company_id,password_hash FROM ep_members WHERE email=?', (email,)).fetchone()
        if row:
            valid = password_matches(body.get('password'), row[2])
            require(valid, 'Для этой почты уже есть аккаунт. Укажите его пароль.', 401)
            require(row[1] == invitation['company']['id'],
                    'Эта почта уже связана с другим кабинетом компании.', 409)
            member_id = row[0]
        else:
            hashed = password_hash(body.get('password'))
            member_id = uid('member')
            db.execute('INSERT INTO ep_members VALUES (?,?,?,?,?,?)',
                       (member_id, invitation['company']['id'], email, name, hashed, iso(now())))
        changed = db.execute('UPDATE ep_invites SET used_by=? WHERE token_hash=? AND used_by IS NULL AND expires>?',
                             (member_id, token_digest(body['token']), int(time.time()))).rowcount
        require(changed == 1, 'Приглашение уже использовано. Попросите новую ссылку.', 410)
        return member_id

    @staticmethod
    def team(db, identity):
        members = [{'id': row[0], 'name': row[1], 'email': row[2], 'joinedAt': row[3]}
                   for row in db.execute('SELECT id,name,email,created_at FROM ep_members WHERE company_id=? ORDER BY created_at,id',
                                         (identity['companyId'],))]
        return {'company': identity['company'], 'members': members}

    def handle(self, method, raw_path, headers, body, ip):
        path = urllib.parse.urlsplit(raw_path).path
        require(self.handles(path), 'Не найдено.', 404)
        action = path[len(PREFIX):]
        require(method in ('GET', 'POST'), 'Метод не поддерживается.', 405)
        require((method == 'GET') == (action in ('session', 'team')), 'Метод не поддерживается.', 405)
        if method == 'POST':
            require(headers.get('Origin') in (None, self.origin) and headers.get('Sec-Fetch-Site') != 'cross-site',
                    'Другой источник запроса.', 403)
            require(headers.get('Content-Type', '').split(';', 1)[0].strip() == 'application/json', 'Нужен JSON.', 415)
            require(isinstance(body, dict), 'Некорректный запрос.', 400)
            if action in ('register', 'login', 'team/join', 'team/join/preview'):
                self.rate_limit('public-ip:' + ip, 40, 900)
                if action != 'team/join/preview':
                    email_key = str(body.get('email', '')).strip().casefold()[:254]
                    self.rate_limit(action + '-email:' + email_key, 5 if action == 'register' else 15,
                                    3600 if action == 'register' else 900)
            elif action != 'logout':
                with self.connect() as db:
                    identity = self.session(db, headers)
                self.rate_limit('member:' + identity['memberId'], 60, 60)
                if action == 'team/invite':
                    self.rate_limit('company-invites:' + identity['companyId'], 30, 86400)

        with self.lock, self.connect() as db:
            if method == 'POST':
                db.execute('BEGIN IMMEDIATE')
            if action == 'session':
                identity = self.session(db, headers, required=False)
                return {'authenticated': bool(identity), 'identity': identity}, {}
            if action == 'team':
                return self.team(db, self.session(db, headers)), {}
            if action == 'logout':
                require(not body, 'Некорректный запрос.', 400)
                return {'ok': True}, {'Set-Cookie': self.clear_session(db, headers)}
            if action == 'team/invite':
                require(not body, 'Некорректный запрос.', 400)
                return self.invite(db, self.session(db, headers)), {}
            if action == 'team/join/preview':
                require(set(body) == {'token'}, 'Проверьте приглашение.', 400)
                return self.invitation(db, body.get('token')), {}
            member_id = (self.register(db, body) if action == 'register' else
                         self.login(db, body) if action == 'login' else self.join(db, body))
            cookie = self.session_cookie(db, member_id, headers)
            return {'authenticated': True, 'identity': self.identity(db, member_id)}, {'Set-Cookie': cookie}
