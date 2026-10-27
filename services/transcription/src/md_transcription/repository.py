"""Durable job records with per-tenant idempotency and fenced leases."""
import hashlib
import json
import time
import uuid
from pathlib import Path
from sqlalchemy import event, create_engine, MetaData, Table, Column, String, Text, Integer, Float, UniqueConstraint, select, update
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
    Column('result_json', Text), Column('error_code', String),
    UniqueConstraint('client_id', 'idempotency_key'))

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
        metadata.create_all(self.engine)

    def _dict(self, row):
        if row is None: return None
        value = dict(row)
        value['result'] = json.loads(value.pop('result_json')) if value['result_json'] is not None else None
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
            changed = conn.execute(update(jobs).where(jobs.c.id==job_id, jobs.c.status=='queued', jobs.c.available_at<=now, jobs.c.attempts<max_attempts).values(status='running', lease_token=token, lease_until=now+lease_seconds, attempts=jobs.c.attempts+1, updated_at=now)).rowcount
            if not changed: return None
            return self._dict(conn.execute(select(jobs).where(jobs.c.id==job_id)).mappings().one())

    def _fence(self, job_id, token):
        return (jobs.c.id==job_id, jobs.c.status=='running', jobs.c.lease_token==token, jobs.c.lease_until>time.time())

    def renew(self, job_id, token, lease_seconds):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(*self._fence(job_id,token)).values(lease_until=time.time()+lease_seconds, updated_at=time.time())).rowcount)

    def succeed(self, job_id, token, result):
        encoded = json.dumps(result, allow_nan=False)
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(*self._fence(job_id,token)).values(status='succeeded', result_json=encoded, error_code=None, lease_token=None, lease_until=None, updated_at=time.time())).rowcount)

    def fail(self, job_id, token, code, retryable=False, uncertain=False, max_attempts=3):
        with self.engine.begin() as conn:
            row = conn.execute(select(jobs.c.attempts).where(*self._fence(job_id,token))).first()
            if row is None: return False
            status = 'uncertain' if uncertain else ('queued' if retryable and row.attempts<max_attempts else 'failed')
            return bool(conn.execute(update(jobs).where(*self._fence(job_id,token)).values(status=status, error_code=code, lease_token=None, lease_until=None, updated_at=time.time(), available_at=time.time()+min(300,2**row.attempts))).rowcount)

    def due(self, limit=100, after_id=None):
        now = time.time()
        with self.engine.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.status=='running',jobs.c.lease_until<=now).values(status='uncertain', error_code='worker_lease_expired',lease_token=None,lease_until=None,updated_at=now))
            query = select(jobs.c.id).where(jobs.c.status=='queued',jobs.c.available_at<=now)
            if after_id is not None: query = query.where(jobs.c.id>after_id)
            return list(conn.execute(query.order_by(jobs.c.id).limit(limit)).scalars())

    def retry(self, job_id, client_id):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(jobs).where(jobs.c.id==job_id,jobs.c.client_id==client_id,jobs.c.status.in_(['failed','uncertain'])).values(status='queued', attempts=0,error_code=None,available_at=time.time(),updated_at=time.time())).rowcount)


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

