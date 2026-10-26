"""Durable, read-only HR materials observation for the authorized directory.

This HR-owned runtime stores readiness/cursors only. Core owns recordings and
primary transcript choices. No assessment, CV extraction, ASR or provider writes
are available through this module. Its configuration needs only Core reads.
"""
from __future__ import annotations

import fcntl
import os
import sqlite3
import threading
import time

from assessment_corpus import CandidateCorpusCollector, CollectionError
from core_source_client import CoreClient, ServiceHTTP
from domain import Problem, require
import selection_status


# Stripes bound process memory while serializing card/background reads of the
# same candidate. The runtime file lock separately owns the directory scan.
_REFRESH_LOCKS = [threading.RLock() for _ in range(64)]


def _authorize(authorize, cid):
    require(authorize(cid) is not False, 'candidate_access_denied', 403)


def configured_collector(authorize):
    try:
        http = ServiceHTTP(os.environ['HR_MATERIALS_CORE_URL'],
                           os.environ['HR_MATERIALS_CORE_KEY'], timeout=3)
    except (KeyError, ValueError):
        raise Problem('materials_configuration_unavailable', 503) from None
    # The HR-only collector path never calls the CV reader.
    return CandidateCorpusCollector(CoreClient(http), lambda cid: None, authorize=authorize)


def refresh_candidate(connect, lock, authorize, cid, *, collector=None):
    """Authorized GET-safe refresh; only canonical GETs and metadata writes.

    No shared SQLite lock/transaction spans a network read. A local source
    change during collection cannot be stamped with the newer portal revision.
    Failure replaces previous ready evidence immediately.
    """
    import selection
    _authorize(authorize, cid)
    with _REFRESH_LOCKS[hash(cid) % len(_REFRESH_LOCKS)]:
        with lock, connect() as db:
            before = selection.person(db, cid)['sourceRevision']
        try:
            reader = collector if collector is not None else configured_collector(authorize)
            snapshot = reader.collect_interviews(cid)
            require(snapshot.get('candidate_id') == cid, 'materials_identity_mismatch', 502)
        except (CollectionError, Problem, OSError, ValueError, KeyError, TypeError, AttributeError):
            # Reauthorization failures must not be swallowed as readable data.
            _authorize(authorize, cid)
            with lock, connect() as db:
                return selection_status.invalidate_evidence(db, cid)
        _authorize(authorize, cid)
        with lock, connect() as db:
            if selection.person(db, cid)['sourceRevision'] != before:
                return selection_status.invalidate_evidence(db, cid, error='materials_changed')
            return selection_status.save_evidence(db, cid, snapshot)


def init(db):
    selection_status.init_evidence(db)
    db.execute('''CREATE TABLE IF NOT EXISTS sl_materials_cursor (
        id INTEGER PRIMARY KEY CHECK(id=1),candidate_id TEXT NOT NULL,updated REAL NOT NULL)''')
    db.execute('INSERT OR IGNORE INTO sl_materials_cursor VALUES (1,?,?)', ('', time.time()))
    db.execute('''CREATE TABLE IF NOT EXISTS sl_materials_checks (
        candidate_id TEXT PRIMARY KEY,state TEXT NOT NULL,error_count INTEGER NOT NULL,
        next_check REAL NOT NULL,checked REAL NOT NULL)''')


