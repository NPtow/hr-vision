"""Private assessment API + durable single-consumer worker, independent of HR DB."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from engine import Failure, JevBoundary, PACK_HASH, VERSION, assess, check, digest, dump, intake, match_intake, match_vacancy, unique


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
              CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, subject TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS calls (
                key TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), stage TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, error TEXT, budget INTEGER NOT NULL,
                created REAL NOT NULL, seconds REAL);
            ''')
        os.chmod(path, 0o600)

    def db(self):
        from contextlib import contextmanager
        @contextmanager
        def connect():
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            try:
                with db:
                    yield db
            finally:
                db.close()
        return connect()

    def submit(self, value, model, *, match=False):
        body = match_intake(value) if match else intake(value)
        subject = ('match:' + body['baseJobId'] + ':' + body['vacancyId']) if match else body['subject']
        if match:
            base = self.get(body['baseJobId'])
            check(base['status'] == 'completed' and base['assessment'] and 'provenance' in base['assessment']
                  and base['assessment']['provenance'].get('method') == 'jev-evidence-rating', 'base_assessment_not_ready')
        identity = digest([body, VERSION, PACK_HASH, model])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM jobs WHERE id=?', (identity,)).fetchone():
                check(db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] < 100, 'queue_full')
                check(db.execute('SELECT count(*) FROM jobs WHERE subject=? AND created>?',
                      (subject, time.time() - 86400)).fetchone()[0] < 5, 'subject_daily_budget')
                db.execute('INSERT INTO jobs VALUES (?,?,?,\'queued\',NULL,NULL,?,?)',
                           (identity, subject, dump(body), time.time(), time.time()))
        return self.get(identity)

    def get(self, identity):
        with self.db() as db:
            row = db.execute('SELECT id,state,result,error FROM jobs WHERE id=?', (identity,)).fetchone()
        check(row is not None, 'not_found')
        return {'id': row['id'], 'status': row['state'], 'assessment': json.loads(row['result']) if row['result'] else None,
                'error': row['error']}

    def begin_call(self, job_id, key, stage, budget):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT state,created FROM jobs WHERE id=?', (job_id,)).fetchone()
            check(job and job['state'] == 'running', 'cancelled')
            check(time.time() - job['created'] < 86400, 'job_expired')
            first_call = db.execute('SELECT MIN(created) FROM calls WHERE job_id=?', (job_id,)).fetchone()[0]
            check(first_call is None or time.time() - first_call < 600, 'job_time_budget')
            row = db.execute('SELECT state,result,error FROM calls WHERE key=?', (key,)).fetchone()
            if row:
                if row['state'] == 'completed':
                    return json.loads(row['result'])
                raise Failure(row['error'] or 'provider_outcome_unknown')
            spent = db.execute('SELECT COALESCE(SUM(budget),0) FROM calls WHERE job_id=?', (job_id,)).fetchone()[0]
            daily = db.execute('SELECT COALESCE(SUM(budget),0) FROM calls WHERE created>?', (time.time() - 86400,)).fetchone()[0]
            check(spent + budget <= 900000, 'job_token_budget')
            check(daily + budget <= int(os.environ.get('ASSESSMENT_DAILY_BUDGET', '6000000')), 'daily_token_budget')
            # Bytes upper-bound tokens; no provider result can undo the reservation.
            db.execute('INSERT INTO calls VALUES (?,?,?,\'sent\',NULL,NULL,?,?,NULL)', (key, job_id, stage, budget, time.time()))
        return None

    def finish_call(self, job_id, key, value, seconds):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            check(db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()[0] == 'running', 'cancelled')
            db.execute("UPDATE calls SET state='completed',result=?,seconds=? WHERE key=?", (dump(value), seconds, key))

    def fail_call(self, key, error):
        with self.db() as db:
            db.execute("UPDATE calls SET state='failed',error=? WHERE key=?", (error, key))

    def cancel(self, identity):
        with self.db() as db:
            db.execute("UPDATE jobs SET state='cancelled',payload='{}',result=NULL,updated=? WHERE id=?", (time.time(), identity))

    def recover(self):
        with self.db() as db:
            # Completed stages replay from cache. A stage persisted as 'sent'
            # is never recharged after a crash: it fails outcome_unknown.
            db.execute("UPDATE jobs SET state='queued' WHERE state='running'")

    def work(self, boundary):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return False
            db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (time.time(), row['id']))
        try:
            body = json.loads(row['payload'])
            if body.get('kind') == 'match':
                base = self.get(body['baseJobId'])
                check(base['status'] == 'completed' and base['assessment'], 'base_assessment_not_ready')
                result = match_vacancy(body, row['id'], boundary, base['assessment'])
            else:
                result = assess(body, row['id'], boundary)
            with self.db() as db:
                db.execute("UPDATE jobs SET state='completed',result=?,updated=? WHERE id=? AND state='running'",
                           (dump(result), time.time(), row['id']))
            print(dump({'job': row['id'], 'status': 'completed', 'pack': VERSION, 'model': boundary.model}), flush=True)
        except Exception as error:
            code = error.code if isinstance(error, Failure) else 'internal_error'
            with self.db() as db:
                db.execute("UPDATE jobs SET state='failed',error=?,updated=? WHERE id=? AND state='running'", (code, time.time(), row['id']))
            print(dump({'job': row['id'], 'status': 'failed', 'error': code}), flush=True)
        return True

    def prune(self):
        with self.db() as db:
            cutoff = time.time() - 7 * 86400
            db.execute("UPDATE jobs SET state='failed',error='job_expired',payload='{}' WHERE state='queued' AND created<?", (cutoff,))
            db.execute("UPDATE jobs SET payload='{}' WHERE state IN ('completed','failed','cancelled') AND updated<?", (cutoff,))
            # Result/evidence retention: 90 days; same job cannot be silently re-billed.
            db.execute('DELETE FROM calls WHERE created<?', (time.time() - 90 * 86400,))
            db.execute("UPDATE jobs SET result=NULL,error='result_expired',state='expired' WHERE state='completed' AND updated<?", (time.time() - 90 * 86400,))


def handler(store, boundary, secret):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, value):
            raw = dump(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def handle_request(self):
            self.connection.settimeout(15)
            try:
                if self.command == 'GET' and self.path == '/healthz':
                    return self.send(200, {'status': 'ok', 'version': VERSION, 'model': boundary.model})
                check(hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + secret), 'unauthorized')
                if self.command == 'POST' and self.path in ('/v1/jobs', '/v1/matches'):
                    check(self.headers.get('Content-Type') == 'application/json', 'invalid_input')
                    size = int(self.headers.get('Content-Length', '0'))
                    check(0 < size <= 260000, 'input_too_large')
                    body = json.loads(self.rfile.read(size), object_pairs_hook=unique)
                    return self.send(202, store.submit(body, boundary.model, match=self.path == '/v1/matches'))
                match = re.fullmatch('/v1/jobs/([a-f0-9]{64})', self.path)
                check(match, 'not_found')
                if self.command == 'DELETE':
                    store.cancel(match[1])
                else:
                    check(self.command == 'GET', 'invalid_input')
                return self.send(200, store.get(match[1]))
            except Failure as error:
                return self.send(401 if error.code == 'unauthorized' else 404 if error.code == 'not_found' else 422, {'error': error.code})
            except (ValueError, TypeError, UnicodeError):
                return self.send(400, {'error': 'invalid_json'})
            except Exception:
                return self.send(503, {'error': 'service_unavailable'})

        do_GET = do_POST = do_DELETE = handle_request
    return Handler


def main():
    secret = os.environ.get('ASSESSMENT_SERVICE_TOKEN', '')
    check(re.fullmatch('[a-f0-9]{64}', secret), 'service_token_missing')
    store = Store(os.environ.get('ASSESSMENT_DB', '/var/lib/hr-vision-assessment/jobs.sqlite3'))
    boundary = JevBoundary(store)
    store.recover()
    def worker():
        while True:
            try:
                store.prune()
                if not store.work(boundary):
                    time.sleep(2)
            except Exception:
                print('{"error":"worker_iteration_failed"}', flush=True)
                time.sleep(5)
    threading.Thread(target=worker, name='assessment', daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('ASSESSMENT_PORT', '8096'))), handler(store, boundary, secret)).serve_forever()


if __name__ == '__main__':
    main()
