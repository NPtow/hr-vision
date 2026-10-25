"""Durable materials catalogue. Business-owner authorization belongs to the caller.

This module has no provider I/O, task execution, signed URLs or global connection.
Every worker publication must supply its job_id and lease_token. They are checked
in the same SQLite transaction as the write, including source-generation fencing.
"""
import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

from domain import Problem


class RepositoryError(Problem):
    def __init__(self, code, status=409):
        self.code = code
        super().__init__(code, status)


def _require(condition, code, status=409):
    if not condition:
        raise RepositoryError(code, status)


def _identifier(value):
    _require(isinstance(value, str) and 0 < len(value) <= 500 and not any(ord(c) < 32 for c in value), 'invalid_identifier', 400)
    _require(not re.search(r'https?://|file://|bearer\s', value, re.I), 'unsafe_reference', 400)
    return value


def _json(value):
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise RepositoryError('invalid_json', 400) from None
    _require(len(result.encode()) <= 2 * 1024 * 1024, 'payload_too_large', 400)
    return result


def _reference(value):
    """Reject credentials and access links, including in nested provider metadata."""
    def check(item, depth=0):
        _require(depth <= 12, 'unsafe_reference', 400)
        if isinstance(item, dict):
            for key, child in item.items():
                _require(isinstance(key, str), 'unsafe_reference', 400)
                normalized = re.sub(r'[^a-z]', '', key.lower())
                _require(not any(word in normalized for word in ('url', 'token', 'secret', 'password', 'authorization', 'cookie', 'credential', 'apikey', 'signature'))
                         and normalized not in ('link', 'downloadlink'), 'unsafe_reference', 400)
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif isinstance(item, str):
            _require(not re.search(r'https?://|file://|bearer\s', item, re.I), 'unsafe_reference', 400)
        else:
            _require(item is None or isinstance(item, (bool, int, float)), 'unsafe_reference', 400)
    check(value)
    return _json(value)


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _policy(value):
    value = 'owned' if value == 'copy' else value
    _require(value in ('remote', 'owned'), 'invalid_storage_policy', 400)
    return value


ASSET_STATES = ('pending', 'processing', 'ready', 'error', 'missing', 'unsupported', 'not_recorded')
SOURCE_STATES = ('pending', 'processing', 'ready', 'partial', 'error', 'missing', 'awaiting_provider', 'unsupported', 'not_recorded')


