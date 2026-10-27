"""Private durable queue/checkpoints. SQLite contains opaque bindings, never HR tables."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from .errors import LeaseLost, ServiceError


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Store:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = str(path), clock
        root = Path(path).parent
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS source_jobs (
                id TEXT PRIMARY KEY, binding_id TEXT NOT NULL, generation INTEGER NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                checkpoint TEXT NOT NULL DEFAULT '{}', error_code TEXT,
                next_run REAL NOT NULL, lease_token TEXT, lease_until REAL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(binding_id,generation));
              CREATE TABLE IF NOT EXISTS source_receipts (
                receipt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, body TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                FOREIGN KEY(job_id) REFERENCES source_jobs(id));
              CREATE TABLE IF NOT EXISTS source_artifacts (
                binding_id TEXT NOT NULL, external_id TEXT NOT NULL, artifact TEXT NOT NULL,
                PRIMARY KEY(binding_id,external_id));
              CREATE TABLE IF NOT EXISTS source_webhooks (
                event_id TEXT PRIMARY KEY, hint TEXT NOT NULL, received_at REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS source_jobs_due ON source_jobs(status,next_run);
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    def _row(self, row):
        if row is None:
            return None
        result = dict(row)
        result['checkpoint'] = json.loads(result['checkpoint'])
        return result

    def create(self, binding_id, generation, source_ref=None):
        now = self.clock()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Higher generations fence stale jobs locally; core is the final fence.
            highest = db.execute('SELECT MAX(generation) FROM source_jobs WHERE binding_id=?', (binding_id,)).fetchone()[0]
            if highest is not None and highest > generation:
                db.rollback()
                raise ServiceError('stale_generation', 409)
            db.execute("UPDATE source_jobs SET status='fenced',lease_token=NULL,lease_until=NULL WHERE binding_id=? AND generation<?", (binding_id,generation))
            initial = {'source_ref':source_ref} if source_ref else {}
            previous = db.execute('SELECT checkpoint FROM source_jobs WHERE binding_id=? ORDER BY generation DESC LIMIT 1',(binding_id,)).fetchone()
            if previous:
                old = json.loads(previous[0])
                if old.get('source_ref')==source_ref:
                    # Retention upgrades fence publishing, not speech processing.
                    # Preserve accepted ASR IDs, including uncertain/failed ones.
                    initial['asr'] = old.get('asr',{})
                    initial['assets'] = {k:v for k,v in old.get('assets',{}).items() if
                        v.get('kind')=='transcript' and v.get('state')=='ready'}
            db.execute('INSERT OR IGNORE INTO source_jobs(id,binding_id,generation,status,checkpoint,next_run,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()),binding_id,generation,'queued',encode(initial),now,now,now))
            row = db.execute('SELECT * FROM source_jobs WHERE binding_id=? AND generation=?', (binding_id,generation)).fetchone()
            db.commit()
            return self._row(row)

    def get(self, job_id):
        with self.db() as db:
            return self._row(db.execute('SELECT * FROM source_jobs WHERE id=?', (job_id,)).fetchone())

    def resume(self,job_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM source_jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:
                raise ServiceError('job_not_found',404)
            if row['status'] not in ('succeeded','failed','waiting'):
                raise ServiceError('job_not_resumable',409)
            cp = json.loads(row['checkpoint'])
            cp.pop('terminal',None); cp.pop('failed',None)
            cp['transport_attempts'] = 0
            cp['normalization_attempts'] = 0
            cp['asr_errors'] = 0
            # Never clear ASR IDs/statuses: uncertain/failed paid calls require
            # a separate explicit transcription retry decision, not this resume.
            db.execute("UPDATE source_jobs SET status='queued',checkpoint=?,attempts=0,error_code=NULL,next_run=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (encode(cp),self.clock(),self.clock(),job_id))
            db.commit()
        return self.get(job_id)

    def due(self, limit=100):
        now = self.clock()
        with self.db() as db:
            return [row['id'] for row in db.execute("SELECT id FROM source_jobs WHERE status IN ('queued','waiting','running') AND next_run<=? AND (lease_until IS NULL OR lease_until<=?) ORDER BY next_run LIMIT ?", (now,now,limit))]

    def claim(self, job_id, seconds):
        now, token = self.clock(), secrets.token_hex(24)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute("UPDATE source_jobs SET status='running',lease_token=?,lease_until=?,updated_at=? WHERE id=? AND status IN ('queued','waiting','running') AND next_run<=? AND (lease_until IS NULL OR lease_until<=?)", (token,now+seconds,now,job_id,now,now)).rowcount
            db.commit()
            return (self.get(job_id), token) if changed else (None,None)

    def _lease(self, db, job_id, token):
        row = db.execute("SELECT * FROM source_jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>?", (job_id,token,self.clock())).fetchone()
        if row is None:
            raise LeaseLost()
        return row

    def renew(self, job_id, token, seconds):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._lease(db,job_id,token)
            db.execute('UPDATE source_jobs SET lease_until=?,updated_at=? WHERE id=?', (self.clock()+seconds,self.clock(),job_id))
            db.commit()

    def save(self, job_id, token, checkpoint, *, attempts=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._lease(db,job_id,token)
            db.execute('UPDATE source_jobs SET checkpoint=?,attempts=COALESCE(?,attempts),updated_at=? WHERE id=?', (encode(checkpoint),attempts,self.clock(),job_id))
            db.commit()

    def artifact(self, binding_id, external_id):
        with self.db() as db:
            row = db.execute('SELECT artifact FROM source_artifacts WHERE binding_id=? AND external_id=?', (binding_id,external_id)).fetchone()
            return json.loads(row[0]) if row else None

    def remember_verified_artifact(self,binding_id,external_id,artifact):
        """Only after authorized core binding + exact provider discovery/inspection."""
        with self.db() as db:
            db.execute('INSERT INTO source_artifacts VALUES(?,?,?) ON CONFLICT(binding_id,external_id) DO UPDATE SET artifact=excluded.artifact', (binding_id,external_id,encode(artifact)))

    def webhook(self,hint):
        if hint.get('test'):
            return False,[]
        now = self.clock()
        payload = hint['payload']
        room_id,room_name = payload.get('room_id'),payload.get('room_name')
        session = payload.get('mtg_session_id',payload.get('mtgSessionId'))
        ready = hint['type'] in ('recording.ready-to-download','transcript.ready-to-download')
        woken = []
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            inserted = db.execute('INSERT OR IGNORE INTO source_webhooks VALUES(?,?,?)', (hint['event_id'],encode(hint),now)).rowcount
            if not inserted:
                db.commit()
                return True,[]
            if room_id or room_name:
                rows = db.execute("SELECT * FROM source_jobs WHERE status IN ('queued','waiting','succeeded','failed') AND (json_extract(checkpoint,'$.source_ref.room_id')=? OR json_extract(checkpoint,'$.source_ref.room_name')=?)", (room_id,room_name)).fetchall()
                for row in rows:
                    cp = json.loads(row['checkpoint'])
                    bound = cp.get('source_ref',{}).get('session_id')
                    if session and bound and session != bound:
                        continue
                    if row['status'] in ('succeeded','failed') and not ready:
                        continue
                    if row['status'] in ('succeeded','failed'):
                        cp.pop('terminal',None); cp.pop('failed',None)
                        cp['acquisition_complete'] = False
                    db.execute("UPDATE source_jobs SET status='queued',checkpoint=?,attempts=0,next_run=?,updated_at=? WHERE id=?", (encode(cp),now,now,row['id']))
                    woken.append(row['id'])
            db.commit()
        return False,woken

    def save_artifact(self, job_id, token, binding_id, external_id, artifact):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._lease(db,job_id,token)
            db.execute('INSERT INTO source_artifacts VALUES(?,?,?) ON CONFLICT(binding_id,external_id) DO UPDATE SET artifact=excluded.artifact', (binding_id,external_id,encode(artifact)))
            db.commit()

    def receipt(self, job_id, token, body):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._lease(db,job_id,token)
            db.execute('INSERT OR IGNORE INTO source_receipts VALUES(?,?,?,?,?)', (body['receipt_id'],job_id,encode(body),0,self.clock()))
            existing = db.execute('SELECT body FROM source_receipts WHERE receipt_id=?', (body['receipt_id'],)).fetchone()[0]
            if existing != encode(body):
                raise ServiceError('receipt_conflict',409)
            db.commit()

    def pending(self, job_id):
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM source_receipts WHERE job_id=? AND delivered=0 ORDER BY created_at,receipt_id', (job_id,))]

    def delivered(self, job_id, token, receipt_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._lease(db,job_id,token)
            db.execute('UPDATE source_receipts SET delivered=1 WHERE job_id=? AND receipt_id=?', (job_id,receipt_id))
            db.commit()

    def release(self, job_id, token, status, *, delay=0, error_code=None):
        if status not in ('waiting','succeeded','failed','fenced'):
            raise ValueError('Invalid source state')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._lease(db,job_id,token)
            db.execute('UPDATE source_jobs SET status=?,error_code=?,next_run=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?', (status,error_code,self.clock()+delay,self.clock(),job_id))
            db.commit()
