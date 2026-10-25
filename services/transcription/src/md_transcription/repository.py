"""Durable job records with per-tenant idempotency and fenced leases."""
import hashlib
import fcntl
import os
from contextlib import contextmanager
import json
import time
import uuid
from pathlib import Path
from sqlalchemy import inspect, text, or_, event, create_engine, MetaData, Table, Column, String, Text, Integer, Float, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError

class RepositoryConflict(ValueError):
    pass

metadata = MetaData()
jobs = Table('jobs', metadata,
    Column('id', String, primary_key=True), Column('client_id', String, nullable=False),
    Column('idempotency_key', String, nullable=False), Column('fingerprint', String, nullable=False),
    Column('input_key', String, nullable=False), Column('input_sha256', String, nullable=False),
    Column('input_size', Integer, nullable=False), Column('filename', String, nullable=False),
    Column('language', String), Column('backend_model', String), Column('backend_identity', String, nullable=False),
    Column('status', String, nullable=False), Column('attempts', Integer, nullable=False),
    Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False),
    Column('available_at', Float, nullable=False), Column('lease_until', Float), Column('lease_token', String),
    Column('provider_state', String), Column('provider_operation_id', String),
    Column('provider_submit_token', String), Column('provider_staging_key', String),
    Column('provider_raw_json', Text), Column('provider_metadata_json', Text),
    Column('result_json', Text), Column('error_code', String),
    UniqueConstraint('client_id', 'idempotency_key'))

worker_health = Table('worker_health', metadata, Column('id', String, primary_key=True), Column('seen_at', Float, nullable=False))

