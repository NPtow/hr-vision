"""HR-owned autonomous comparisons; model work stays in its owning services.

Only active vacancies and the complete canonical ready-HR pool participate.
No invitation, application, nomination, outbox or ASR commands are available.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import threading
import time

from assessment_core_bridge import digest, dump
from domain import Problem, require
import recommendations_bridge as matching


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rc_auto_cursor (
        id INTEGER PRIMARY KEY CHECK(id=1),vacancy_id TEXT NOT NULL,updated REAL NOT NULL)''')
    db.execute('INSERT OR IGNORE INTO rc_auto_cursor VALUES (1,?,?)', ('', time.time()))
    db.execute('''CREATE TABLE IF NOT EXISTS rc_auto_vacancies (
        vacancy_id TEXT PRIMARY KEY,input_hash TEXT,versions TEXT,state TEXT NOT NULL,
        error_code TEXT,error_count INTEGER NOT NULL DEFAULT 0,next_check REAL NOT NULL DEFAULT 0,
        checked REAL NOT NULL,updated REAL NOT NULL)''')


def projection(db, vid):
    """Local SELECT-only status; caller owns vacancy access checks."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rc_auto_vacancies'").fetchone():
        return None
    row = db.execute('SELECT state,error_code,next_check,updated FROM rc_auto_vacancies WHERE vacancy_id=?', (vid,)).fetchone()
    return dict(zip(('state', 'errorCode', 'retryAt', 'updatedAt'), row)) if row else None


