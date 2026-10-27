"""Employer identities and shared workspaces, independent of candidate sessions.

The HTTP adapter owns origin checks and response cookies. Before public register or
login it must commit consume_auth_limits() in a separate short transaction: a bad
password must not roll back its attempt counter. team_session() is an internal
entry point and must only run after the existing private team grant is verified.

No function commits or uses executescript. Mutations belong to the caller's
transaction, including schema initialization and failed multi-row operations.
"""
from contextlib import contextmanager
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import re
import secrets
import sqlite3
import time

from domain import Problem, require, text, iso, now


COOKIE = '__Host-hr_employer'
SESSION_SECONDS = 30 * 86400
TEAM_WORKSPACE_ID = 'hr-vision-team'
TEAM_ACCOUNT_ID = 'em-account-team'
TEAM_NAME = 'HR Vision'
TEAM_ACTOR_NAME = 'Команда HR Vision'
TOKEN_PATTERN = re.compile(r'em_[A-Za-z0-9_-]{43}')
# A syntactically valid non-account hash makes unknown-email checks cost the same
# bounded scrypt operation. The result is never accepted without an account row.
_DUMMY_HASH = 'scrypt$16384$8$1$' + '00' * 16 + '$' + '00' * 32


@contextmanager
def _atomic(db):
    # Releasing an outermost SAVEPOINT commits SQLite. An explicit BEGIN keeps
    # ownership with the caller, even when the function is called without BEGIN.
    if not db.in_transaction:
        db.execute('BEGIN')
    savepoint = 'em_' + secrets.token_hex(8)
    db.execute('SAVEPOINT ' + savepoint)
    try:
        yield
    except BaseException:
        db.execute('ROLLBACK TO ' + savepoint)
        db.execute('RELEASE ' + savepoint)
        raise
    else:
        db.execute('RELEASE ' + savepoint)