class MaterialsRepository:
    def __init__(self, connect, clock=None):
        self.connect = connect
        self.clock = clock or time.time

    @contextmanager
    def _db(self, *, write=False):
        db = self.connect()
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def init(self, db=None):
        if db is None:
            with self._db(write=True) as connection:
                self.init(connection)
            return
        # execute(), not executescript(): never commit a caller's transaction.
        statements = [
            '''CREATE TABLE IF NOT EXISTS media_sources (
                id TEXT PRIMARY KEY, interview_id TEXT REFERENCES cp_interviews(id),
                meeting_id TEXT REFERENCES cp_meetings(id), provider TEXT NOT NULL,
                connection_id TEXT NOT NULL, capture_identity TEXT NOT NULL,
                parent_source_id TEXT REFERENCES media_sources(id),
                source_ref TEXT NOT NULL, storage_policy TEXT NOT NULL,
                generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>0),
                state TEXT NOT NULL DEFAULT 'pending', metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                CHECK((interview_id IS NOT NULL)+(meeting_id IS NOT NULL)=1),
                UNIQUE(provider,connection_id,capture_identity))''',
            '''CREATE TABLE IF NOT EXISTS media_assets (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES media_sources(id),
                kind TEXT NOT NULL, provider_asset_id TEXT NOT NULL,
                state TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(source_id,kind,provider_asset_id))''',
            '''CREATE TABLE IF NOT EXISTS asset_locations (
                id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES media_assets(id),
                backend TEXT NOT NULL, object_key TEXT NOT NULL, state TEXT NOT NULL,
                size INTEGER, sha256 TEXT, metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(asset_id,backend,object_key))''',
            '''CREATE TABLE IF NOT EXISTS transcript_versions (
                id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES media_assets(id),
                version INTEGER NOT NULL CHECK(version>0), raw_ref TEXT NOT NULL,
                segments TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
                content_sha256 TEXT NOT NULL, created_at REAL NOT NULL,
                UNIQUE(asset_id,version), UNIQUE(asset_id,content_sha256))''',
            '''CREATE TRIGGER IF NOT EXISTS transcript_versions_immutable
                BEFORE UPDATE ON transcript_versions BEGIN
                  SELECT RAISE(ABORT,'transcript_versions are immutable'); END''',
            '''CREATE TABLE IF NOT EXISTS media_jobs (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES media_sources(id),
                generation INTEGER NOT NULL, operation TEXT NOT NULL,
                input_fingerprint TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL CHECK(max_attempts>0), next_attempt REAL NOT NULL,
                lease_token TEXT, lease_until REAL, worker_id TEXT,
                result TEXT, last_error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(source_id,generation,operation,input_fingerprint))''',
            '''CREATE INDEX IF NOT EXISTS media_jobs_due ON media_jobs(status,next_attempt,lease_until)''',
            '''CREATE TABLE IF NOT EXISTS media_attempts (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES media_jobs(id),
                attempt INTEGER NOT NULL, generation INTEGER NOT NULL,
                lease_token TEXT NOT NULL UNIQUE, worker_id TEXT NOT NULL,
                status TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL,
                error TEXT, UNIQUE(job_id,attempt))''',
            '''CREATE TABLE IF NOT EXISTS media_events (
                id TEXT PRIMARY KEY, provider TEXT NOT NULL, connection_id TEXT NOT NULL,
                event_id TEXT NOT NULL, payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
                source_id TEXT REFERENCES media_sources(id), state TEXT NOT NULL DEFAULT 'pending',
                received_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(provider,connection_id,event_id))''',
        ]
        for statement in statements:
            db.execute(statement)
        if 'parent_source_id' not in {row[1] for row in db.execute('PRAGMA table_info(media_sources)')}:
            db.execute('ALTER TABLE media_sources ADD COLUMN parent_source_id TEXT REFERENCES media_sources(id)')

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        result = dict(row)
        for key in ('source_ref', 'metadata', 'raw_ref', 'segments', 'payload', 'result'):
            if key in result and result[key] is not None:
                result[key] = json.loads(result[key])
        if 'interview_id' in result:
            result['owner_kind'] = 'interview' if result['interview_id'] is not None else 'meeting'
            result['owner_id'] = result['interview_id'] or result['meeting_id']
        return result

    @classmethod
    def _one(cls, db, table, identity, code='not_found'):
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone()
        _require(row is not None, code, 404)
        return cls._decode(row)

    def get_source(self, source_id):
        with self._db() as db:
            return self._one(db, 'media_sources', source_id, 'source_not_found')

    def list_sources(self, owner_kind, owner_id):
        _require(owner_kind in ('interview', 'meeting'), 'invalid_owner', 400)
        with self._db() as db:
            return [self._decode(row) for row in db.execute(
                f'SELECT * FROM media_sources WHERE {owner_kind}_id=? ORDER BY created_at,id', (owner_id,))]

    owner_sources = list_sources

    def ensure_source(self, owner_kind, owner_id, provider, connection_id, source_ref, storage_policy='remote',
                      *, job_id=None, lease_token=None):
        _require(owner_kind in ('interview', 'meeting'), 'invalid_owner', 400)
        for value in (owner_id, provider, connection_id, storage_policy):
            _identifier(value)
        storage_policy = _policy(storage_policy)
        _require(isinstance(source_ref, dict), 'invalid_source_ref', 400)
        ref = dict(source_ref)
        aliases = {'roomId': 'room_id', 'roomName': 'room_name', 'sessionId': 'session_id',
                   'meetingSessionId': 'session_id', 'captureInstanceId': 'capture_instance_id',
                   'recordingInstanceId': 'recording_instance_id'}
        for old, new in aliases.items():
            if old in ref:
                _require(new not in ref or ref[new] == ref[old], 'source_identity_conflict')
                ref[new] = ref.pop(old)
        encoded = _reference(ref)
        capture_key = next((key for key in ('session_id', 'capture_instance_id', 'recording_instance_id') if ref.get(key)), None)
        capture = ref[capture_key] if capture_key else None
        _require(capture is not None, 'capture_identity_required', 400)
        capture = capture_key + ':' + _identifier(capture)
        with self._db(write=True) as db:
            parent_source_id = None
            _require((job_id is None) == (lease_token is None), 'lease_required', 400)
            if job_id is not None:
                parent_job = self._lease(db, job_id, lease_token)
                parent = self._one(db, 'media_sources', parent_job['source_id'])
                _require((parent['owner_kind'], parent['owner_id'], parent['provider'], parent['connection_id']) ==
                         (owner_kind, owner_id, provider, connection_id), 'source_identity_conflict')
                parent_source_id = parent['id']
                # Policy is owner-controlled; a worker cannot choose weaker retention.
                storage_policy = parent['storage_policy']
            _require(db.execute(f'SELECT 1 FROM cp_{"interviews" if owner_kind == "interview" else "meetings"} WHERE id=?',
                                (owner_id,)).fetchone() is not None, 'owner_not_found', 404)
            row = db.execute('SELECT * FROM media_sources WHERE provider=? AND connection_id=? AND capture_identity=?',
                             (provider, connection_id, capture)).fetchone()
            if row is not None:
                source = self._decode(row)
                _require(source['owner_kind'] == owner_kind and source['owner_id'] == owner_id,
                         'source_identity_conflict')
                # Identity is immutable. Enrichment may add a previously unknown session.
                old_ref = source['source_ref']
                _require(all(key not in old_ref or old_ref[key] == value for key, value in ref.items()), 'source_identity_conflict')
                if parent_source_id is not None and parent_source_id != source['id']:
                    _require(source['parent_source_id'] in (None, parent_source_id), 'source_identity_conflict')
                    if source['parent_source_id'] is None:
                        db.execute('UPDATE media_sources SET parent_source_id=? WHERE id=?', (parent_source_id, source['id']))
                merged = {**old_ref, **ref}
                if merged != old_ref:
                    db.execute('UPDATE media_sources SET source_ref=?,updated_at=? WHERE id=?',
                               (_reference(merged), self.clock(), source['id']))
                return self._one(db, 'media_sources', source['id'])
            identity, stamp = secrets.token_hex(16), self.clock()
            db.execute('''INSERT INTO media_sources
                (id,interview_id,meeting_id,provider,connection_id,capture_identity,source_ref,storage_policy,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (identity, owner_id if owner_kind == 'interview' else None, owner_id if owner_kind == 'meeting' else None,
                 provider, connection_id, capture, encoded, storage_policy, stamp, stamp))
            if parent_source_id is not None:
                db.execute('UPDATE media_sources SET parent_source_id=? WHERE id=?', (parent_source_id, identity))
            return self._one(db, 'media_sources', identity)

    def _fence(self, db, source_id, job_id, lease_token):
        _require((job_id is None) == (lease_token is None), 'lease_required', 400)
        if job_id is not None:
            job = self._lease(db, job_id, lease_token)
            _require(job['source_id'] == source_id, 'stale_lease')

    def _lease(self, db, job_id, lease_token):
        job = self._one(db, 'media_jobs', job_id, 'job_not_found')
        source = self._one(db, 'media_sources', job['source_id'], 'source_not_found')
        _require(job['status'] == 'running' and job['lease_token'] == lease_token
                 and job['lease_until'] is not None and job['lease_until'] > self.clock()
                 and job['generation'] == source['generation'] and not source['metadata'].get('capture_disabled'), 'stale_lease')
        return job

    def set_source_state(self, source_id, state, metadata=None, *, job_id=None, lease_token=None):
        _require(state in SOURCE_STATES, 'invalid_state', 400)
        metadata = metadata or {}
        _reference(metadata)
        _require(state != 'missing' or metadata.get('explicit_missing') is True, 'missing_not_confirmed')
        with self._db(write=True) as db:
            self._fence(db, source_id, job_id, lease_token)
            source = self._one(db, 'media_sources', source_id, 'source_not_found')
            db.execute('UPDATE media_sources SET state=?,metadata=?,updated_at=? WHERE id=?',
                       (state, _reference({**source['metadata'], **metadata}), self.clock(), source_id))
            return self._one(db, 'media_sources', source_id)

    def _invalidate(self, db, source_id):
        self._one(db, 'media_sources', source_id, 'source_not_found')
        stamp = self.clock()
        db.execute('UPDATE media_sources SET generation=generation+1,updated_at=? WHERE id=?', (stamp, source_id))
        db.execute('''UPDATE media_attempts SET status='cancelled',finished_at=? WHERE status='running'
            AND job_id IN (SELECT id FROM media_jobs WHERE source_id=?)''', (stamp, source_id))
        db.execute('''UPDATE media_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,updated_at=?
            WHERE source_id=? AND status IN ('pending','retry','running')''', (stamp, source_id))
        return self._one(db, 'media_sources', source_id)

    def invalidate_source(self, source_id):
        with self._db(write=True) as db:
            return self._invalidate(db, source_id)

    def update_source_policy(self, source_id, storage_policy):
        storage_policy = _policy(storage_policy)
        with self._db(write=True) as db:
            source = self._one(db, 'media_sources', source_id, 'source_not_found')
            if source['storage_policy'] != storage_policy:
                self._invalidate(db, source_id)
                db.execute('UPDATE media_sources SET storage_policy=? WHERE id=?', (storage_policy, source_id))
            return self._one(db, 'media_sources', source_id)

    def invalidate_owner(self, owner_kind, owner_id):
        """Fence root and session children together; never cancel by candidate ID."""
        _require(owner_kind in ('interview', 'meeting'), 'invalid_owner', 400)
        with self._db(write=True) as db:
            identities = [row[0] for row in db.execute(f'SELECT id FROM media_sources WHERE {owner_kind}_id=?', (owner_id,))]
            return [self._invalidate(db, identity) for identity in identities]

    def _suspend(self, db, source_id, reason):
        source = self._invalidate(db, source_id)
        metadata = {**source['metadata'], 'capture_disabled': True, 'disabled_reason': reason}
        db.execute("UPDATE media_sources SET state='unsupported',metadata=? WHERE id=?", (_reference(metadata), source_id))
        return self._one(db, 'media_sources', source_id)

    def suspend_source(self, source_id, reason='revoked'):
        _identifier(reason)
        with self._db(write=True) as db:
            return self._suspend(db, source_id, reason)

    def suspend_owner(self, owner_kind, owner_id, reason='revoked'):
        _require(owner_kind in ('interview', 'meeting'), 'invalid_owner', 400)
        _identifier(reason)
        with self._db(write=True) as db:
            identities = [row[0] for row in db.execute(f'SELECT id FROM media_sources WHERE {owner_kind}_id=?', (owner_id,))]
            return [self._suspend(db, identity, reason) for identity in identities]

    def update_owner_policy(self, owner_kind, owner_id, storage_policy):
        _require(owner_kind in ('interview', 'meeting'), 'invalid_owner', 400)
        storage_policy = _policy(storage_policy)
        with self._db(write=True) as db:
            identities = [row[0] for row in db.execute(f'SELECT id FROM media_sources WHERE {owner_kind}_id=? AND storage_policy<>?',
                                                     (owner_id, storage_policy))]
            for identity in identities:
                self._invalidate(db, identity)
                db.execute('UPDATE media_sources SET storage_policy=? WHERE id=?', (storage_policy, identity))
            return [self._decode(row) for row in db.execute(f'SELECT * FROM media_sources WHERE {owner_kind}_id=? ORDER BY created_at,id', (owner_id,))]

    def get_asset(self, asset_id):
        with self._db() as db:
            return self._one(db, 'media_assets', asset_id, 'asset_not_found')

    def list_assets(self, source_id):
        with self._db() as db:
            return [self._decode(row) for row in db.execute('SELECT * FROM media_assets WHERE source_id=? ORDER BY created_at,id', (source_id,))]

    def upsert_asset(self, source_id, kind, provider_asset_id, state='pending', metadata=None, *, job_id=None, lease_token=None):
        _identifier(kind)
        _identifier(provider_asset_id)
        _require(state in ASSET_STATES, 'invalid_state', 400)
        metadata = metadata or {}
        _reference(metadata)
        _require(state != 'missing' or metadata.get('explicit_missing') is True, 'missing_not_confirmed')
        with self._db(write=True) as db:
            self._fence(db, source_id, job_id, lease_token)
            self._one(db, 'media_sources', source_id, 'source_not_found')
            row = db.execute('SELECT * FROM media_assets WHERE source_id=? AND kind=? AND provider_asset_id=?',
                             (source_id, kind, provider_asset_id)).fetchone()
            stamp = self.clock()
            if row is not None:
                prior = self._decode(row)
                # An eventually consistent list must not downgrade a usable object.
                effective = prior['state'] if prior['state'] == 'ready' and state in ('pending', 'processing') else state
                db.execute('UPDATE media_assets SET state=?,metadata=?,updated_at=? WHERE id=?',
                           (effective, _reference({**prior['metadata'], **metadata}), stamp, prior['id']))
                identity = prior['id']
            else:
                identity = secrets.token_hex(16)
                db.execute('INSERT INTO media_assets VALUES (?,?,?,?,?,?,?,?)',
                           (identity, source_id, kind, provider_asset_id, state, _reference(metadata), stamp, stamp))
            return self._one(db, 'media_assets', identity)

    def add_location(self, asset_id, backend, object_key, state='pending', size=None, sha256=None, metadata=None,
                     *, job_id=None, lease_token=None):
        _identifier(backend)
        _identifier(object_key)
        _require(state in ('pending', 'available', 'verified', 'error', 'missing', 'deleted'), 'invalid_state', 400)
        _require(size is None or type(size) is int and size >= 0, 'invalid_location', 400)
        _require(sha256 is None or isinstance(sha256, str) and re.fullmatch('[0-9a-f]{64}', sha256), 'invalid_location', 400)
        _require(state != 'verified' or size is not None and sha256 is not None, 'invalid_location', 400)
        metadata = metadata or {}
        _reference(metadata)
        with self._db(write=True) as db:
            asset = self._one(db, 'media_assets', asset_id, 'asset_not_found')
            self._fence(db, asset['source_id'], job_id, lease_token)
            row = db.execute('SELECT * FROM asset_locations WHERE asset_id=? AND backend=? AND object_key=?',
                             (asset_id, backend, object_key)).fetchone()
            stamp = self.clock()
            if row is not None:
                prior = self._decode(row)
                if prior['state'] == 'verified' and state in ('pending', 'available'):
                    state, size, sha256 = prior['state'], prior['size'], prior['sha256']
                if prior['sha256'] and sha256:
                    _require(prior['sha256'] == sha256, 'location_content_conflict')
                identity = prior['id']
                db.execute('UPDATE asset_locations SET state=?,size=?,sha256=?,metadata=?,updated_at=? WHERE id=?',
                           (state, size if size is not None else prior['size'], sha256 or prior['sha256'],
                            _reference({**prior['metadata'], **metadata}), stamp, identity))
            else:
                identity = secrets.token_hex(16)
                db.execute('INSERT INTO asset_locations VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (identity, asset_id, backend, object_key, state, size, sha256, _reference(metadata), stamp, stamp))
            return self._one(db, 'asset_locations', identity)

    def list_locations(self, asset_id):
        with self._db() as db:
            return [self._decode(row) for row in db.execute('SELECT * FROM asset_locations WHERE asset_id=? ORDER BY created_at,id', (asset_id,))]

    def save_transcript(self, asset_id, raw_ref, segments, metadata=None, *, job_id=None, lease_token=None):
        _require(isinstance(raw_ref, dict) and raw_ref, 'raw_reference_required', 400)
        raw = _reference(raw_ref)
        _require(isinstance(segments, list) and len(segments) <= 100000, 'invalid_segments', 400)
        for segment in segments:
            _require(isinstance(segment, dict) and isinstance(segment.get('text'), str), 'invalid_segments', 400)
            for key in ('start_ms', 'end_ms', 'startMs', 'endMs'):
                value = segment.get(key)
                _require(value is None or type(value) in (int, float) and math.isfinite(value) and value >= 0,
                         'invalid_segments', 400)
            start, end = segment.get('start_ms', segment.get('startMs')), segment.get('end_ms', segment.get('endMs'))
            _require(start is None or end is None or end >= start, 'invalid_segments', 400)
        # Transcript content is untrusted data, not a storage reference. A person
        # can say a URL or discuss tokens/passwords without making it a secret.
        # Only the small processing-provenance envelope uses reference checks.
        metadata = metadata or {}
        _require(isinstance(metadata, dict), 'invalid_metadata', 400)
        allowed = {'origin', 'source_provider', 'language', 'parser_version', 'model_version',
                   'schema_version', 'format', 'warnings', 'normalized'}
        _require(set(metadata).issubset(allowed), 'invalid_transcript_provenance', 400)
        provenance = {key: value for key, value in metadata.items() if key != 'normalized'}
        _reference(provenance)
        if 'normalized' in metadata:
            _require(isinstance(metadata['normalized'], dict), 'invalid_metadata', 400)
        normalized, meta = _json(segments), _json(metadata)
        fingerprint = _digest(_json({'raw_ref': raw_ref, 'segments': segments, 'metadata': metadata or {}}))
        with self._db(write=True) as db:
            asset = self._one(db, 'media_assets', asset_id, 'asset_not_found')
            _require(asset['kind'] in ('transcript', 'captions', 'subtitle'), 'invalid_transcript_asset', 400)
            self._fence(db, asset['source_id'], job_id, lease_token)
            row = db.execute('SELECT * FROM transcript_versions WHERE asset_id=? AND content_sha256=?', (asset_id, fingerprint)).fetchone()
            if row is not None:
                return self._decode(row)
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM transcript_versions WHERE asset_id=?', (asset_id,)).fetchone()[0]
            identity = secrets.token_hex(16)
            db.execute('INSERT INTO transcript_versions VALUES (?,?,?,?,?,?,?,?)',
                       (identity, asset_id, version, raw, normalized, meta, fingerprint, self.clock()))
            return self._one(db, 'transcript_versions', identity)

    def get_transcript(self, transcript_id):
        with self._db() as db:
            return self._one(db, 'transcript_versions', transcript_id, 'transcript_not_found')

    def list_transcripts(self, source_id):
        with self._db() as db:
            return [self._decode(row) for row in db.execute('''SELECT t.* FROM transcript_versions t
                JOIN media_assets a ON a.id=t.asset_id WHERE a.source_id=? ORDER BY t.created_at,t.version,t.id''', (source_id,))]

    def enqueue_job(self, source_id, operation, input_fingerprint='', payload=None, max_attempts=5,
                    *, job_id=None, lease_token=None):
        _identifier(operation)
        _require(isinstance(input_fingerprint, str) and len(input_fingerprint) <= 1000, 'invalid_fingerprint', 400)
        _reference(input_fingerprint)
        encoded = _reference(payload or {})
        _require(type(max_attempts) is int and 1 <= max_attempts <= 100, 'invalid_attempts', 400)
        with self._db(write=True) as db:
            source = self._one(db, 'media_sources', source_id, 'source_not_found')
            _require(not source['metadata'].get('capture_disabled'), 'source_disabled')
            _require((job_id is None) == (lease_token is None), 'lease_required', 400)
            if job_id is not None:
                parent_job = self._lease(db, job_id, lease_token)
                parent = self._one(db, 'media_sources', parent_job['source_id'])
                _require(source_id == parent['id'] or source['parent_source_id'] == parent['id'], 'stale_lease')
                _require((source['owner_kind'], source['owner_id'], source['provider'], source['connection_id']) ==
                         (parent['owner_kind'], parent['owner_id'], parent['provider'], parent['connection_id']), 'source_identity_conflict')
            row = db.execute('''SELECT * FROM media_jobs WHERE source_id=? AND generation=? AND operation=? AND input_fingerprint=?''',
                             (source_id, source['generation'], operation, input_fingerprint)).fetchone()
            if row is not None:
                job = self._decode(row)
                _require(job['payload'] == (payload or {}), 'job_identity_conflict')
                return job
            identity, stamp = secrets.token_hex(16), self.clock()
            db.execute('''INSERT INTO media_jobs
                (id,source_id,generation,operation,input_fingerprint,payload,max_attempts,next_attempt,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (identity, source_id, source['generation'], operation, input_fingerprint, encoded, max_attempts, stamp, stamp, stamp))
            return self._one(db, 'media_jobs', identity)

    def get_job(self, job_id):
        with self._db() as db:
            return self._one(db, 'media_jobs', job_id, 'job_not_found')

    def list_jobs(self, source_id):
        with self._db() as db:
            return [self._decode(row) for row in db.execute('SELECT * FROM media_jobs WHERE source_id=? ORDER BY created_at,id', (source_id,))]

    def list_pending_jobs(self, limit=100):
        """Due job IDs can be delivered repeatedly; claim_job is authoritative."""
        _require(type(limit) is int and 1 <= limit <= 1000, 'invalid_limit', 400)
        with self._db() as db:
            return [self._decode(row) for row in db.execute('''SELECT j.* FROM media_jobs j
                JOIN media_sources s ON s.id=j.source_id WHERE j.generation=s.generation
                AND COALESCE(json_extract(s.metadata,'$.capture_disabled'),0)<>1
                AND j.attempts<j.max_attempts AND ((j.status IN ('pending','retry') AND j.next_attempt<=?)
                OR (j.status='running' AND j.lease_until<=?)) ORDER BY j.next_attempt,j.created_at,j.id LIMIT ?''',
                (self.clock(), self.clock(), limit))]

    def _reconcile(self, db):
        stamp = self.clock()
        expired = list(db.execute("SELECT id,lease_token,attempts,max_attempts FROM media_jobs WHERE status='running' AND lease_until<=?", (stamp,)))
        for row in expired:
            db.execute("UPDATE media_attempts SET status='expired',finished_at=? WHERE lease_token=? AND status='running'", (stamp, row['lease_token']))
            db.execute('''UPDATE media_jobs SET status=?,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=?
                WHERE id=?''', ('failed' if row['attempts'] >= row['max_attempts'] else 'retry', stamp, row['id']))
        # A generation is authoritative even after a restart or manual migration.
        db.execute('''UPDATE media_attempts SET status='cancelled',finished_at=? WHERE status='running' AND job_id IN
            (SELECT j.id FROM media_jobs j JOIN media_sources s ON s.id=j.source_id WHERE j.generation<>s.generation)''', (stamp,))
        db.execute('''UPDATE media_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,updated_at=?
            WHERE status IN ('pending','retry','running') AND EXISTS
            (SELECT 1 FROM media_sources s WHERE s.id=media_jobs.source_id AND s.generation<>media_jobs.generation)''', (stamp,))
        return len(expired)

    def reconcile_jobs(self):
        with self._db(write=True) as db:
            return self._reconcile(db)

    def claim_job(self, worker_id, lease_seconds=60, job_id=None):
        _identifier(worker_id)
        _require(type(lease_seconds) in (int, float) and 0 < lease_seconds <= 86400, 'invalid_lease', 400)
        with self._db(write=True) as db:
            self._reconcile(db)
            query = '''SELECT j.* FROM media_jobs j JOIN media_sources s ON s.id=j.source_id
                WHERE j.status IN ('pending','retry') AND j.next_attempt<=? AND j.attempts<j.max_attempts
                AND j.generation=s.generation AND COALESCE(json_extract(s.metadata,'$.capture_disabled'),0)<>1'''
            params = [self.clock()]
            if job_id is not None:
                query += ' AND j.id=?'
                params.append(job_id)
            row = db.execute(query + ' ORDER BY j.next_attempt,j.created_at,j.id LIMIT 1', params).fetchone()
            if row is None:
                return None
            stamp, token = self.clock(), secrets.token_hex(24)
            db.execute('''UPDATE media_jobs SET status='running',attempts=attempts+1,lease_token=?,lease_until=?,worker_id=?,updated_at=?
                WHERE id=?''', (token, stamp + lease_seconds, worker_id, stamp, row['id']))
            db.execute('''INSERT INTO media_attempts
                (id,job_id,attempt,generation,lease_token,worker_id,status,started_at) VALUES (?,?,?,?,?,?,'running',?)''',
                (secrets.token_hex(16), row['id'], row['attempts'] + 1, row['generation'], token, worker_id, stamp))
            return self._one(db, 'media_jobs', row['id'])

    def renew_job(self, job_id, lease_token, lease_seconds=60):
        _require(type(lease_seconds) in (int, float) and 0 < lease_seconds <= 86400, 'invalid_lease', 400)
        with self._db(write=True) as db:
            self._lease(db, job_id, lease_token)
            db.execute('UPDATE media_jobs SET lease_until=?,updated_at=? WHERE id=?',
                       (self.clock() + lease_seconds, self.clock(), job_id))
            return self._one(db, 'media_jobs', job_id)

    def finish_job(self, job_id, lease_token, result=None):
        encoded = _reference(result or {})
        with self._db(write=True) as db:
            self._lease(db, job_id, lease_token)
            stamp = self.clock()
            db.execute("UPDATE media_attempts SET status='succeeded',finished_at=? WHERE lease_token=?", (stamp, lease_token))
            db.execute('''UPDATE media_jobs SET status='succeeded',result=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?''',
                       (encoded, stamp, job_id))
            return self._one(db, 'media_jobs', job_id)

    def fail_job(self, job_id, lease_token, error, retryable=True, retry_after=30):
        # Error codes only. Never persist arbitrary HTTP exception strings.
        _require(isinstance(error, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', error), 'invalid_error_code', 400)
        _require(type(retry_after) in (int, float) and 0 <= retry_after <= 604800, 'invalid_retry_delay', 400)
        with self._db(write=True) as db:
            job = self._lease(db, job_id, lease_token)
            state = 'retry' if retryable and job['attempts'] < job['max_attempts'] else 'failed'
            stamp = self.clock()
            db.execute("UPDATE media_attempts SET status='failed',finished_at=?,error=? WHERE lease_token=?", (stamp, error, lease_token))
            db.execute('''UPDATE media_jobs SET status=?,last_error=?,next_attempt=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?''',
                       (state, error, stamp + retry_after, stamp, job_id))
            return self._one(db, 'media_jobs', job_id)

    def cancel_job(self, job_id):
        with self._db(write=True) as db:
            job = self._one(db, 'media_jobs', job_id, 'job_not_found')
            if job['status'] in ('pending', 'retry', 'running'):
                stamp = self.clock()
                db.execute("UPDATE media_attempts SET status='cancelled',finished_at=? WHERE job_id=? AND status='running'", (stamp, job_id))
                db.execute("UPDATE media_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (stamp, job_id))
            return self._one(db, 'media_jobs', job_id)

    def list_attempts(self, job_id):
        with self._db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM media_attempts WHERE job_id=? ORDER BY attempt', (job_id,))]

    def record_event(self, provider, connection_id, event_id, payload, source_id=None):
        for value in (provider, connection_id, event_id):
            _identifier(value)
        encoded = _reference(payload)
        fingerprint = _digest(encoded)
        with self._db(write=True) as db:
            if source_id is not None:
                source = self._one(db, 'media_sources', source_id, 'source_not_found')
                _require((provider, connection_id) == (source['provider'], source['connection_id']), 'event_source_mismatch')
            row = db.execute('SELECT * FROM media_events WHERE provider=? AND connection_id=? AND event_id=?',
                             (provider, connection_id, event_id)).fetchone()
            if row is not None:
                _require(row['payload_digest'] == fingerprint and (source_id is None or row['source_id'] in (None, source_id)), 'event_identity_conflict')
                return self._decode(row)
            identity, stamp = secrets.token_hex(16), self.clock()
            db.execute('''INSERT INTO media_events
                (id,provider,connection_id,event_id,payload,payload_digest,source_id,received_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)''', (identity, provider, connection_id, event_id, encoded, fingerprint, source_id, stamp, stamp))
            return self._one(db, 'media_events', identity)

    def list_events(self, provider, connection_id, state='pending', limit=100):
        _require(type(limit) is int and 1 <= limit <= 1000, 'invalid_limit', 400)
        with self._db() as db:
            return [self._decode(row) for row in db.execute('''SELECT * FROM media_events
                WHERE provider=? AND connection_id=? AND state=? ORDER BY received_at,id LIMIT ?''',
                (provider, connection_id, state, limit))]

    def mark_event(self, event_id, state='processed', source_id=None):
        _require(state in ('pending', 'processed', 'unmatched', 'error'), 'invalid_state', 400)
        with self._db(write=True) as db:
            event = self._one(db, 'media_events', event_id, 'event_not_found')
            if source_id is not None:
                source = self._one(db, 'media_sources', source_id, 'source_not_found')
                _require((event['provider'], event['connection_id']) == (source['provider'], source['connection_id']), 'event_source_mismatch')
                _require(event['source_id'] in (None, source_id), 'event_identity_conflict')
            db.execute('UPDATE media_events SET state=?,source_id=COALESCE(?,source_id),updated_at=? WHERE id=?',
                       (state, source_id, self.clock(), event_id))
            return self._one(db, 'media_events', event_id)
