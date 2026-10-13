"""Private local effect journal; durable barriers prevent blind browser retries."""
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager

from .adapter import BrowserError


class BrowserStore:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS browser_control (
              id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0,
              reason TEXT NOT NULL DEFAULT '', manual INTEGER NOT NULL DEFAULT 0);
            INSERT OR IGNORE INTO browser_control(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS browser_attempts (
              id TEXT PRIMARY KEY, prepared_id TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS browser_operations (
              id TEXT PRIMARY KEY, state TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL);
            ''')
            db.execute("UPDATE browser_operations SET state='interrupted',updated=? WHERE state IN ('queued','running')", (clock(),))
            changed = db.execute("UPDATE browser_attempts SET state='unknown',updated=? WHERE state='send_started'", (clock(),)).rowcount
            if changed:
                db.execute("UPDATE browser_control SET paused=1,reason='recovered_uncertain_send' WHERE id=1")
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def control(self):
        with self.db() as db:
            return dict(db.execute('SELECT paused,reason,manual FROM browser_control WHERE id=1').fetchone())

    def pause(self, reason):
        with self.db() as db:
            db.execute('UPDATE browser_control SET paused=1,reason=? WHERE id=1', (reason[:100],))

    def resume(self):
        with self.db() as db:
            if db.execute("SELECT 1 FROM browser_attempts WHERE state IN ('unknown','send_started') LIMIT 1").fetchone():
                raise BrowserError('uncertain_attempt_pending')
            db.execute("UPDATE browser_control SET paused=0,reason='' WHERE id=1")

    def manual(self, enabled):
        with self.db() as db:
            db.execute('UPDATE browser_control SET manual=? WHERE id=1', (int(enabled),))

    def get(self, attempt_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM browser_attempts WHERE id=?', (attempt_id,)).fetchone()
            if not row:
                return None
            return dict(row) | {'payload': json.loads(row['payload']), 'result': json.loads(row['result']) if row['result'] else None}

    def start(self, attempt_id, prepared_id, payload):
        with self.db() as db:
            if db.execute('SELECT 1 FROM browser_attempts WHERE id=?', (attempt_id,)).fetchone():
                raise BrowserError('attempt_already_started')
            db.execute('INSERT INTO browser_attempts VALUES (?,?,?,?,?,?,?)',
                       (attempt_id, prepared_id, json.dumps(payload, ensure_ascii=False), 'send_started', None, self.clock(), self.clock()))

    def finish(self, attempt_id, result):
        state = 'sent_observed' if result.get('outcome') == 'sent_observed' else 'unknown'
        with self.db() as db:
            previous = db.execute('SELECT state FROM browser_attempts WHERE id=?', (attempt_id,)).fetchone()
            if state == 'unknown' and previous and previous['state'] == 'abandoned':
                state = 'abandoned'
            db.execute('UPDATE browser_attempts SET state=?,result=?,updated=? WHERE id=?',
                       (state, json.dumps(result), self.clock(), attempt_id))
            if state == 'unknown':
                db.execute("UPDATE browser_control SET paused=1,reason='uncertain_send' WHERE id=1")

    def operation_create(self, operation_id):
        with self.db() as db:
            db.execute('INSERT INTO browser_operations VALUES (?,?,?,?,?)',
                       (operation_id, 'queued', None, self.clock(), self.clock()))

    def acknowledge(self, attempt_id):
        """Operator shelves an uncertain attempt, never authorizes resend."""
        with self.db() as db:
            row = db.execute('SELECT state FROM browser_attempts WHERE id=?', (attempt_id,)).fetchone()
            if not row or row['state'] not in ('unknown', 'abandoned'):
                raise BrowserError('attempt_not_uncertain')
            db.execute("UPDATE browser_attempts SET state='abandoned',updated=? WHERE id=?", (self.clock(), attempt_id))

    def operation_update(self, operation_id, state, result=None):
        with self.db() as db:
            db.execute('UPDATE browser_operations SET state=?,result=?,updated=? WHERE id=?',
                       (state, json.dumps(result) if result is not None else None, self.clock(), operation_id))

    def operation(self, operation_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM browser_operations WHERE id=?', (operation_id,)).fetchone()
            if not row:
                raise BrowserError('operation_not_found')
            result = {'operationId': row['id'], 'status': row['state']}
            if row['result']:
                result['result'] = json.loads(row['result'])
            return result