class Lifecycle:
    def __init__(self, connect, lock, portal, *, bridge_factory=None, candidate_ids=None,
                 versions=None, batch_size=5, interval=30, clock=time.time):
        if (type(batch_size) is not int or not 1 <= batch_size <= 100
                or type(interval) not in (int, float) or not 1 <= interval <= 60):
            raise ValueError('invalid_recommendation_automation_configuration')
        self.connect, self.lock, self.portal = connect, lock, portal
        self.bridge_factory = bridge_factory or matching.configured
        self.candidate_ids = candidate_ids or matching.automatic_candidate_ids
        self.versions = versions or matching.local_versions
        self.batch_size, self.interval, self.clock = batch_size, interval, clock
        with lock, connect() as db:
            matching.init(db)
            init(db)

    def _inputs(self, db, vid):
        # Helper authorizes an open vacancy and exhausts the ready directory.
        # The service limit is an explicit refusal, never a truncated pool.
        ids = self.candidate_ids(self.portal, db, vid)
        require(type(ids) is list and all(type(cid) is str for cid in ids)
                and ids == sorted(set(ids)), 'invalid_automatic_candidate_pool', 503)
        if len(ids) > 1000:
            return ids, {'pool': ids, 'over_limit': True}
        return ids, self.versions(db, vid, ids)

    def _save(self, vid, input_hash, versions, state, error=None, errors=0, delay=None):
        checked = self.clock()
        with self.lock, self.connect() as db:
            db.execute('''INSERT INTO rc_auto_vacancies VALUES (?,?,?,?,?,?,?,?,?)
              ON CONFLICT(vacancy_id) DO UPDATE SET input_hash=excluded.input_hash,versions=excluded.versions,
              state=excluded.state,error_code=excluded.error_code,error_count=excluded.error_count,
              next_check=excluded.next_check,checked=excluded.checked,updated=excluded.updated''',
              (vid, input_hash, dump(versions) if versions is not None else None, state, error,
               errors, checked + (self.interval if delay is None else delay), checked, checked))

    def _bridge(self, vid, pool):
        import selection
        import selection_status
        def authorize_vacancy(resource_id):
            require(resource_id == vid, 'automatic_vacancy_access_denied', 403)
            with self.lock, self.connect() as db:
                vacancy = selection.vacancy(db, vid)
                selection.employers.ensure_open(db, vacancy['workspaceId'], vid)
        def authorize_candidate(resource_id):
            require(resource_id in pool, 'automatic_candidate_access_denied', 403)
            authorize_vacancy(vid)
            with self.lock, self.connect() as db:
                person = selection.person(db, resource_id)
                require(selection_status.evidence(db, person)['state'] == 'ready',
                        'automatic_candidate_materials_changed', 409)
        return self.bridge_factory(self.connect, self.lock, authorize_vacancy, authorize_candidate)

    def _process(self, vid, pool, versions, previous, stop_event):
        identity = digest(versions)
        self._save(vid, identity, versions, 'processing')
        try:
            bridge = self._bridge(vid, pool)
            profile = bridge.vacancy.read(vid)
            if stop_event is not None and stop_event.is_set():
                return
            if profile['state'] in ('not_started', 'stale', 'dispatch_unknown'):
                # Frozen-input submission is idempotent in the vacancy service:
                # an ACK retry retrieves its existing run, never requeues JEV.
                # An acknowledged outcome_unknown is observed by GET only.
                profile = bridge.vacancy.start(vid, profile['revision'])
            if not matching.profile_comparable(profile):
                state = {'dispatch_unknown': 'dispatch_unknown', 'outcome_unknown': 'outcome_unknown',
                         'error': 'failed', 'needs_review': 'needs_review'}.get(profile['state'], 'waiting_profile')
                self._save_current(vid, pool, state, 'vacancy_profile_requires_review' if state == 'needs_review' else None,
                                   expected_versions=versions)
                return
            if not pool:
                self._save_current(vid, pool, 'waiting_candidates', expected_versions=versions)
                return
            if stop_event is not None and stop_event.is_set():
                return
            with self.lock, self.connect() as db:
                current_pool, compared_versions = self._inputs(db, vid)
                binding = matching.latest(db, vid)
                previous_pool = json.loads(binding['candidate_ids']) if binding else None
            require(current_pool == pool, 'automatic_candidate_pool_changed', 409)
            require(compared_versions.get('profile') == (profile.get('profile') or {}).get('profile_id'),
                    'automatic_vacancy_profile_changed', 409)
            # An old binding may contain a now-ineligible candidate. Do not run
            # its old-pool access checks through this current-pool authorizer.
            current = bridge.read(vid) if previous_pool == pool else None
            if current is None or current.get('state') != 'ready' or current.get('stale') or current.get('candidateIds') != pool:
                # Model-free recommendations are content-idempotent. This also
                # recovers a POST whose ACK was lost, without candidate scoring.
                current = bridge.start(vid, profile['revision'], pool)
            state = 'waiting_assessment' if current.get('pendingCandidates') else current.get('state', 'waiting_assessment')
            if state not in ('ready', 'waiting_assessment'):
                state = 'input_changed'
            self._save_current(vid, pool, state, expected_versions=compared_versions)
        except Problem as error:
            if error.status in (403, 404):
                self._save(vid, identity, versions, 'inactive', 'vacancy_or_candidate_access_denied')
            elif error.status == 409:
                self._save(vid, identity, versions, 'input_changed', 'inputs_changed')
            else:
                self._failed(vid, identity, versions, previous)
        except Exception:
            self._failed(vid, identity, versions, previous)

    def _save_current(self, vid, pool, state, error=None, *, expected_versions):
        with self.lock, self.connect() as db:
            fresh_pool, fresh = self._inputs(db, vid)
        if fresh_pool != pool or fresh != expected_versions:
            self._save(vid, digest(fresh), fresh, 'input_changed', 'inputs_changed_during_comparison', delay=0)
        else:
            self._save(vid, digest(fresh), fresh, state, error)

    def _failed(self, vid, identity, versions, previous):
        # Bridge may have durably progressed a binding before losing its HTTP
        # ACK. Bind backoff to that current local snapshot so our own progress
        # cannot immediately bypass the persisted retry deadline.
        with self.lock, self.connect() as db:
            _, versions = self._inputs(db, vid)
        identity = digest(versions)
        errors = (previous['error_count'] + 1 if previous and previous['input_hash'] == identity else 1)
        self._save(vid, identity, versions, 'backoff', 'comparison_dependency_unavailable', errors,
                   min(300, 5 * 2 ** min(errors - 1, 6)))

    def scan(self, stop_event=None):
        with self.lock, self.connect() as db:
            cursor = db.execute('SELECT vacancy_id FROM rc_auto_cursor WHERE id=1').fetchone()[0]
            ids = [row[0] for row in db.execute('''SELECT e.vacancy_id FROM em_vacancies e
                JOIN cp_vacancies c ON c.id=e.vacancy_id JOIN em_workspaces w ON w.id=e.workspace_id
                WHERE e.vacancy_id>? ORDER BY e.vacancy_id LIMIT ?''', (cursor, self.batch_size))]
            if not ids:
                db.execute('UPDATE rc_auto_cursor SET vacancy_id=?,updated=? WHERE id=1', ('', self.clock()))
        for vid in ids:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                with self.lock, self.connect() as db:
                    db.row_factory = sqlite3.Row
                    previous = db.execute('SELECT * FROM rc_auto_vacancies WHERE vacancy_id=?', (vid,)).fetchone()
                    previous = dict(previous) if previous else None
                    pool, versions = self._inputs(db, vid)
                identity = digest(versions)
                changed = previous is None or previous['input_hash'] != identity
                if len(pool) > 1000:
                    self._save(vid, identity, versions, 'pool_too_large', 'candidate_pool_limit_exceeded')
                elif changed or previous['next_check'] <= self.clock() and previous['state'] not in ('ready', 'needs_review'):
                    self._process(vid, pool, versions, previous, stop_event)
            except Problem as error:
                self._save(vid, None, None, 'inactive' if error.status in (403, 404, 409) else 'backoff',
                           'vacancy_unavailable' if error.status in (403, 404, 409) else 'comparison_dependency_unavailable')
            except Exception:
                self._save(vid, None, None, 'backoff', 'comparison_dependency_unavailable')
            with self.lock, self.connect() as db:
                db.execute('UPDATE rc_auto_cursor SET vacancy_id=?,updated=? WHERE id=1', (vid, self.clock()))
        return len(ids)

    def tick(self, stop_event=None):
        return {'scanned': self.scan(stop_event)}

    def status(self):
        with self.connect() as db:
            return {'enabled': True, 'batchSize': self.batch_size, 'intervalSeconds': self.interval,
                    'cursor': db.execute('SELECT vacancy_id FROM rc_auto_cursor WHERE id=1').fetchone()[0],
                    'vacancies': dict(db.execute('SELECT state,count(*) FROM rc_auto_vacancies GROUP BY state')),
                    'lastCheckedAt': db.execute('SELECT max(checked) FROM rc_auto_vacancies').fetchone()[0]}