def init(db):
    """Idempotently initialize access tables without committing caller writes."""
    with _atomic(db):
        db.execute('''CREATE TABLE IF NOT EXISTS em_workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('employer','team')),
            created_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS em_accounts (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL UNIQUE REFERENCES em_workspaces(id) ON DELETE CASCADE,
            email TEXT UNIQUE COLLATE NOCASE,
            password_hash TEXT,
            name TEXT NOT NULL,
            is_team INTEGER NOT NULL DEFAULT 0 CHECK (is_team IN (0,1)),
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
            created_at TEXT NOT NULL,
            CHECK ((is_team=1 AND email IS NULL AND password_hash IS NULL) OR
                   (is_team=0 AND email IS NOT NULL AND password_hash IS NOT NULL))
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS em_sessions (
            token_hash TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES em_accounts(id) ON DELETE CASCADE,
            created_at INTEGER NOT NULL,
            expires INTEGER NOT NULL CHECK (expires > created_at)
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS em_session_account ON em_sessions(account_id)')
        db.execute('CREATE INDEX IF NOT EXISTS em_session_expiry ON em_sessions(expires)')
        db.execute('''CREATE TABLE IF NOT EXISTS em_auth_limits (
            key TEXT PRIMARY KEY,
            count INTEGER NOT NULL CHECK (count >= 1),
            reset_at INTEGER NOT NULL
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS em_auth_reset ON em_auth_limits(reset_at)')
        created = iso(now())
        db.execute('INSERT OR IGNORE INTO em_workspaces VALUES (?,?,?,?)',
                   (TEAM_WORKSPACE_ID, TEAM_NAME, 'team', created))
        db.execute('''INSERT OR IGNORE INTO em_accounts
            (id,workspace_id,email,password_hash,name,is_team,active,created_at)
            VALUES (?,?,NULL,NULL,?,1,1,?)''',
                   (TEAM_ACCOUNT_ID, TEAM_WORKSPACE_ID, TEAM_ACTOR_NAME, created))


def _body(body, allowed):
    require(isinstance(body, dict) and set(body) <= allowed,
            'Проверьте поля входа.', 400)
    return body


def _email(value):
    value = text(value, 254).casefold()
    require(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value) is not None,
            'Укажите корректную почту.', 400)
    return value


def _password_hash(password, salt=None):
    require(isinstance(password, str) and 10 <= len(password) <= 128,
            'Пароль должен содержать от 10 до 128 символов.', 400)
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$' + salt.hex() + '$' + digest.hex()


def _password_matches(password, stored):
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        return False
    if not isinstance(stored, str) or not re.fullmatch(
            r'scrypt\$16384\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{64}', stored):
        return False
    salt, digest = stored.split('$')[-2:]
    actual = _password_hash(password, bytes.fromhex(salt)).split('$')[-1]
    return hmac.compare_digest(actual, digest)


def consume_auth_limits(db, action, body, ip):
    """Consume IP and email limits; caller commits this before trying credentials.

    IP must be the adapter's trusted client address, never a body-supplied value.
    Email/IP bucket keys are hashed; no passwords or raw addresses are stored.
    All attempts count, including successful logins and malformed payloads.
    """
    require(action in ('register', 'login'), 'Недоступное действие.', 400)
    ip = ip if isinstance(ip, str) and 0 < len(ip) <= 128 else 'unknown'
    email = body.get('email', '') if isinstance(body, dict) else ''
    email = email.strip().casefold()[:254] if isinstance(email, str) else ''
    identity = email or 'invalid-email'
    buckets = [('auth-ip:' + ip, 40, 900)]
    buckets.append(('register-email:' + identity, 5, 3600) if action == 'register'
                   else ('login-email:' + identity, 15, 900))
    tick = int(time.time())
    with _atomic(db):
        # First write serializes concurrent attempts before reading counters.
        db.execute('DELETE FROM em_auth_limits WHERE reset_at<=?', (tick,))
        for key, limit, seconds in buckets:
            digest = hashlib.sha256(key.encode()).hexdigest()
            row = db.execute('SELECT count FROM em_auth_limits WHERE key=?', (digest,)).fetchone()
            require(not row or row[0] < limit,
                    'Слишком много попыток. Попробуйте позже.', 429)
            if row:
                db.execute('UPDATE em_auth_limits SET count=count+1 WHERE key=?', (digest,))
            else:
                db.execute('INSERT INTO em_auth_limits VALUES (?,?,?)', (digest, 1, tick + seconds))


def _issue_session(db, account_id, *, expected_hash=None):
    tick = int(time.time())
    db.execute('DELETE FROM em_sessions WHERE expires<=?', (tick,))
    row = db.execute('''SELECT a.id,a.name,a.password_hash,a.is_team,w.id,w.name,w.kind
        FROM em_accounts a JOIN em_workspaces w ON w.id=a.workspace_id
        WHERE a.id=? AND a.active=1''', (account_id,)).fetchone()
    require(row and ((row[3] == 1 and row[6] == 'team' and row[4] == TEAM_WORKSPACE_ID)
                     or (row[3] == 0 and row[6] == 'employer')),
            'Войдите в кабинет работодателя.', 401)
    if expected_hash is not None:
        require(hmac.compare_digest(row[2] or '', expected_hash),
                'Войдите в кабинет работодателя.', 401)
    token = 'em_' + secrets.token_urlsafe(32)
    db.execute('INSERT INTO em_sessions VALUES (?,?,?,?)',
               (hashlib.sha256(token.encode()).hexdigest(), account_id, tick, tick + SESSION_SECONDS))
    return {'token': token, 'workspace': {'id': row[4], 'name': row[5]},
            'actorName': row[1], 'accountId': row[0], 'expiresAt': tick + SESSION_SECONDS}


def register(db, body):
    """Create a new shared employer workspace; client IDs/roles are never accepted."""
    _body(body, {'email', 'password', 'name', 'workspaceName'})
    email = _email(body.get('email'))
    name = text(body.get('name'), 140)
    workspace_name = text(body.get('workspaceName', name), 200)
    password = _password_hash(body.get('password'))
    account_id, workspace_id = 'em-account-' + secrets.token_hex(12), 'em-workspace-' + secrets.token_hex(12)
    created = iso(now())
    try:
        with _atomic(db):
            # The first write wins the SQLite writer lock. A unique account-email
            # constraint resolves simultaneous registrations without orphan data.
            db.execute('INSERT INTO em_workspaces VALUES (?,?,?,?)',
                       (workspace_id, workspace_name, 'employer', created))
            db.execute('''INSERT INTO em_accounts
                (id,workspace_id,email,password_hash,name,is_team,active,created_at)
                VALUES (?,?,?,?,?,0,1,?)''',
                       (account_id, workspace_id, email, password, name, created))
            return _issue_session(db, account_id, expected_hash=password)
    except sqlite3.IntegrityError as error:
        if 'em_accounts.email' in str(error):
            raise Problem('Не удалось создать аккаунт. Если вы уже регистрировались, войдите.', 409) from None
        raise


def login(db, body):
    """Password login creates another session for the same shared workspace."""
    _body(body, {'email', 'password'})
    email = _email(body.get('email'))
    row = db.execute('''SELECT id,password_hash FROM em_accounts
        WHERE email=? AND is_team=0 AND active=1''', (email,)).fetchone()
    matches = _password_matches(body.get('password'), row[1] if row else _DUMMY_HASH)
    require(matches and row, 'Проверьте почту и пароль.', 401)
    with _atomic(db):
        return _issue_session(db, row[0], expected_hash=row[1])


def team_session(db):
    """Internal only: the HTTP caller must first validate its private team grant."""
    with _atomic(db):
        return _issue_session(db, TEAM_ACCOUNT_ID)


def _header(headers, name):
    for key, value in headers.items():
        if isinstance(key, str) and key.casefold() == name.casefold():
            return value if isinstance(value, str) else ''
    return ''


def _token(headers):
    auth = _header(headers, 'Authorization')
    if auth:
        # An explicitly supplied invalid bearer never falls back to a cookie.
        parts = auth.split()
        token = parts[1] if len(parts) == 2 and parts[0].casefold() == 'bearer' else ''
    else:
        token = ''
        cookie = _header(headers, 'Cookie')
        if len(cookie) > 8192:
            return ''
        try:
            parsed = SimpleCookie()
            parsed.load(cookie)
            token = parsed[COOKIE].value if COOKIE in parsed else ''
        except (CookieError, KeyError, ValueError, TypeError):
            pass
    return token if TOKEN_PATTERN.fullmatch(token) else ''


def authenticate(db, headers, required=True):
    """Resolve authority only from an unexpired employer session and active account."""
    token = _token(headers)
    row = None
    if token:
        row = db.execute('''SELECT w.id,a.id,a.name,w.kind,a.is_team FROM em_sessions s
            JOIN em_accounts a ON a.id=s.account_id JOIN em_workspaces w ON w.id=a.workspace_id
            WHERE s.token_hash=? AND s.expires>? AND a.active=1''',
                         (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        if row and not ((row[3] == 'employer' and row[4] == 0)
                        or (row[3] == 'team' and row[4] == 1 and row[0] == TEAM_WORKSPACE_ID)):
            row = None
    if required:
        require(row, 'Войдите в кабинет работодателя.', 401)
    return ({'workspaceId': row[0], 'accountId': row[1], 'name': row[2],
             'role': 'employer', 'team': row[3] == 'team'} if row else None)


def session_cookie(token):
    require(isinstance(token, str) and TOKEN_PATTERN.fullmatch(token), 'Некорректная сессия.', 400)
    return f'{COOKIE}={token}; Path=/; Max-Age={SESSION_SECONDS}; Secure; HttpOnly; SameSite=Lax'


def clear_cookie():
    return f'{COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax'


def logout(db, headers):
    """Idempotent revocation of this session only, never another device's session."""
    token = _token(headers)
    if token:
        db.execute('DELETE FROM em_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    return clear_cookie()
