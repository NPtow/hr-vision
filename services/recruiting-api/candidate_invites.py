"""Recruiter-prepared people and two deliberately separate invitation capabilities."""
import datetime as dt
import hashlib
import json
import re
import secrets
import time
from urllib.parse import quote

from domain import require, iso, now, timestamp


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def uid(prefix):
    return prefix + '_' + secrets.token_hex(12)


def migrate_accounts(db):
    """Allow a real person to exist before credentials, retaining every foreign key."""
    info = {row[1]: row for row in db.execute('PRAGMA table_info(cp_candidates)')}
    if not info or not info['email'][3]:
        return
    require(not db.in_transaction, 'Миграция аккаунтов требует отдельной транзакции.', 500)
    foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()[0]
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE cp_candidates_next (
          id TEXT PRIMARY KEY, email TEXT UNIQUE, password_hash TEXT,
          profile TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        db.execute('INSERT INTO cp_candidates_next SELECT id,email,password_hash,profile,created_at,updated_at FROM cp_candidates')
        db.execute('DROP TABLE cp_candidates')
        db.execute('ALTER TABLE cp_candidates_next RENAME TO cp_candidates')
        require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'Нарушена целостность профилей.', 500)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=' + str(foreign_keys))


def init(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS cp_intake (
        candidate_id TEXT PRIMARY KEY REFERENCES cp_candidates(id), source TEXT NOT NULL,
        external_key TEXT UNIQUE, contact_email TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS cp_invitations (
        id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
        interview_id TEXT REFERENCES cp_interviews(id), scope TEXT NOT NULL CHECK(scope IN ('call','claim')),
        expires INTEGER NOT NULL, revoked_at TEXT, consumed_at TEXT, created_at TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS cp_invitation_candidate ON cp_invitations(candidate_id,scope);
      CREATE TABLE IF NOT EXISTS cp_recording_control (
        interview_id TEXT PRIMARY KEY REFERENCES cp_interviews(id), consent_received_at TEXT,
        consent_source TEXT, start_requested_at TEXT, recording_requested INTEGER NOT NULL DEFAULT 0,
        transcription_requested INTEGER NOT NULL DEFAULT 0);
    ''')


def create_prepared(db, profile, *, source='manual', external_key=None, contact_email='', cv=None):
    require(source in ('manual', 'hh'), 'Источник профиля не поддерживается.', 400)
    require(not profile.get('consentReuse') and not profile.get('autoApprove'), 'Согласие оформляется отдельно.', 400)
    if external_key is not None:
        require(isinstance(external_key, str) and 1 <= len(external_key) <= 300, 'Некорректный внешний идентификатор.', 400)
        row = db.execute('SELECT candidate_id FROM cp_intake WHERE external_key=?', (external_key,)).fetchone()
        if row:
            return row[0]
    cid, created = uid('person'), iso(now())
    # NULL is intentional: no guessed email, password or consent from a CV.
    db.execute('INSERT INTO cp_candidates(id,email,password_hash,profile,created_at,updated_at) VALUES (?,NULL,NULL,?,?,?)', (cid, dump(profile), created, created))
    db.execute('INSERT INTO cp_intake VALUES (?,?,?,?,?)', (cid, source, external_key, contact_email, created))
    if cv:
        db.execute('INSERT INTO cp_resumes VALUES (?,?,?,?,?,?,?)',
                   (cid, cv['filename'], cv['mime'], cv['content'], cv['text'], dump(cv['fields']), created))
    return cid


def intake(db, cid):
    row = db.execute('SELECT source,contact_email FROM cp_intake WHERE candidate_id=?', (cid,)).fetchone()
    return {'source': row[0], 'contactEmail': row[1]} if row else None


def recording(db, iid):
    row = db.execute('''SELECT consent_received_at,consent_source,start_requested_at,
      recording_requested,transcription_requested FROM cp_recording_control WHERE interview_id=?''', (iid,)).fetchone()
    return {'consentReceived': bool(row and row[0]), 'consentSource': row[1] if row else None,
            'startRequested': bool(row and row[2]), 'recordingRequested': bool(row and row[3]),
            'transcriptionRequested': bool(row and row[4])}


def issue(db, cid, scope, origin):
    require(scope in ('call', 'claim'), 'Выберите назначение ссылки.', 400)
    person = db.execute('SELECT email FROM cp_candidates WHERE id=?', (cid,)).fetchone()
    require(person and intake(db, cid), 'Подготовленный профиль не найден.', 404)
    row = db.execute('''SELECT i.id,i.status,s.end FROM cp_interviews i JOIN cp_recruiter_slots s ON s.id=i.slot_id
      WHERE i.candidate_id=?''', (cid,)).fetchone()
    require(row, 'Сначала назначьте встречу.', 409)
    if scope == 'call':
        require(row[1] in ('booked', 'live'), 'Встреча уже завершена.', 409)
        expires = int(timestamp(row[2]).timestamp()) + 90 * 60
        require(expires > time.time(), 'Время встречи прошло.', 409)
    else:
        require(person[0] is None, 'Профиль уже активирован.', 409)
        require(row[1] == 'completed' or recording(db, row[0])['startRequested'],
                'Ссылку на профиль можно выдать после начала интервью.', 409)
        expires = int(time.time()) + 7 * 86400
    revoke(db, cid, scope)
    token, iid = secrets.token_urlsafe(32), uid('invitation')
    db.execute('INSERT INTO cp_invitations VALUES (?,?,?,?,?,?,NULL,NULL,?)',
               (iid, hashlib.sha256(token.encode()).hexdigest(), cid, row[0], scope, expires, iso(now())))
    page, fragment = ('guest', 'invite') if scope == 'call' else ('claim', 'claim')
    return {'invitationId': iid, 'url': origin + '/iframe.html?id=hr-vision-product--candidate&viewMode=story&cp=' + page + '#' + fragment + '=' + quote(token),
            'expiresAt': iso(dt.datetime.fromtimestamp(expires, dt.timezone.utc))}


def revoke(db, cid, scope):
    require(scope in ('call', 'claim'), 'Выберите назначение ссылки.', 400)
    db.execute('UPDATE cp_invitations SET revoked_at=? WHERE candidate_id=? AND scope=? AND revoked_at IS NULL',
               (iso(now()), cid, scope))


def resolve(db, token, scope, *, allow_consumed=False):
    require(isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]{40,100}', token), 'Ссылка недействительна.', 404)
    row = db.execute('''SELECT id,candidate_id,interview_id,expires,revoked_at,consumed_at
      FROM cp_invitations WHERE token_hash=? AND scope=?''', (hashlib.sha256(token.encode()).hexdigest(), scope)).fetchone()
    require(row and not row[4] and row[3] > time.time(), 'Ссылка истекла или отозвана. Попросите рекрутера прислать новую.', 410)
    require(allow_consumed or not row[5], 'Профиль уже активирован. Войдите со своей почтой и паролем.', 409)
    return {'id': row[0], 'candidateId': row[1], 'interviewId': row[2], 'expires': row[3], 'consumed': bool(row[5])}