class Runtime:
    def __init__(self, lifecycle, owner_fd):
        self.lifecycle, self.owner_fd = lifecycle, owner_fd
        self.stop_event = threading.Event()
        self.last_error = None
        self.thread = threading.Thread(target=self._run, name='recommendation-automation', daemon=True)
        self.thread.start()

    def _run(self):
        failures = 0
        try:
            while not self.stop_event.is_set():
                try:
                    result = self.lifecycle.tick(self.stop_event)
                    failures, self.last_error = 0, None
                    delay = .1 if result['scanned'] else self.lifecycle.interval
                except Exception:
                    failures += 1
                    self.last_error = 'recommendation_automation_unavailable'
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


def start_background(connect, lock, portal):
    import assessment_core_bridge
    flag = os.environ.get('HR_RECOMMENDATIONS_AUTO_ENABLED')
    if flag == '0' or flag is None and not assessment_core_bridge.enabled():
        return None
    fd = None
    try:
        with connect() as db:
            path = db.execute('PRAGMA database_list').fetchone()[2]
        require(bool(path), 'recommendation_automation_requires_durable_database', 503)
        fd = os.open(path + '.recommendation-automation.lock', os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lifecycle = Lifecycle(connect, lock, portal,
            batch_size=int(os.environ.get('HR_RECOMMENDATIONS_AUTO_BATCH_SIZE', '5')),
            interval=float(os.environ.get('HR_RECOMMENDATIONS_AUTO_INTERVAL_SECONDS', '30')))
        return Runtime(lifecycle, fd)
    except (OSError, ValueError, Problem, sqlite3.Error):
        if fd is not None:
            os.close(fd)
        raise Problem('recommendation_automation_runtime_unavailable', 503) from None
