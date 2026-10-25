"""Durable source-owned upload intents and fenced per-file checkpoints."""
import json
import secrets

from .repository import Conflict, LostLease


class UploadCapacity(Exception):
    def __init__(self, code, status=507):
        self.code, self.status = code, status


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class UploadRepository:
    def __init__(self, repository):
        self.repository = repository
        with repository.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS uploads (
              id TEXT PRIMARY KEY, caller TEXT NOT NULL, idempotency_key TEXT NOT NULL,
              manifest TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'uploading',
              receipt TEXT, ack TEXT, error_code TEXT, lease_token TEXT, lease_until REAL,
              reserved_bytes INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL, updated_at REAL NOT NULL,
              UNIQUE(caller,idempotency_key));
              CREATE TABLE IF NOT EXISTS upload_parts (
              upload_id TEXT NOT NULL REFERENCES uploads(id), part_index INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', sha256 TEXT, probe TEXT, stored TEXT, local_ref TEXT,
              lease_token TEXT, lease_until REAL, PRIMARY KEY(upload_id,part_index));
              CREATE TABLE IF NOT EXISTS upload_streams (
              token TEXT PRIMARY KEY,upload_id TEXT NOT NULL,part_index INTEGER NOT NULL,lease_until REAL NOT NULL);''')
            db.execute('BEGIN IMMEDIATE')
            if 'error_code' not in {row[1] for row in db.execute('PRAGMA table_info(uploads)')}:
                db.execute('ALTER TABLE uploads ADD COLUMN error_code TEXT')
            if 'local_ref' not in {row[1] for row in db.execute('PRAGMA table_info(upload_parts)')}:
                db.execute('ALTER TABLE upload_parts ADD COLUMN local_ref TEXT')
            if 'reserved_bytes' not in {row[1] for row in db.execute('PRAGMA table_info(uploads)')}:
                db.execute('ALTER TABLE uploads ADD COLUMN reserved_bytes INTEGER NOT NULL DEFAULT 0')
                for row in db.execute("SELECT id,manifest FROM uploads WHERE status<>'registered'").fetchall():
                    db.execute('UPDATE uploads SET reserved_bytes=? WHERE id=?',
                        (sum(p['size'] for p in json.loads(row['manifest'])['files']),row['id']))

    def begin(self, caller, key, manifest, budget=2*1024**3, orphan_bytes=0):
        body, now = encoded(manifest), self.repository.clock()
        with self.repository.db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT id,manifest FROM uploads WHERE caller=? AND idempotency_key=?', (caller,key)).fetchone()
            if prior:
                if prior['manifest'] != body:
                    raise Conflict()
                identity, created = prior['id'], False
            else:
                reserved = sum(p['size'] for p in manifest['files'])
                total = db.execute('SELECT COALESCE(sum(reserved_bytes),0) FROM uploads').fetchone()[0]
                if total + reserved + orphan_bytes > budget:
                    raise UploadCapacity('upload_staging_budget_exceeded')
                identity, created = secrets.token_hex(16), True
                db.execute('INSERT INTO uploads(id,caller,idempotency_key,manifest,reserved_bytes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (identity,caller,key,body,reserved,now,now))
                db.executemany('INSERT INTO upload_parts(upload_id,part_index) VALUES(?,?)',
                    [(identity,index) for index in range(len(manifest['files']))])
        return self.get(identity,caller), created

    def get(self, identity, caller=None):
        with self.repository.db() as db:
            row = db.execute('SELECT * FROM uploads WHERE id=?', (identity,)).fetchone()
            if row is None or caller is not None and row['caller'] != caller:
                return None
            result = dict(row)
            result['parts'] = [dict(p) for p in db.execute('SELECT * FROM upload_parts WHERE upload_id=? ORDER BY part_index', (identity,))]
        for key in ('manifest','receipt','ack'):
            if result[key] is not None:
                result[key] = json.loads(result[key])
        for part in result['parts']:
            for key in ('probe','stored'):
                if part[key] is not None:
                    part[key] = json.loads(part[key])
        return result

    def claim_part(self, identity, index, seconds):
        now, token = self.repository.clock(), secrets.token_hex(24)
        with self.repository.db() as db:
            changed = db.execute("""UPDATE upload_parts SET lease_token=?,lease_until=?
              WHERE upload_id=? AND part_index=? AND (lease_until IS NULL OR lease_until<=?)
              AND EXISTS(SELECT 1 FROM uploads WHERE id=? AND status='uploading')""",
              (token,now+seconds,identity,index,now,identity)).rowcount
        if not changed:
            raise Conflict()
        return token

    def claim_stream(self, identity, index, seconds, limit, budget, orphan_bytes=0):
        now, token = self.repository.clock(), secrets.token_hex(24)
        with self.repository.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM upload_streams WHERE lease_until<=?',(now,))
            if db.execute('SELECT count(*) FROM upload_streams').fetchone()[0] >= limit:
                raise UploadCapacity('upload_stream_concurrency_exceeded',429)
            if db.execute('SELECT COALESCE(sum(reserved_bytes),0) FROM uploads').fetchone()[0] + orphan_bytes > budget:
                raise UploadCapacity('upload_staging_budget_exceeded')
            state=db.execute('SELECT status FROM uploads WHERE id=?',(identity,)).fetchone()
            if state is None or state[0] in ('expired','rejected'):
                raise Conflict()
            db.execute('INSERT INTO upload_streams VALUES(?,?,?,?)',(token,identity,index,now+seconds))
            db.execute('UPDATE uploads SET updated_at=? WHERE id=?',(now,identity))
        return token

    def renew_stream(self, token, seconds):
        now=self.repository.clock()
        with self.repository.db() as db:
            changed=db.execute('UPDATE upload_streams SET lease_until=? WHERE token=? AND lease_until>?',
                (now+seconds,token,now)).rowcount
            if changed:
                db.execute('UPDATE uploads SET updated_at=? WHERE id=(SELECT upload_id FROM upload_streams WHERE token=?)',(now,token))
        if not changed:
            raise LostLease()

    def release_stream(self, token):
        with self.repository.db() as db:
            db.execute('DELETE FROM upload_streams WHERE token=?',(token,))

    def expire_idle(self, ttl):
        now=self.repository.clock()
        with self.repository.db() as db:
            db.execute("""UPDATE uploads SET status='expired',error_code='upload_expired',updated_at=?
              WHERE status='uploading' AND receipt IS NULL AND updated_at<?
              AND NOT EXISTS(SELECT 1 FROM upload_parts p WHERE p.upload_id=uploads.id AND p.lease_until>?)
              AND NOT EXISTS(SELECT 1 FROM upload_streams s WHERE s.upload_id=uploads.id AND s.lease_until>?)""",
                (now,now-ttl,now,now))

    def release_reservation(self, identity):
        with self.repository.db() as db:
            db.execute("UPDATE uploads SET reserved_bytes=0 WHERE id=? AND status IN ('registered','expired')",(identity,))

    def terminal_reserved(self):
        with self.repository.db() as db:
            return [row[0] for row in db.execute("SELECT id FROM uploads WHERE status IN ('registered','expired') AND reserved_bytes>0 LIMIT 100")]

    def update_part(self, identity, index, token, **changes):
        if not changes.keys() <= {'sha256','probe','stored','status','local_ref','lease_token','lease_until'}:
            raise ValueError('invalid_upload_checkpoint')
        for key in ('probe','stored'):
            if key in changes and changes[key] is not None:
                changes[key] = encoded(changes[key])
        with self.repository.db() as db:
            changed = db.execute('UPDATE upload_parts SET '+','.join(k+'=?' for k in changes)+
                ' WHERE upload_id=? AND part_index=? AND lease_token=? AND lease_until>?',
                (*changes.values(),identity,index,token,self.repository.clock())).rowcount
        if not changed:
            raise LostLease()

    def start_finalize(self, identity):
        with self.repository.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM uploads WHERE id=?', (identity,)).fetchone()
            if row['status'] == 'uploading':
                pending = db.execute("SELECT 1 FROM upload_parts WHERE upload_id=? AND (status<>'ready' OR lease_until>?) LIMIT 1",
                    (identity,self.repository.clock())).fetchone()
                if pending:
                    raise Conflict()
                db.execute("UPDATE uploads SET status='finalizing',updated_at=? WHERE id=?", (self.repository.clock(),identity))

    def claim_finalize(self, identity, seconds):
        now, token = self.repository.clock(), secrets.token_hex(24)
        with self.repository.db() as db:
            changed = db.execute("UPDATE uploads SET lease_token=?,lease_until=? WHERE id=? AND status='finalizing' AND (lease_until IS NULL OR lease_until<=?)",
                (token,now+seconds,identity,now)).rowcount
        if not changed:
            raise Conflict()
        return token

    def checkpoint(self, identity, token, **changes):
        if not changes.keys() <= {'status','receipt','ack','error_code','lease_token','lease_until'}:
            raise ValueError('invalid_upload_checkpoint')
        for key in ('receipt','ack'):
            if key in changes and changes[key] is not None:
                changes[key] = encoded(changes[key])
        changes['updated_at'] = self.repository.clock()
        with self.repository.db() as db:
            changed = db.execute('UPDATE uploads SET '+','.join(k+'=?' for k in changes)+
                ' WHERE id=? AND lease_token=? AND lease_until>?',
                (*changes.values(),identity,token,self.repository.clock())).rowcount
        if not changed:
            raise LostLease()

    def store_part(self, identity, index, token, stored):
        # A finalize lease owns all stored-object checkpoints after parts freeze.
        with self.repository.db() as db:
            changed = db.execute('''UPDATE upload_parts SET stored=? WHERE upload_id=? AND part_index=?
              AND EXISTS(SELECT 1 FROM uploads WHERE id=? AND lease_token=? AND lease_until>?)''',
                (encoded(stored),identity,index,identity,token,self.repository.clock())).rowcount
        if not changed:
            raise LostLease()

    def due(self, limit=3):
        with self.repository.db() as db:
            return [row[0] for row in db.execute("SELECT id FROM uploads WHERE status='finalizing' AND (lease_until IS NULL OR lease_until<=?) ORDER BY updated_at,id LIMIT ?",
                (self.repository.clock(),limit))]

    def reject_unleased(self, identity, error_code):
        with self.repository.db() as db:
            db.execute("UPDATE uploads SET status='rejected',error_code=?,updated_at=? WHERE id=? AND status='finalizing' AND (lease_until IS NULL OR lease_until<=?)",
                (error_code,self.repository.clock(),identity,self.repository.clock()))
