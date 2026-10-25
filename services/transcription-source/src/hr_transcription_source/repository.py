"""SQLite outbox, persisted checkpoints, expiring fenced leases. Own DB only."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time


class LostLease(Exception):
    pass


class Conflict(Exception):
    pass


class Repository:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY,caller TEXT NOT NULL,event TEXT NOT NULL,revision TEXT NOT NULL,
              stable_key TEXT NOT NULL UNIQUE,transcription_status TEXT NOT NULL DEFAULT 'queued',
              delivery_status TEXT NOT NULL DEFAULT 'pending',asr_job_id TEXT,input_sha256 TEXT,
              result TEXT,raw BLOB,raw_ref TEXT,receipt TEXT,transcript_ids TEXT,
              error_code TEXT,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL,
              lease_token TEXT,lease_until REAL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
              selection_requested INTEGER NOT NULL DEFAULT 0,selection_revision INTEGER,
              selection_status TEXT NOT NULL DEFAULT 'not_requested',selection_outcome TEXT);
              CREATE TABLE IF NOT EXISTS idempotency(caller TEXT,key TEXT,job_id TEXT REFERENCES jobs(id),
              PRIMARY KEY(caller,key));
              CREATE TABLE IF NOT EXISTS worker_health(id INTEGER PRIMARY KEY CHECK(id=1),seen REAL);''')
            # Additive migration preserves paid ASR/result/immutable receipts.
            db.execute('BEGIN IMMEDIATE')
            columns={row[1] for row in db.execute('PRAGMA table_info(jobs)')}
            for name,definition in (
                    ('selection_requested','INTEGER NOT NULL DEFAULT 0'),
                    ('selection_revision','INTEGER'),
                    ('selection_status',"TEXT NOT NULL DEFAULT 'not_requested'"),
                    ('selection_outcome','TEXT')):
                if name not in columns:
                    db.execute('ALTER TABLE jobs ADD COLUMN '+name+' '+definition)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=15000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, identity, caller=None):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone()
        if row is None or caller is not None and row['caller'] != caller:
            return None
        job = dict(row)
        for k in ('event', 'result', 'raw_ref', 'receipt', 'transcript_ids'):
            if job[k] is not None:
                job[k] = json.loads(job[k])
        return job

    def submit(self, caller, key, event, revision, *, select_primary=False):
        encoded = json.dumps(event, sort_keys=True, separators=(',', ':'))
        # Candidate scope is authenticated on every submission, before dedup.
        stable = hashlib.sha256(json.dumps([event, revision], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        stamp = self.clock()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT job_id FROM idempotency WHERE caller=? AND key=?', (caller,key)).fetchone()
            if prior:
                old = db.execute('SELECT stable_key FROM jobs WHERE id=?', (prior[0],)).fetchone()
                if old[0] != stable:
                    raise Conflict()
                identity, created = prior[0], False
            else:
                row = db.execute('SELECT id,caller FROM jobs WHERE stable_key=?', (stable,)).fetchone()
                if row and row['caller'] != caller:
                    # Do not expose another principal's job/result.
                    raise Conflict()
                identity, created = (row['id'], False) if row else (secrets.token_hex(16), True)
                if created:
                    db.execute('INSERT INTO jobs(id,caller,event,revision,stable_key,next_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                        (identity,caller,encoded,revision,stable,stamp,stamp,stamp))
                db.execute('INSERT INTO idempotency VALUES(?,?,?)',(caller,key,identity))
            if select_primary:
                # Enabling primary selection is not a new ASR config/revision.
                # Wake even a completed/failed legacy job, preserving all bytes.
                db.execute("""UPDATE jobs SET selection_requested=1,selection_status='requested',
                    next_at=?,updated_at=? WHERE id=? AND selection_requested=0""",(stamp,stamp,identity))
        return self.get(identity), created

    def due(self):
        with self.db() as db:
            return [r[0] for r in db.execute("SELECT id FROM jobs WHERE ((delivery_status NOT IN ('succeeded','rejected') AND transcription_status NOT IN ('failed','uncertain')) OR (selection_requested=1 AND selection_status IN ('requested','pending'))) AND next_at<=? AND (lease_until IS NULL OR lease_until<=?) ORDER BY next_at LIMIT 100", (self.clock(),self.clock()))]

    def claim(self, identity, seconds):
        stamp, token = self.clock(), secrets.token_hex(24)
        with self.db() as db:
            db.execute('INSERT INTO worker_health VALUES(1,?) ON CONFLICT(id) DO UPDATE SET seen=excluded.seen',(stamp,))
            result = db.execute("UPDATE jobs SET lease_token=?,lease_until=?,attempts=attempts+1 WHERE id=? AND next_at<=? AND (lease_until IS NULL OR lease_until<=?) AND ((delivery_status NOT IN ('succeeded','rejected') AND transcription_status NOT IN ('failed','uncertain')) OR (selection_requested=1 AND selection_status IN ('requested','pending')))", (token,stamp+seconds,identity,stamp,stamp))
        return (self.get(identity), token) if result.rowcount else (None,None)

    def update(self, identity, token, **changes):
        allowed = {'transcription_status','delivery_status','asr_job_id','input_sha256','result','raw','raw_ref','receipt','transcript_ids','error_code','next_at','lease_token','lease_until','selection_requested','selection_revision','selection_status','selection_outcome'}
        if not changes.keys() <= allowed:
            raise ValueError('invalid_checkpoint')
        for k in ('result','raw_ref','receipt','transcript_ids'):
            if k in changes and changes[k] is not None:
                changes[k] = json.dumps(changes[k], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        changes['updated_at'] = self.clock()
        with self.db() as db:
            result = db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in changes)+' WHERE id=? AND lease_token=? AND lease_until>?', (*changes.values(),identity,token,self.clock()))
        if not result.rowcount:
            raise LostLease()
        return self.get(identity)

    def worker_seen(self):
        with self.db() as db:
            row = db.execute('SELECT seen FROM worker_health WHERE id=1').fetchone()
            return row[0] if row else None
