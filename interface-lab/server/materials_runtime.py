"""Explicit materials composition, local commands and Celery entry points.

Importing this module does not import app.py, connect to a provider, initialize a
database or start Celery. The API can commit jobs while broker delivery is down;
the scheduler republishes durable pending work after recovery.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time

from materials_capture import owner_source_ref
from materials_repository import MaterialsRepository
from materials_worker import MaterialsWorker


def _policy(value):
    value = 'owned' if value == 'copy' else value
    if value not in ('remote', 'owned'):
        raise ValueError('Invalid materials video policy')
    return value


class MaterialsRuntime:
    def __init__(self, connect, repository, captures, storage, *, enabled=True,
                 default_policy='remote', connection_id='daily', publish=None,
                 clock=None, settle_seconds=86400, backfill_enabled=False, **worker_options):
        self.connect, self.repository = connect, repository
        self.captures, self.storage, self.enabled = captures, storage, enabled
        self.default_policy, self.connection_id = _policy(default_policy), connection_id
        self.clock = clock or time.time
        self.settle_seconds = max(0, int(settle_seconds))
        self.backfill_enabled = backfill_enabled
        self.worker = MaterialsWorker(repository, captures, storage, publish=publish, **worker_options)

    def register_owner(self, owner_kind, owner_id, provider='daily', connection_id=None,
                       source_ref=None, storage_policy=None):
        if not self.enabled:
            return None
        source = self.repository.ensure_source(owner_kind, owner_id, provider,
            connection_id or self.connection_id, source_ref or {},
            storage_policy=_policy(storage_policy or self.default_policy))
        if not source['metadata'].get('capture_disabled'):
            self.enqueue_discovery(source['id'])
        return source

    def enqueue_discovery(self, source_id, *, token=None):
        return self.worker.enqueue_discovery(source_id, token=token)

    def upgrade_policy(self, source_id, policy='owned'):
        source = self.repository.update_source_policy(source_id, _policy(policy))
        if not source['metadata'].get('capture_disabled'):
            self.enqueue_discovery(source_id)
        return source

    def _sources(self):
        db = self.connect()
        try:
            ids = [row[0] for row in db.execute('SELECT id FROM media_sources ORDER BY updated_at,id')]
        finally:
            db.close()
        return [self.repository.get_source(identity) for identity in ids]

    def backfill(self, limit=100, dry_run=False):
        """Discover only completed owners with server-stored exclusive room data.

        Legacy reusable rooms without a session are reported ambiguous. No room
        name or session is guessed and no live provider request occurs here.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Invalid materials backfill limit')
        registered = ambiguous = skipped = 0
        db = self.connect()
        try:
            rows = []
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for kind, table in (('interview', 'cp_interviews'), ('meeting', 'cp_meetings')):
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                # Invalid/reusable rooms must not occupy the first page forever.
                clean_room = "CASE WHEN json_valid(p.room) THEN p.room ELSE '{}' END"
                eligible = (f"(json_extract({clean_room},'$.session_id') IS NOT NULL OR "
                    f"json_extract({clean_room},'$.sessionId') IS NOT NULL OR "
                    f"json_extract({clean_room},'$.meetingSessionId') IS NOT NULL OR "
                    f"json_extract({clean_room},'$.mtgSessionId') IS NOT NULL OR "
                    f"json_extract({clean_room},'$.name')='hrv-' || p.id)")
                base = (f"FROM {table} p WHERE status='completed' AND room IS NOT NULL "
                    f"AND NOT EXISTS (SELECT 1 FROM media_sources s WHERE s.{kind}_id=p.id)")
                never_started = f"COALESCE(json_extract({clean_room},'$.captureNeverStarted'),0)<>0"
                if kind == 'interview' and {'cp_intake', 'cp_recording_control'} <= tables:
                    # Legacy prepared introductions may predate the room flag.
                    # Both persisted facts are required; absent control is unknown.
                    never_started += (" OR EXISTS (SELECT 1 FROM cp_intake intake "
                        "JOIN cp_recording_control control ON control.interview_id=p.id "
                        "WHERE intake.candidate_id=p.candidate_id AND control.start_requested_at IS NULL)")
                skipped += db.execute('SELECT count(*) ' + base + ' AND (' + never_started + ')').fetchone()[0]
                base += ' AND NOT (' + never_started + ')'
                ambiguous += db.execute('SELECT count(*) ' + base + ' AND NOT ' + eligible).fetchone()[0]
                rows.extend((kind, *row) for row in db.execute(
                    'SELECT id,room ' + base + ' AND ' + eligible + ' ORDER BY id LIMIT ?', (limit,)))
        finally:
            db.close()
        for kind, identity, body in rows:
            if self.repository.list_sources(kind, identity):
                skipped += 1
                continue
            try:
                room = json.loads(body)
                if room.get('captureNeverStarted'):
                    skipped += 1
                    continue
                session = (room.get('session_id') or room.get('sessionId') or
                           room.get('meetingSessionId') or room.get('mtgSessionId'))
                room_id, name = room.get('id'), room.get('name')
                provider = room.get('provider', 'daily')
                if not room_id or not name or (not session and name != 'hrv-' + identity):
                    ambiguous += 1
                    continue
                connection = room.get('connectionId', self.connection_id)
                ref = owner_source_ref(kind, identity, room, provider, connection)
                if not dry_run:
                    self.register_owner(kind, identity, provider, connection, source_ref=ref)
                registered += 1
            except (ValueError, TypeError, KeyError):
                ambiguous += 1
        return {'registered': 0 if dry_run else registered, 'eligible': registered,
                'ambiguous': ambiguous, 'skipped': skipped, 'dry_run': dry_run}

    def _events(self):
        count = 0
        for key, adapter in self.captures.items():
            if not isinstance(key, tuple):
                continue
            provider, connection = key
            for event in self.repository.list_events(provider, connection):
                payload = event['payload'].get('payload', {})
                session = payload.get('mtg_session_id') or payload.get('mtgSessionId') or payload.get('meeting_id')
                room_id, room_name = payload.get('room_id'), payload.get('room_name')
                matches = []
                for source in self._sources():
                    if ((source['provider'], source['connection_id']) != key or
                            source['metadata'].get('capture_disabled')):
                        continue
                    ref = source['source_ref']
                    if session and ref.get('session_id') == session:
                        matches.append(source)
                    elif room_id and ref.get('room_id') == room_id and not ref.get('session_id'):
                        matches.append(source)
                    elif room_name and ref.get('room_name') == room_name and not ref.get('session_id'):
                        matches.append(source)
                # Hints wake only existing authorized sources. Never create a source
                # from a webhook-controlled owner, room or recording identity.
                for source in matches:
                    self.enqueue_discovery(source['id'], token='event:' + event['id'])
                if matches:
                    self.repository.mark_event(event['id'], 'processed')
                    count += 1
        return count

    def handle_event(self, provider, headers, raw_body):
        if not self.enabled:
            raise ValueError('Materials are disabled')
        adapter = self.captures.get((provider, self.connection_id))
        if adapter is None:
            raise ValueError('Capture provider unconfigured')
        verified = adapter.verify_webhook(raw_body, headers)
        if verified.get('test'):
            return {'accepted': True, 'test': True}
        event = self.repository.record_event(provider, verified['connection_id'],
            verified['event_id'], verified)
        self._events()
        return {'accepted': True, 'event_id': event['id']}

    def reconcile(self):
        if not self.enabled:
            return {'enabled': False}
        backfill = self.backfill() if self.backfill_enabled else {'enabled': False}
        bucket = int(self.clock() // 60)
        rediscovered = 0
        for source in self._sources():
            if source['metadata'].get('capture_disabled') or source['state'] in ('not_recorded', 'unsupported'):
                continue
            # Refresh even ready sources while provider parts settle. Afterwards
            # preserve the honest waiting/error state, without infinite polling;
            # an authenticated event/manual wake can still schedule discovery.
            if self.clock() <= source['created_at'] + self.settle_seconds:
                self.enqueue_discovery(source['id'], token='poll:' + str(bucket))
                rediscovered += 1
        events = self._events()
        return {**self.worker.reconcile(), 'backfill': backfill,
                'rediscovered': rediscovered, 'events': events}


def build_runtime(connect=None, media=None, environ=None, *, publish=None, captures=None, storage=None):
    env = os.environ if environ is None else environ
    enabled = env.get('HR_MATERIALS_ENABLED', '0') == '1'
    root = Path(env.get('HR_DATA_DIR', '/var/lib/hr-vision-api'))
    database = Path(env.get('HR_MATERIALS_DB_PATH', str(root / 'journey.sqlite3')))
    if connect is None:
        def connect():
            db = sqlite3.connect(database, timeout=10)
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=10000')
            return db
    repository = MaterialsRepository(connect)
    if enabled:
        repository.init()
    connection = env.get('HR_MATERIALS_CONNECTION_ID', 'daily')
    if captures is None:
        from materials_daily import DailyCapture
        if media is None:
            from daily_media import DailyMedia
            media = DailyMedia(env.get('DAILY_API_KEY', ''), enabled)
        captures = {('daily', connection): DailyCapture(media, connection,
                    webhook_hmac=env.get('HR_MATERIALS_DAILY_WEBHOOK_HMAC'))}
    if storage is None:
        # Construction is explicit. No default public bucket or signed GET URL.
        backend = env.get('HR_MATERIALS_STORAGE_BACKEND', 'filesystem')
        if backend not in ('s3', 'filesystem'):
            raise ValueError('Invalid materials storage backend')
        if backend == 's3':
            from materials_storage import S3Storage
            storage = S3Storage(env['HR_MATERIALS_S3_BUCKET'],
                prefix=env.get('HR_MATERIALS_S3_PREFIX', 'materials'),
                endpoint_url=env.get('HR_MATERIALS_S3_ENDPOINT'), region_name=env.get('HR_MATERIALS_S3_REGION'))
        else:
            from materials_storage import FileSystemStorage
            storage = FileSystemStorage(env.get('HR_MATERIALS_STORAGE_ROOT', str(root / 'materials')))
    if publish is None:
        publisher = []
        def publish(identity):
            broker = env.get('CELERY_BROKER_URL')
            if not broker:
                raise RuntimeError('Materials broker is unconfigured')
            if not publisher:
                publisher.append(create_celery_app(env))
            app = publisher[0]
            app.send_task('materials.run_job', args=[identity], queue='materials',
                          retry=False)
    return MaterialsRuntime(connect, repository, captures, storage, enabled=enabled,
        default_policy=env.get('HR_MATERIALS_VIDEO_POLICY', 'remote'), connection_id=connection,
        backfill_enabled=env.get('HR_MATERIALS_BACKFILL_ENABLED', '0') == '1',
        settle_seconds=int(env.get('HR_MATERIALS_SETTLE_SECONDS', '86400')),
        publish=publish, lease_seconds=int(env.get('HR_MATERIALS_LEASE_SECONDS', '60')),
        max_bytes=int(env.get('HR_MATERIALS_MAX_DOWNLOAD_BYTES', str(2 * 1024**3))),
        transcript_max_bytes=int(env.get('HR_MATERIALS_TRANSCRIPT_MAX_BYTES', str(20 * 1024**2))),
        timeout_seconds=int(env.get('HR_MATERIALS_DOWNLOAD_TIMEOUT_SECONDS', '300')))


def create_celery_app(environ=None):
    from celery import Celery
    env = os.environ if environ is None else environ
    broker = env.get('CELERY_BROKER_URL')
    if not broker or (broker.startswith('memory:') and env.get('HR_MATERIALS_TEST_MODE') != '1'):
        raise ValueError('A durable materials broker is required')
    app = Celery('materials', broker=broker)
    # RabbitMQ 4.3 rejects Celery's transient, non-exclusive pidbox queue.
    # This worker uses the durable jobs table for coordination, not remote control.
    app.conf.update(task_default_queue='materials', task_serializer='json', accept_content=['json'],
        result_serializer='json', task_ignore_result=True, worker_prefetch_multiplier=1,
        task_acks_late=True, task_reject_on_worker_lost=True,
        worker_enable_remote_control=False, worker_send_task_events=False,
        broker_connection_timeout=3, task_publish_retry=False,
        beat_schedule={'materials-reconcile': {'task': 'materials.reconcile',
                       'schedule': int(env.get('HR_MATERIALS_RECONCILE_SECONDS', '10'))}})

    @app.task(name='materials.run_job')
    def run_job(identity):
        if env.get('HR_MATERIALS_ENABLED', '0') != '1':
            return {'enabled': False}
        return build_runtime(environ=env).worker.run_once(identity)

    @app.task(name='materials.reconcile')
    def reconcile():
        return build_runtime(environ=env).reconcile()
    return app


_celery_app = None


def __getattr__(name):
    global _celery_app
    if name == 'celery_app':
        if _celery_app is None:
            _celery_app = create_celery_app()
        return _celery_app
    raise AttributeError(name)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Durable interview materials jobs')
    parser.add_argument('command', choices=('worker', 'scheduler', 'run-once', 'reconcile', 'backfill'))
    parser.add_argument('--job-id')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if os.environ.get('HR_MATERIALS_ENABLED', '0') != '1':
        raise SystemExit('HR_MATERIALS_ENABLED=1 is required')
    if args.command == 'worker':
        create_celery_app().worker_main(['worker', '--pool=solo', '--concurrency=1', '-Q', 'materials',
            '--without-gossip', '--without-mingle', '--without-heartbeat', '--loglevel=INFO'])
    elif args.command == 'scheduler':
        create_celery_app().start(['beat', '--loglevel=INFO', '--schedule=' +
            os.environ.get('HR_MATERIALS_BEAT_PATH', '/var/lib/hr-vision-api/materials-celerybeat')])
    else:
        runtime = build_runtime()
        if not runtime.enabled:
            raise SystemExit('HR_MATERIALS_ENABLED=1 is required')
        if args.command == 'run-once':
            result = runtime.worker.run_once(args.job_id)
        elif args.command == 'backfill':
            if not 1 <= args.limit <= 1000:
                parser.error('--limit must be between 1 and 1000')
            result = runtime.backfill(args.limit, args.dry_run)
        else:
            result = runtime.reconcile()
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