class Repository:
    def __init__(self, database_url):
        if database_url.startswith('sqlite:///') and database_url != 'sqlite:///:memory:':
            Path(database_url[len('sqlite:///'):]).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(database_url, connect_args={'check_same_thread': False, 'timeout':30} if database_url.startswith('sqlite') else {})

        if database_url.startswith('sqlite'):
            @event.listens_for(self.engine, 'connect')
            def configure_sqlite(connection, _):
                cursor = connection.cursor()
                cursor.execute('PRAGMA busy_timeout=30000')
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.execute('PRAGMA synchronous=FULL')
                cursor.close()

    def init(self):
        with self._schema_lock():
            self._migrate()

    @contextmanager
    def _schema_lock(self):
        # API, worker and scheduler may start together against the same local SQLite.
        # Serialize create_all plus additive migrations across processes.
        database=self.engine.url.database
        if self.engine.dialect.name!='sqlite' or database in (None,':memory:'):
            yield
            return
        fd=os.open(database+'.migration.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd,fcntl.LOCK_UN); os.close(fd)

    def _migrate(self):
        metadata.create_all(self.engine)
        # Additive migration for existing single-host SQLite deployments.
        columns = {column['name'] for column in inspect(self.engine).get_columns('jobs')}
        with self.engine.begin() as conn:
            for name in ('provider_state', 'provider_operation_id', 'provider_submit_token', 'provider_staging_key',
                         'provider_raw_json', 'provider_metadata_json'):
                if name not in columns:
                    conn.execute(text('ALTER TABLE jobs ADD COLUMN ' + name + ' TEXT'))

    def _dict(self, row):
        if row is None: return None
        value = dict(row)
        value['result'] = json.loads(value.pop('result_json')) if value['result_json'] is not None else None
        value['provider_raw'] = json.loads(value.pop('provider_raw_json')) if value['provider_raw_json'] else None
        value['provider_metadata'] = json.loads(value.pop('provider_metadata_json')) if value['provider_metadata_json'] else None
        return value

    def submit(self, client_id, key, input_key, input_sha256, input_size, filename, language, backend_identity, initial_status='queued', backend_model=None):
        if not client_id or not key: raise ValueError('client ID and idempotency key required')
        if initial_status not in ('queued', 'uploading'): raise ValueError('invalid initial status')
        fingerprint = hashlib.sha256(json.dumps([input_sha256, language, backend_identity, filename], separators=(',',':')).encode()).hexdigest()
        now = time.time()
        values = dict(id=str(uuid.uuid4()), client_id=client_id, idempotency_key=key, fingerprint=fingerprint,
            input_key=input_key, input_sha256=input_sha256, input_size=input_size, filename=filename,
            language=language, backend_model=backend_model, backend_identity=backend_identity, status=initial_status, attempts=0,
            created_at=now, updated_at=now, available_at=now)
        try:
            with self.engine.begin() as conn: conn.execute(jobs.insert().values(**values))
            return self.get(values['id']), True
        except IntegrityError:
            with self.engine.connect() as conn:
                row = conn.execute(select(jobs).where(jobs.c.client_id==client_id, jobs.c.idempotency_key==key)).mappings().first()
            if row is None: raise
            if row['fingerprint'] != fingerprint: raise RepositoryConflict('idempotency key already used with different payload')
            return self._dict(row), False

    def get(self, job_id, client_id=None):
        query = select(jobs).where(jobs.c.id==job_id)
        if client_id is not None: query = query.where(jobs.c.client_id==client_id)
        with self.engine.connect() as conn: return self._dict(conn.execute(query).mappings().first())

    def claim(self, job_id, lease_seconds, max_attempts):
        self.due(limit=0)  # Expired requests become uncertain even with direct delivery.
        now, token = time.time(), str(uuid.uuid4())
        with self.engine.begin() as conn:
            changed = conn.execute(update(jobs).where(jobs.c.id==job_id, jobs.c.status=='queued', jobs.c.available_at<=now, or_(jobs.c.attempts<max_attempts, jobs.c.provider_operation_id.is_not(None),jobs.c.provider_state=='response_saved')).values(status='running', lease_token=token, lease_until=now+lease_seconds, attempts=jobs.c.attempts+1, updated_at=now)).rowcount
            if not changed: return None
            return self._dict(conn.execute(select(jobs).where(jobs.c.id==job_id)).mappings().one())

    def _fence(self, job_id, token):
        return (jobs.c.id==job_id, jobs.c.status=='running', jobs.c.lease_token==token, jobs.c.lease_until>time.time())

    def renew(self, job_id, token, lease_seconds):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(*self._fence(job_id,token)).values(lease_until=time.time()+lease_seconds, updated_at=time.time())).rowcount)

    def succeed(self, job_id, token, result, raw=None, provider_metadata=None):
        encoded = json.dumps(result, allow_nan=False)
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(*self._fence(job_id,token)).values(status='succeeded', result_json=encoded, provider_raw_json=json.dumps(raw, allow_nan=False) if raw is not None else None, provider_metadata_json=json.dumps(provider_metadata, allow_nan=False) if provider_metadata is not None else None, error_code=None, lease_token=None, lease_until=None, updated_at=time.time())).rowcount)

    def fail(self, job_id, token, code, retryable=False, uncertain=False, max_attempts=3, retry_delay=None):
        with self.engine.begin() as conn:
            row = conn.execute(select(jobs.c.attempts, jobs.c.provider_operation_id).where(*self._fence(job_id,token))).first()
            if row is None: return False
            status = 'uncertain' if uncertain else ('queued' if retryable and (row.attempts<max_attempts or row.provider_operation_id) else 'failed')
            return bool(conn.execute(update(jobs).where(*self._fence(job_id,token)).values(status=status, error_code=code, lease_token=None, lease_until=None, updated_at=time.time(), available_at=time.time()+(retry_delay if retry_delay is not None else min(300,2**min(row.attempts,9))))).rowcount)

    def due(self, limit=100, after_id=None):
        now = time.time()
        with self.engine.begin() as conn:
            expired = (jobs.c.status=='running',jobs.c.lease_until<=now)
            # Known async operations are safe to resume using GET only. No submit intent is also safe.
            conn.execute(update(jobs).where(*expired, or_(jobs.c.provider_operation_id.is_not(None), jobs.c.provider_state.in_(['prepared','response_saved']))).values(status='queued', error_code='worker_resuming', lease_token=None, lease_until=None, available_at=now, updated_at=now))
            conn.execute(update(jobs).where(*expired).values(status='uncertain', error_code='worker_lease_expired',lease_token=None,lease_until=None,updated_at=now))
            query = select(jobs.c.id).where(jobs.c.status=='queued',jobs.c.available_at<=now)
            if after_id is not None: query = query.where(jobs.c.id>after_id)
            return list(conn.execute(query.order_by(jobs.c.id).limit(limit)).scalars())

    def retry(self, job_id, client_id):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(jobs.c.id==job_id,jobs.c.client_id==client_id,jobs.c.status.in_(['failed','uncertain']), or_(jobs.c.provider_state.is_(None), jobs.c.provider_state!='submitting', jobs.c.provider_operation_id.is_not(None))).values(status='queued', attempts=0,error_code=None,available_at=time.time(),updated_at=time.time())).rowcount)


    def touch_worker(self):
        with self.engine.begin() as conn:
            if not conn.execute(update(worker_health).where(worker_health.c.id=='asr').values(seen_at=time.time())).rowcount:
                conn.execute(worker_health.insert().values(id='asr',seen_at=time.time()))

    def worker_readiness(self):
        with self.engine.connect() as conn:
            seen = conn.execute(select(worker_health.c.seen_at).where(worker_health.c.id=='asr')).scalar()
        return {'status':'observed' if seen is not None and time.time()-seen<90 else 'not_observed', 'last_seen_at':seen}

    def pending_cleanup(self):
        with self.engine.connect() as conn:
            return [self._dict(row) for row in conn.execute(select(jobs).where(jobs.c.status=='succeeded',jobs.c.provider_staging_key.is_not(None)).limit(100)).mappings()]

    def cleaned(self, job_id):
        with self.engine.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.id==job_id,jobs.c.status=='succeeded').values(provider_staging_key=None))

    def prepare_provider(self, job_id, token, staging_key, metadata):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(*self._fence(job_id, token), jobs.c.provider_operation_id.is_(None), or_(jobs.c.provider_state.is_(None), jobs.c.provider_state=='prepared')).values(provider_state='prepared', provider_staging_key=staging_key, provider_metadata_json=json.dumps(metadata), updated_at=time.time())).rowcount)

    def begin_provider_submit(self, job_id, token):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(*self._fence(job_id, token), jobs.c.provider_state=='prepared', jobs.c.provider_operation_id.is_(None)).values(provider_state='submitting', provider_submit_token=token, updated_at=time.time())).rowcount)

    def record_provider_operation(self, job_id, token, operation_id):
        # Preserve even a late response after lease expiry. The original submit token is
        # durable and cannot be replaced by a new worker while outcome is uncertain.
        now = time.time()
        with self.engine.begin() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id==job_id, jobs.c.provider_submit_token==token, jobs.c.provider_state=='submitting', jobs.c.provider_operation_id.is_(None))).mappings().first()
            if row is None: return False
            values = dict(provider_operation_id=operation_id, provider_state='polling', updated_at=now)
            if row['status'] != 'running' or (row['lease_until'] or 0) <= now:
                values.update(status='queued', lease_token=None, lease_until=None, available_at=now, error_code=None)
            return bool(conn.execute(update(jobs).where(jobs.c.id==job_id, jobs.c.provider_submit_token==token, jobs.c.provider_operation_id.is_(None)).values(**values)).rowcount)

    def record_provider_response(self, job_id, token, raw, provider_metadata):
        # Sync prerecorded providers have no poll operation. Save the response before
        # parsing so a crash or invalid canonical projection never triggers new billing.
        now=time.time()
        encoded=json.dumps(raw,allow_nan=False)
        with self.engine.begin() as conn:
            row=conn.execute(select(jobs).where(jobs.c.id==job_id,jobs.c.provider_submit_token==token,
                jobs.c.provider_state=='submitting',jobs.c.provider_raw_json.is_(None))).mappings().first()
            if row is None: return False
            values=dict(provider_state='response_saved',provider_raw_json=encoded,
                provider_metadata_json=json.dumps(provider_metadata,allow_nan=False),updated_at=now)
            if row['status']!='running' or (row['lease_until'] or 0)<=now:
                values.update(status='queued',lease_token=None,lease_until=None,available_at=now,error_code=None)
            return bool(conn.execute(update(jobs).where(jobs.c.id==job_id,jobs.c.provider_submit_token==token,
                jobs.c.provider_raw_json.is_(None)).values(**values)).rowcount)

    def record_provider_fetch(self, job_id, token, raw, provider_metadata):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(*self._fence(job_id,token),jobs.c.provider_operation_id.is_not(None)).values(
                provider_state='response_saved',provider_raw_json=json.dumps(raw,allow_nan=False),
                provider_metadata_json=json.dumps(provider_metadata,allow_nan=False),updated_at=time.time())).rowcount)

    def provider_snapshot(self, job_id, token):
        with self.engine.connect() as conn:
            return self._dict(conn.execute(select(jobs).where(*self._fence(job_id, token))).mappings().first())

    def activate(self, job_id, client_id):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(jobs.c.id==job_id,jobs.c.client_id==client_id,jobs.c.status=='uploading').values(status='queued',available_at=time.time(),updated_at=time.time())).rowcount)

    def uploads(self, limit=100, after_id=None):
        with self.engine.connect() as conn:
            query = select(jobs).where(jobs.c.status=='uploading')
            if after_id is not None: query = query.where(jobs.c.id>after_id)
            return [self._dict(row) for row in conn.execute(query.order_by(jobs.c.id).limit(limit)).mappings()]

# Compatibility name for callers using the more specific name.
IdempotencyConflict = RepositoryConflict

class JobNotFound(KeyError):
    pass

