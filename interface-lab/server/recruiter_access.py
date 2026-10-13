"""Personal recruiter identities, isolated from candidate and employer authority.

Registration must be authorized by the HTTP adapter's verified HR Vision grant
or an existing manager session. An account is not itself team-wide data access:
interview/schedule ownership is checked separately using its stable recruiter ID.
Transactions belong to the caller; credential failures must not roll back the
separate, committed consume_auth_limits transaction.
"""
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import re
import secrets
import sqlite3
import time
import urllib.parse

from domain import Problem, require, text, iso, now
from employer_access import _atomic, _email, _password_hash, _password_matches, _DUMMY_HASH


COOKIE = '__Host-hr_recruiter'
SESSION_SECONDS = 30 * 86400
TOKEN_PATTERN = re.compile(r'rc_[A-Za-z0-9_-]{43}')
ACTIVATION_PATTERN = re.compile(r'ra_[A-Za-z0-9_-]{43}')


def init(db):
    with _atomic(db):
        db.execute('''CREATE TABLE IF NOT EXISTS rc_accounts (
            id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS rc_sessions (
            token_hash TEXT PRIMARY KEY,
            recruiter_id TEXT NOT NULL REFERENCES rc_accounts(id) ON DELETE CASCADE,
            created_at INTEGER NOT NULL, expires INTEGER NOT NULL CHECK(expires > created_at))''')
        db.execute('CREATE INDEX IF NOT EXISTS rc_session_account ON rc_sessions(recruiter_id)')
        db.execute('CREATE INDEX IF NOT EXISTS rc_session_expiry ON rc_sessions(expires)')
        db.execute('''CREATE TABLE IF NOT EXISTS rc_auth_limits (
            key TEXT PRIMARY KEY, count INTEGER NOT NULL CHECK(count >= 1), reset_at INTEGER NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS rc_auth_reset ON rc_auth_limits(reset_at)')
        db.execute('''CREATE TABLE IF NOT EXISTS rc_activations (
            token_hash TEXT PRIMARY KEY, recruiter_id TEXT NOT NULL UNIQUE REFERENCES rc_accounts(id),
            expires INTEGER NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS cp_recruiter_prepared_owners (
            candidate_id TEXT PRIMARY KEY REFERENCES cp_candidates(id),
            recruiter_id TEXT NOT NULL REFERENCES rc_accounts(id))''')


def _body(body, allowed):
    require(isinstance(body, dict) and set(body) <= allowed, 'Проверьте поля входа.', 400)


def _header(headers, name):
    for key, value in headers.items():
        if isinstance(key, str) and key.casefold() == name.casefold():
            return value if isinstance(value, str) else ''
    return ''


def token(headers):
    auth = _header(headers, 'Authorization')
    if auth:
        parts = auth.split()
        value = parts[1] if len(parts) == 2 and parts[0].casefold() == 'bearer' else ''
    else:
        value = ''
        cookie = _header(headers, 'Cookie')
        if len(cookie) > 8192:
            return ''
        try:
            parsed = SimpleCookie()
            parsed.load(cookie)
            value = parsed[COOKIE].value if COOKIE in parsed else ''
        except (CookieError, KeyError, TypeError, ValueError):
            pass
    return value if TOKEN_PATTERN.fullmatch(value) else ''


def authenticate(db, headers, required=True):
    supplied = token(headers)
    row = None
    if supplied:
        row = db.execute('''SELECT a.id,a.name,a.email FROM rc_sessions s
            JOIN rc_accounts a ON a.id=s.recruiter_id
            WHERE s.token_hash=? AND s.expires>? AND a.active=1''',
            (hashlib.sha256(supplied.encode()).hexdigest(), int(time.time()))).fetchone()
    if required:
        require(row, 'Войдите в кабинет рекрутера.', 401)
    return {'id': row[0], 'name': row[1], 'email': row[2]} if row else None


def session(db, headers):
    recruiter = authenticate(db, headers, required=False)
    result = {'authenticated': bool(recruiter), 'recruiter': recruiter}
    if recruiter:
        result['token'] = token(headers)
    return result


def session_cookie(value):
    require(isinstance(value, str) and TOKEN_PATTERN.fullmatch(value), 'Некорректная сессия.', 400)
    return f'{COOKIE}={value}; Path=/; Max-Age={SESSION_SECONDS}; Secure; HttpOnly; SameSite=Lax'


def clear_cookie():
    return f'{COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax'


def consume_auth_limits(db, action, body, ip):
    require(action in ('register', 'login', 'activate', 'accounts'), 'Недоступное действие.', 400)
    ip = ip if isinstance(ip, str) and 0 < len(ip) <= 128 else 'unknown'
    email = body.get('email', '') if isinstance(body, dict) else ''
    email = email.strip().casefold()[:254] if isinstance(email, str) else ''
    if action == 'activate':
        supplied = body.get('token', '') if isinstance(body, dict) else ''
        email = hashlib.sha256(str(supplied)[:200].encode()).hexdigest()
    buckets = [('auth-ip:' + ip, 40, 900),
               (action + '-email:' + (email or 'invalid-email'), 5 if action == 'register' else 15,
                3600 if action == 'register' else 900)]
    tick = int(time.time())
    with _atomic(db):
        db.execute('DELETE FROM rc_auth_limits WHERE reset_at<=?', (tick,))
        for key, maximum, seconds in buckets:
            digest = hashlib.sha256(key.encode()).hexdigest()
            row = db.execute('SELECT count FROM rc_auth_limits WHERE key=?', (digest,)).fetchone()
            require(not row or row[0] < maximum, 'Слишком много попыток. Попробуйте позже.', 429)
            if row:
                db.execute('UPDATE rc_auth_limits SET count=count+1 WHERE key=?', (digest,))
            else:
                db.execute('INSERT INTO rc_auth_limits VALUES (?,?,?)', (digest, 1, tick + seconds))


def _issue_session(db, recruiter_id, expected_hash):
    row = db.execute('SELECT id,name,email,password_hash FROM rc_accounts WHERE id=? AND active=1',
                     (recruiter_id,)).fetchone()
    require(row and hmac.compare_digest(row[3], expected_hash), 'Войдите в кабинет рекрутера.', 401)
    tick = int(time.time())
    value = 'rc_' + secrets.token_urlsafe(32)
    db.execute('DELETE FROM rc_sessions WHERE expires<=?', (tick,))
    db.execute('INSERT INTO rc_sessions VALUES (?,?,?,?)',
               (hashlib.sha256(value.encode()).hexdigest(), recruiter_id, tick, tick + SESSION_SECONDS))
    return {'authenticated': True, 'recruiter': {'id': row[0], 'name': row[1], 'email': row[2]}, 'token': value}


def register(db, body, *, authorized=False):
    require(authorized is True, 'Регистрация доступна по приглашению HR Vision.', 403)
    _body(body, {'name', 'email', 'password'})
    email, name = _email(body.get('email')), text(body.get('name'), 140)
    hashed = _password_hash(body.get('password'))
    recruiter_id, stamp = 'recruiter_' + secrets.token_hex(12), iso(now())
    try:
        with _atomic(db):
            db.execute('INSERT INTO rc_accounts VALUES (?,?,?,?,1,?,?)',
                       (recruiter_id, email, hashed, name, stamp, stamp))
            return _issue_session(db, recruiter_id, hashed)
    except sqlite3.IntegrityError as error:
        if 'rc_accounts.email' in str(error):
            raise Problem('Не удалось создать аккаунт. Если вы уже регистрировались, войдите.', 409) from None
        raise


def provision(db, body, origin, *, authorized=False):
    require(authorized is True, 'Аккаунты создаёт команда HR Vision.', 403)
    _body(body, {'name', 'email'})
    email, name = _email(body.get('email')), text(body.get('name'), 140)
    with _atomic(db):
        existing = db.execute('SELECT id,active,password_hash FROM rc_accounts WHERE email=?', (email,)).fetchone()
        # Only an unactivated invitation may be reissued; disabled existing
        # accounts cannot be taken over through account provisioning.
        require(not existing or (existing[1] == 0 and existing[2] == ''),
                'Для этой почты уже есть аккаунт рекрутера.', 409)
        recruiter_id = existing[0] if existing else 'recruiter_' + secrets.token_hex(12)
        stamp = iso(now())
        if existing:
            db.execute('UPDATE rc_accounts SET name=?,updated_at=? WHERE id=?', (name, stamp, recruiter_id))
        else:
            db.execute('INSERT INTO rc_accounts VALUES (?,?,?, ?,0,?,?)', (recruiter_id, email, '', name, stamp, stamp))
        value = 'ra_' + secrets.token_urlsafe(32)
        db.execute('DELETE FROM rc_activations WHERE recruiter_id=?', (recruiter_id,))
        db.execute('INSERT INTO rc_activations VALUES (?,?,?,?)',
                   (hashlib.sha256(value.encode()).hexdigest(), recruiter_id, int(time.time()) + 72 * 3600, stamp))
        query = urllib.parse.urlencode({'id': 'hr-vision-product--manager', 'viewMode': 'story',
                                      'workspace': 'recruiter', 'recruiterActivate': value})
        return {'recruiter': {'id': recruiter_id, 'name': name, 'email': email},
                'activationUrl': origin.rstrip('/') + '/iframe.html?' + query}


def activate(db, body):
    _body(body, {'token', 'password'})
    value = body.get('token')
    require(isinstance(value, str) and ACTIVATION_PATTERN.fullmatch(value), 'Ссылка недействительна или истекла.', 404)
    password = _password_hash(body.get('password'))
    with _atomic(db):
        digest = hashlib.sha256(value.encode()).hexdigest()
        row = db.execute('''SELECT a.id FROM rc_activations t JOIN rc_accounts a ON a.id=t.recruiter_id
            WHERE t.token_hash=? AND t.expires>? AND a.active=0 AND a.password_hash='' ''',
                         (digest, int(time.time()))).fetchone()
        require(row, 'Ссылка недействительна или истекла.', 404)
        db.execute('UPDATE rc_accounts SET password_hash=?,active=1,updated_at=? WHERE id=?', (password, iso(now()), row[0]))
        db.execute('DELETE FROM rc_activations WHERE recruiter_id=?', (row[0],))
        return _issue_session(db, row[0], password)


def login(db, body):
    _body(body, {'email', 'password'})
    email = _email(body.get('email'))
    row = db.execute('SELECT id,password_hash FROM rc_accounts WHERE email=? AND active=1', (email,)).fetchone()
    matches = _password_matches(body.get('password'), row[1] if row else _DUMMY_HASH)
    require(matches and row, 'Проверьте почту и пароль.', 401)
    with _atomic(db):
        return _issue_session(db, row[0], row[1])


def update_profile(db, headers, body):
    _body(body, {'name'})
    with _atomic(db):
        recruiter = authenticate(db, headers)
        name = text(body.get('name'), 140)
        db.execute('UPDATE rc_accounts SET name=?,updated_at=? WHERE id=?', (name, iso(now()), recruiter['id']))
        return session(db, headers)


def logout(db, headers):
    supplied = token(headers)
    if supplied:
        db.execute('DELETE FROM rc_sessions WHERE token_hash=?', (hashlib.sha256(supplied.encode()).hexdigest(),))
    return clear_cookie()
