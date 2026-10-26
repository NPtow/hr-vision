"""Opt-in, durable reconciliation of ready candidate corpora.

Runs in the trusted HR backend, independently of browser reads. It owns only
command/cursor metadata in the HR DB; the assessment service owns model work.
No source text, credentials or arbitrary exception messages are persisted here.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import fcntl
import os
import re
import sqlite3
import threading
import time

import assessment_core_bridge as bridge_module
from assessment_corpus import CollectionError
from domain import Problem


TERMINAL = {'completed', 'failed', 'outcome_unknown', 'dispatch_unknown',
            'superseded', 'poll_failed'}


@dataclass(frozen=True)
class Scope:
    candidate_ids: tuple = ()
    created_since: str | None = None
    batch_size: int = 10
    interval: float = 60

    def __post_init__(self):
        if (not self.candidate_ids and not self.created_since or len(self.candidate_ids) > 1000
                or not 1 <= self.batch_size <= 100 or not 1 <= self.interval <= 3600
                or any(not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', cid)
                       for cid in self.candidate_ids)):
            raise ValueError('invalid_automatic_assessment_scope')
        if self.created_since:
            try:
                date = dt.datetime.fromisoformat(self.created_since.replace('Z', '+00:00'))
                if date.utcoffset() != dt.timedelta(0):
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError('automatic_assessment_since_requires_utc_timestamp') from None


def configured_scope():
    if os.environ.get('HR_ASSESSMENT_CORE_AUTO_ENABLED') != '1':
        return None
    try:
        ids = tuple(sorted(set(x.strip() for x in os.environ.get(
            'HR_ASSESSMENT_CORE_AUTO_CANDIDATE_IDS', '').split(',') if x.strip())))
        return Scope(ids, os.environ.get('HR_ASSESSMENT_CORE_AUTO_CREATED_SINCE') or None,
                     int(os.environ.get('HR_ASSESSMENT_CORE_AUTO_BATCH_SIZE', '10')),
                     float(os.environ.get('HR_ASSESSMENT_CORE_AUTO_INTERVAL_SECONDS', '60')))
    except (ValueError, TypeError):
        raise Problem('automatic_assessment_scope_configuration_error', 503) from None


def ready_hr_interview(snapshot):
    """Collector already verifies primary selections for EVERY recording part.

    A closed room, audio alone, absent recording or partial transcript cannot
    establish the HR interview criterion. Other corpus sources still must be
    ready before any assessment command is created.
    """
    interviews = [item for item in snapshot.get('manifest', {}).get('conversations', [])
                  if item.get('owner_kind') == 'interview' and
                  (item.get('recordings') or item.get('status') not in ('not_conducted', 'not_recorded'))]
    parts = [part for item in interviews for part in item.get('recordings', [])]
    return (bool(interviews) and any(part.get('kind') == 'video' for part in parts) and
            all(item.get('lifecycle_status') == 'completed' and item.get('status') == 'ready' and
                item.get('primary_status') == 'ready' and item.get('recordings') for item in interviews) and
            all((part.get('selection') or {}).get('status') == 'ready' and part.get('transcript_id') for part in parts))


def init(db):
    bridge_module.init(db)
    db.execute('''CREATE TABLE IF NOT EXISTS ac_auto_cursor (
      scope_hash TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, updated REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS ac_auto_candidates (
      candidate_id TEXT PRIMARY KEY REFERENCES cp_candidates(id), source_revision TEXT,
      work_id TEXT, state TEXT NOT NULL, error_code TEXT, updated REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS ac_auto_work (
      id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
      source_revision TEXT NOT NULL, rubric_hash TEXT NOT NULL, model TEXT NOT NULL,
      request_format TEXT NOT NULL, state TEXT NOT NULL, run_id TEXT, error_code TEXT,
      poll_errors INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS ac_auto_work_state ON ac_auto_work(state,updated,id)')


class Lifecycle:
    def __init__(self, bridge, scope):
        self.bridge, self.scope = bridge, scope
        self.scope_hash = bridge_module.digest([sorted(scope.candidate_ids), scope.created_since])
        with bridge.lock, bridge.connect() as db:
            init(db)
            db.execute('INSERT OR IGNORE INTO ac_auto_cursor VALUES (?,?,?)',
                       (self.scope_hash, '', time.time()))

    def recover(self):
        """Startup-only, under runtime ownership. Never repeat an uncertain POST.

        An acknowledged dispatch can be resumed by safe GETs. An unacknowledged
        dispatch requires an explicit operator command; the same core run ID
        remains idempotent even if an operator elects to retry that command.
        """
        with self.bridge.lock, self.bridge.connect() as db:
            db.execute('''UPDATE ac_auto_work SET
              state=CASE WHEN EXISTS(SELECT 1 FROM ac_bindings b WHERE b.id=ac_auto_work.id
                AND b.run_id IS NOT NULL) THEN 'submitted' ELSE 'dispatch_unknown' END,
              run_id=(SELECT run_id FROM ac_bindings b WHERE b.id=ac_auto_work.id),
              error_code='interrupted_dispatch',updated=? WHERE state='dispatching' ''', (time.time(),))
            db.execute('''UPDATE ac_auto_candidates SET
              state=(SELECT state FROM ac_auto_work w WHERE w.id=work_id),
              error_code=(SELECT error_code FROM ac_auto_work w WHERE w.id=work_id),updated=?
              WHERE work_id IN (SELECT id FROM ac_auto_work)''', (time.time(),))

    def _observed(self, cid, revision, state, error=None, work_id=None):
        with self.bridge.lock, self.bridge.connect() as db:
            db.execute('''INSERT INTO ac_auto_candidates VALUES (?,?,?,?,?,?)
              ON CONFLICT(candidate_id) DO UPDATE SET source_revision=excluded.source_revision,
              work_id=excluded.work_id,state=excluded.state,error_code=excluded.error_code,updated=excluded.updated''',
                       (cid, revision, work_id, state, error, time.time()))

    def scan(self):
        """Bounded keyset scan; cursor survives restart and rotates at end.

        No historical candidate outside the explicit allowlist/creation cutoff
        is enrolled. A creation cutoff is stable across restarts, unlike now().
        """
        with self.bridge.lock, self.bridge.connect() as db:
            cursor = db.execute('SELECT candidate_id FROM ac_auto_cursor WHERE scope_hash=?',
                                (self.scope_hash,)).fetchone()[0]
            clauses, parameters = [], [cursor]
            if self.scope.candidate_ids:
                clauses.append('c.id IN (' + ','.join('?' for _ in self.scope.candidate_ids) + ')')
                parameters.extend(self.scope.candidate_ids)
            if self.scope.created_since:
                clauses.append('julianday(c.created_at)>=julianday(?)')
                parameters.append(self.scope.created_since)
            parameters.append(self.scope.batch_size)
            ids = [row[0] for row in db.execute('''SELECT c.id FROM cp_candidates c WHERE c.id>?
              AND EXISTS(SELECT 1 FROM cp_interviews i WHERE i.candidate_id=c.id AND i.status='completed')
              AND (''' + ' OR '.join(clauses) + ') ORDER BY c.id LIMIT ?', parameters)]
        # Advance only after processing a candidate, so a crash before processing
        # never skips it. Already enqueued revisions remain deduplicated.
        for cid in ids:
            try:
                self.bridge.authorize(cid)
                snapshot = self.bridge.collector.collect(cid)
                self.bridge.save_source_evidence(cid, snapshot)
                revision = snapshot['revision']
                if snapshot['status'] != 'ready' or not ready_hr_interview(snapshot):
                    self._observed(cid, revision, 'blocked', 'complete_hr_materials_required')
                else:
                    identity = self.bridge.identity(cid, revision)
                    with self.bridge.lock, self.bridge.connect() as db:
                        db.execute('''INSERT OR IGNORE INTO ac_auto_work
                          (id,candidate_id,source_revision,rubric_hash,model,request_format,state,created,updated)
                          VALUES (?,?,?,?,?,?,'queued',?,?)''',
                          (identity, cid, revision, self.bridge.rubric_hash, self.bridge.model,
                           bridge_module.REQUEST_FORMAT, time.time(), time.time()))
                        state, error = db.execute('SELECT state,error_code FROM ac_auto_work WHERE id=?',
                                                 (identity,)).fetchone()
                    self._observed(cid, revision, state, error, identity)
            except Exception:
                # Source read errors can be revisited next sweep. They never
                # submit a model request or expose provider/source messages.
                self._observed(cid, None, 'source_error', 'source_collection_failed')
            with self.bridge.lock, self.bridge.connect() as db:
                db.execute('UPDATE ac_auto_cursor SET candidate_id=?,updated=? WHERE scope_hash=?',
                           (cid, time.time(), self.scope_hash))
        if len(ids) < self.scope.batch_size:
            with self.bridge.lock, self.bridge.connect() as db:
                db.execute('UPDATE ac_auto_cursor SET candidate_id=?,updated=? WHERE scope_hash=?',
                           ('', time.time(), self.scope_hash))
        return len(ids)

    def _state(self, work, state, error=None, run_id=None):
        with self.bridge.lock, self.bridge.connect() as db:
            db.execute('UPDATE ac_auto_work SET state=?,error_code=?,run_id=COALESCE(?,run_id),updated=? WHERE id=?',
                       (state, error, run_id, time.time(), work['id']))
            db.execute('''UPDATE ac_auto_candidates SET state=?,error_code=?,updated=?
              WHERE candidate_id=? AND work_id=?''',
                       (state, error, time.time(), work['candidate_id'], work['id']))

    def process(self):
        with self.bridge.connect() as db:
            db.row_factory = sqlite3.Row
            work = [dict(row) for row in db.execute('''SELECT * FROM ac_auto_work
              WHERE state IN ('queued','submitted') ORDER BY updated,id LIMIT ?''', (self.scope.batch_size,))]
        for item in work:
            cid = item['candidate_id']
            if (item['rubric_hash'] != self.bridge.rubric_hash or item['model'] != self.bridge.model
                    or item['request_format'] != bridge_module.REQUEST_FORMAT):
                self._state(item, 'superseded', 'configuration_changed')
                continue
            if item['state'] == 'queued':
                with self.bridge.lock, self.bridge.connect() as db:
                    changed = db.execute("UPDATE ac_auto_work SET state='dispatching',updated=? WHERE id=? AND state='queued'",
                                         (time.time(), item['id'])).rowcount
                if not changed:
                    continue
                try:
                    self.bridge.start(cid, expected_revision=item['source_revision'])
                except Exception as error:
                    # start may have accepted work before a subsequent read
                    # failed. An existing receipt permits safe GET recovery.
                    with self.bridge.connect() as db:
                        saved = db.execute('SELECT run_id FROM ac_bindings WHERE id=?', (item['id'],)).fetchone()
                    if saved and saved[0]:
                        self._state(item, 'submitted', 'dispatch_read_failed', saved[0])
                    elif isinstance(error, Problem) and error.status in (403, 404, 409):
                        self._state(item, 'superseded', 'sources_changed_or_access_denied')
                    else:
                        self._state(item, 'dispatch_unknown', 'dispatch_not_acknowledged')
                    continue
                with self.bridge.connect() as db:
                    saved = db.execute('SELECT run_id,status FROM ac_bindings WHERE id=?', (item['id'],)).fetchone()
                if saved and saved[0]:
                    self._state(item, saved[1] if saved[1] in TERMINAL else 'submitted', run_id=saved[0])
                else:
                    self._state(item, 'dispatch_unknown', 'dispatch_not_acknowledged')
            else:
                if time.time() - item['created'] > 600:
                    self._state(item, 'poll_failed', 'assessment_deadline_exceeded')
                    continue
                try:
                    self.bridge.authorize(cid)
                    self.bridge.collector.revalidate({'candidate_id': cid, 'revision': item['source_revision']})
                    receipt = self.bridge.validate_receipt(self.bridge.service.call(
                        'GET', '/v1/assessments/' + item['run_id']), cid, item['run_id'])
                    self.bridge._save(item['id'], cid, receipt)
                    code = (receipt.get('error') or {}).get('code')
                    code = code if isinstance(code, str) and re.fullmatch('[a-z0-9_]{1,80}', code) else None
                    self._state(item, receipt['status'] if receipt['status'] in TERMINAL else 'submitted', code)
                except CollectionError:
                    self._state(item, 'superseded', 'sources_changed')
                except Exception:
                    with self.bridge.lock, self.bridge.connect() as db:
                        db.execute('UPDATE ac_auto_work SET poll_errors=poll_errors+1,updated=? WHERE id=?',
                                   (time.time(), item['id']))
                    self._state(item, 'poll_failed' if item['poll_errors'] >= 2 else 'submitted', 'assessment_read_failed')
        return len(work)

    def tick(self):
        scanned = self.scan()
        return {'scanned': scanned, 'processed': self.process()}

    def status(self):
        """Diagnostics only: never collect, dispatch, or expose source text."""
        with self.bridge.connect() as db:
            return {'enabled': True, 'scope': {'candidate_count': len(self.scope.candidate_ids),
                    'created_since': self.scope.created_since, 'batch_size': self.scope.batch_size},
                    'work': dict(db.execute('SELECT state,count(*) FROM ac_auto_work GROUP BY state')),
                    'candidates': dict(db.execute('SELECT state,count(*) FROM ac_auto_candidates GROUP BY state')),
                    'cursor': db.execute('SELECT candidate_id FROM ac_auto_cursor WHERE scope_hash=?',
                                         (self.scope_hash,)).fetchone()[0]}

    def candidate_status(self, cid):
        self.bridge.authorize(cid)
        with self.bridge.connect() as db:
            db.row_factory = sqlite3.Row
            observed = db.execute('SELECT * FROM ac_auto_candidates WHERE candidate_id=?', (cid,)).fetchone()
            if observed is None:
                return {'state': 'not_enrolled'}
            result = dict(observed)
            work = db.execute('SELECT state,run_id,error_code,poll_errors,created,updated FROM ac_auto_work WHERE id=?',
                              (result['work_id'],)).fetchone()
            return {**result, 'work': dict(work) if work else None}


class Runtime:
    def __init__(self, lifecycle, owner_fd):
        self.lifecycle, self.owner_fd = lifecycle, owner_fd
        self.stop_event = threading.Event()
        self.last_error = None
        self.thread = threading.Thread(target=self._run, name='assessment-source-lifecycle', daemon=True)
        self.thread.start()

    def _run(self):
        try:
            try:
                self.lifecycle.recover()
            except Exception:
                self.last_error = 'automatic_assessment_recovery_failed'
                return
            while not self.stop_event.is_set():
                try:
                    self.lifecycle.tick()
                    self.last_error = None
                except Exception:
                    self.last_error = 'automatic_assessment_reconcile_failed'
                self.stop_event.wait(self.lifecycle.scope.interval)
        finally:
            os.close(self.owner_fd)

    def status(self):
        return {**self.lifecycle.status(), 'running': self.thread.is_alive(), 'error': self.last_error}

    def stop(self, timeout=60):
        self.stop_event.set()
        self.thread.join(timeout)
        return not self.thread.is_alive()


def start_background(connect, lock, authorize):
    """Call once after portal schema init; call runtime.stop() on shutdown.

    authorize MUST be a trusted system-scope candidate check, not a captured
    browser session. Startup failure remains visible to the owning application.
    """
    scope = configured_scope()
    if scope is None:
        return None
    bridge = bridge_module.configured(connect, lock, authorize)
    with connect() as db:
        path = db.execute('PRAGMA database_list').fetchone()[2]
    fd = os.open(path + '.assessment-lifecycle.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return Runtime(Lifecycle(bridge, scope), fd)
    except Exception:
        os.close(fd)
        raise Problem('automatic_assessment_runtime_unavailable', 503) from None