class Lifecycle:
    def __init__(self, connect, lock, authorize, collector, *, batch_size=10, interval=30):
        if (type(batch_size) is not int or not 1 <= batch_size <= 100 or
                not isinstance(interval, (int, float)) or not 1 <= interval <= 120):
            raise ValueError('invalid_materials_scan_configuration')
        self.connect, self.lock, self.authorize, self.collector = connect, lock, authorize, collector
        self.batch_size, self.interval = batch_size, interval
        with lock, connect() as db:
            init(db)

    def scan(self, stop_event=None):
        """One bounded keyset batch; candidates require no model enrollment.

        Include every HR appointment status, and previously observed candidates
        whose last local interview was removed. Authorization precedes Core.
        """
        with self.lock, self.connect() as db:
            cursor = db.execute('SELECT candidate_id FROM sl_materials_cursor WHERE id=1').fetchone()[0]
            rows = list(db.execute('''SELECT c.id,COALESCE(s.next_check,0)
                FROM cp_candidates c LEFT JOIN sl_materials_checks s ON s.candidate_id=c.id
                WHERE c.id>? AND (EXISTS(SELECT 1 FROM cp_interviews i WHERE i.candidate_id=c.id)
                    OR EXISTS(SELECT 1 FROM sl_evidence e WHERE e.candidate_id=c.id))
                ORDER BY c.id LIMIT ?''', (cursor, self.batch_size)))
            if not rows:
                db.execute('UPDATE sl_materials_cursor SET candidate_id=?,updated=? WHERE id=1', ('', time.time()))
        for cid, next_check in rows:
            if stop_event is not None and stop_event.is_set():
                break
            if next_check <= time.time():
                try:
                    value = refresh_candidate(self.connect, self.lock, self.authorize, cid, collector=self.collector)
                    state = value['state']
                except Exception:
                    # No exception strings, source text or credentials in state.
                    state = 'unavailable'
                with self.lock, self.connect() as db:
                    old = db.execute('SELECT error_count FROM sl_materials_checks WHERE candidate_id=?', (cid,)).fetchone()
                    errors = (old[0] + 1 if old else 1) if state == 'unavailable' else 0
                    delay = min(120, 5 * 2 ** min(errors - 1, 5)) if errors else self.interval
                    checked = time.time()
                    db.execute('''INSERT INTO sl_materials_checks VALUES (?,?,?,?,?)
                        ON CONFLICT(candidate_id) DO UPDATE SET state=excluded.state,
                        error_count=excluded.error_count,next_check=excluded.next_check,checked=excluded.checked''',
                        (cid, state, errors, checked + delay, checked))
            # Persist after each read; an interrupted batch safely resumes.
            with self.lock, self.connect() as db:
                db.execute('UPDATE sl_materials_cursor SET candidate_id=?,updated=? WHERE id=1', (cid, time.time()))
        return len(rows)

    def tick(self, stop_event=None):
        return {'scanned': self.scan(stop_event)}

    def status(self):
        with self.connect() as db:
            return {'enabled': True, 'batchSize': self.batch_size, 'intervalSeconds': self.interval,
                    'cursor': db.execute('SELECT candidate_id FROM sl_materials_cursor WHERE id=1').fetchone()[0],
                    'candidates': dict(db.execute('SELECT state,count(*) FROM sl_materials_checks GROUP BY state')),
                    'lastCheckedAt': db.execute('SELECT max(checked) FROM sl_materials_checks').fetchone()[0]}


class Runtime:
    def __init__(self, lifecycle, owner_fd):
        self.lifecycle, self.owner_fd = lifecycle, owner_fd
        self.stop_event = threading.Event()
        self.last_error = None
        self.thread = threading.Thread(target=self._run, name='selection-materials-lifecycle', daemon=True)
        self.thread.start()

    def _run(self):
        failures = 0
        try:
            while not self.stop_event.is_set():
                try:
                    result = self.lifecycle.tick(self.stop_event)
                    failures, self.last_error = 0, None
                    # Drain more batches before sleeping for a whole interval.
                    # The small interruptible pause still bounds scan pressure.
                    delay = .1 if result['scanned'] else self.lifecycle.interval
                except Exception:
                    failures += 1
                    self.last_error = 'materials_reconcile_failed'
                    delay = min(60, 5 * 2 ** min(failures - 1, 4))
                self.stop_event.wait(delay)
        finally:
            os.close(self.owner_fd)

    def status(self):
        return {**self.lifecycle.status(), 'running': self.thread.is_alive(), 'error': self.last_error}

    def stop(self, timeout=60):
        self.stop_event.set()
        self.thread.join(timeout)
        return not self.thread.is_alive()


def configured(connect, lock, authorize):
    """Start after portal schema init; no assessment configuration is read."""
    flag = os.environ.get('HR_SELECTION_MATERIALS_ENABLED')
    if flag == '0' or flag is None and os.environ.get('HR_CORE_CATALOG_ENABLED') != '1':
        return None
    fd = None
    try:
        with connect() as db:
            path = db.execute('PRAGMA database_list').fetchone()[2]
        require(bool(path), 'materials_runtime_requires_durable_database', 503)
        fd = os.open(path + '.selection-materials.lock', os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lifecycle = Lifecycle(connect, lock, authorize, configured_collector(authorize),
                              batch_size=int(os.environ.get('HR_SELECTION_MATERIALS_BATCH_SIZE', '10')),
                              interval=float(os.environ.get('HR_SELECTION_MATERIALS_INTERVAL_SECONDS', '30')))
        return Runtime(lifecycle, fd)
    except (OSError, ValueError, Problem, sqlite3.Error):
        if fd is not None:
            os.close(fd)
        raise Problem('materials_runtime_unavailable', 503) from None
