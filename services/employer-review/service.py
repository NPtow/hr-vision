"""Private preparation process. One worker; failures require a human retry."""
from contextlib import contextmanager
import fcntl
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from quotes import Invalid, Model, VERSION, digest, dump, identifier, presentation, quote_key, require, validate

SERVICE_VERSION = 'employer-review-2026-10-27.1'

class Store:
    def __init__(self, path, model_name):
        self.path, self.model_name = str(path), model_name
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS preparations (
                id TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL,
                evidence TEXT, metadata TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                created REAL NOT NULL, updated REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS retries (
                request_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES preparations(id))''')
            if 'quote_key' not in {r[1] for r in db.execute('PRAGMA table_info(preparations)')}:
                db.execute('ALTER TABLE preparations ADD COLUMN quote_key TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS preparation_quotes ON preparations(quote_key,status)')
            for row in db.execute('SELECT id,body FROM preparations WHERE quote_key IS NULL').fetchall():
                body = json.loads(row['body'])
                if row['id'] == digest([VERSION, model_name, body]):
                    db.execute('UPDATE preparations SET quote_key=? WHERE id=?',
                               (digest([VERSION, model_name, quote_key(body)]), row['id']))
        os.chmod(path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, body):
        validate(body)
        identity = digest([VERSION, self.model_name, body])
        reusable = digest([VERSION, self.model_name, quote_key(body)])
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM preparations WHERE id=?', (identity,)).fetchone():
                previous = db.execute("SELECT id,status,evidence,error FROM preparations WHERE quote_key=? AND status IN ('ready','failed') ORDER BY CASE status WHEN 'ready' THEN 0 ELSE 1 END,created DESC LIMIT 1",
                                      (reusable,)).fetchone()
                require(previous or db.execute("SELECT count(*) FROM preparations WHERE status IN ('queued','processing')").fetchone()[0] < 100,
                        'queue_full')
                db.execute('''INSERT INTO preparations(id,body,status,created,updated,quote_key,evidence,metadata,error)
                    VALUES (?,?,?,?,?,?,?,?,?)''', (identity, dump(body), previous['status'] if previous else 'queued',
                    time.time(), time.time(), reusable, previous['evidence'] if previous else None,
                    dump({'reusedFrom': previous['id']}) if previous else None, previous['error'] if previous else None))
        return self.read(identity)

    def row(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT * FROM preparations WHERE id=?', (identity,)).fetchone()
        require(row is not None, 'preparation_not_found')
        return dict(row)

    def read(self, identity):
        row = self.row(identity)
        body = json.loads(row['body'])
        return {'preparationId': identity, 'candidateId': body['candidateId'],
                'coreRunId': body['assessment']['run_id'], 'sourceRevision': body['sourceRevision'],
                'status': row['status'], 'error': row['error'], 'attempts': row['attempts']}

    def view(self, identity, preferred, vacancy=None):
        row = self.row(identity)
        require(row['status'] == 'ready', 'preparation_not_ready')
        return {'preparationId': identity, 'assessment': presentation(json.loads(row['body']), json.loads(row['evidence']), preferred, vacancy)}

    def tasks(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,error,updated FROM preparations WHERE status='failed' ORDER BY updated DESC LIMIT 100").fetchall()
        return {'tasks': [{'id': row['id'], 'title': 'Проверить подготовку цитат кандидата',
                          'error': row['error'], 'updated': row['updated'], 'action': 'manual_retry'} for row in rows]}

    def retry(self, identity, request_id):
        identifier(request_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT job_id FROM retries WHERE request_id=?', (request_id,)).fetchone()
            if old:
                require(old[0] == identity, 'retry_identity_conflict')
            else:
                row = db.execute('SELECT status FROM preparations WHERE id=?', (identity,)).fetchone()
                require(row and row[0] == 'failed', 'retry_not_allowed')
                db.execute('INSERT INTO retries VALUES (?,?)', (request_id, identity))
                db.execute("UPDATE preparations SET status='queued',error=NULL,metadata=?,updated=? WHERE id=?",
                           (dump({'manualRetry': request_id}), time.time(), identity))
        return self.read(identity)

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE preparations SET status='failed',error='interrupted_provider_call',updated=? WHERE status='processing'", (time.time(),))

    def tick(self, model):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM preparations WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row is None:
                return False
            previous = db.execute("SELECT id,evidence FROM preparations WHERE quote_key=? AND status='ready' ORDER BY created LIMIT 1",
                                  (row['quote_key'],)).fetchone()
            if previous:
                db.execute("UPDATE preparations SET status='ready',evidence=?,metadata=?,updated=? WHERE id=?",
                           (previous['evidence'], dump({'reusedFrom': previous['id']}), time.time(), row['id']))
                return True
            failed = db.execute("SELECT id,error FROM preparations WHERE quote_key=? AND status='failed' AND id!=? ORDER BY updated DESC LIMIT 1",
                                (row['quote_key'], row['id'])).fetchone()
            if failed and not json.loads(row['metadata'] or '{}').get('manualRetry'):
                db.execute("UPDATE preparations SET status='failed',error=?,metadata=?,updated=? WHERE id=?",
                           (failed['error'], dump({'reusedFrom': failed['id']}), time.time(), row['id']))
                return True
            db.execute("UPDATE preparations SET status='processing',attempts=attempts+1,updated=? WHERE id=?", (time.time(), row['id']))
        try:
            evidence, metadata = model(json.loads(row['body']), row['id'] + ':' + str(row['attempts'] + 1))
            with self.connect() as db:
                db.execute("UPDATE preparations SET status='ready',evidence=?,metadata=?,error=NULL,updated=? WHERE id=?",
                           (dump(evidence), dump(metadata), time.time(), row['id']))
        except Exception as error:
            code = str(error) if isinstance(error, Invalid) and re.fullmatch('[a-z0-9_]{1,80}', str(error)) else 'quote_preparation_failed'
            with self.connect() as db:
                db.execute("UPDATE preparations SET status='failed',error=?,metadata=?,updated=? WHERE id=?",
                           (code, dump({'rejectedResponse': getattr(error, 'response', None)}), time.time(), row['id']))
        return True


def handler(store, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No candidate IDs, request bodies or credentials in HTTP logs.

        def answer(self, status, value):
            raw = dump(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def dispatch(self):
            self.connection.settimeout(10)
            try:
                if self.command == 'GET' and self.path == '/healthz':
                    return self.answer(200, {'status': 'ok', 'version': SERVICE_VERSION, 'quotesVersion': VERSION})
                if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                    return self.answer(401, {'error': 'unauthorized'})
                body = None
                if self.command == 'POST':
                    require(self.headers.get_content_type() == 'application/json', 'json_required')
                    length = int(self.headers.get('Content-Length', '0'))
                    require(0 < length <= 2 * 1024 * 1024, 'input_too_large')
                    body = json.loads(self.rfile.read(length))
                    require(isinstance(body, dict), 'invalid_input')
                match = re.fullmatch('/v1/preparations/([a-f0-9]{64})(/retry)?', self.path)
                if self.path == '/v1/preparations' and self.command == 'POST':
                    return self.answer(202, store.submit(body))
                if match and self.command == 'GET' and not match[2]:
                    return self.answer(200, store.read(match[1]))
                if match and self.command == 'POST' and match[2]:
                    require(set(body) == {'requestId'}, 'invalid_retry')
                    return self.answer(202, store.retry(match[1], body['requestId']))
                if self.path == '/v1/presentations' and self.command == 'POST':
                    require({'preparationId', 'preferredParameterIds'} <= set(body) <=
                            {'preparationId', 'preferredParameterIds', 'vacancyProfile'}, 'invalid_presentation')
                    return self.answer(200, store.view(body['preparationId'], body['preferredParameterIds'], body.get('vacancyProfile')))
                if self.path == '/v1/levels' and self.command == 'POST':
                    require(set(body) == {'candidateId', 'assessment', 'sourceRevision', 'vacancyProfile'}, 'invalid_presentation')
                    source = validate({k: v for k, v in {**body, 'chunks': []}.items() if k != 'vacancyProfile'})
                    # Existing published cards can show unchanged core levels
                    # while quotes are pending. This endpoint performs no model call.
                    result = presentation(source, {}, [], body['vacancyProfile'])
                    result['provenance']['quotesState'] = 'pending'
                    return self.answer(200, {'assessment': result})
                if self.path == '/v1/tasks' and self.command == 'GET':
                    return self.answer(200, store.tasks())
                self.answer(404, {'error': 'not_found'})
            except Invalid as error:
                code = str(error)
                status = 404 if code == 'preparation_not_found' else 409 if code in (
                    'preparation_not_ready', 'retry_not_allowed', 'retry_identity_conflict') else 503 if code == 'queue_full' else 422
                self.answer(status, {'error': code})
            except (ValueError, TypeError, KeyError):
                self.answer(400, {'error': 'invalid_input'})
            except Exception:
                self.answer(503, {'error': 'preparation_service_unavailable'})
    return Handler


def main():
    os.umask(0o077)
    token = os.environ.get('REVIEW_TOKEN', '')
    require(re.fullmatch('[a-f0-9]{64}', token), 'invalid_service_token')
    model = Model()
    path = Path(os.environ.get('REVIEW_DB', '~/.local/share/hr-vision-employer-review/jobs.sqlite3')).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(str(path) + '.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Store(path, model.model); store.recover()
        stop = threading.Event()
        def work():
            while not stop.is_set():
                if not store.tick(model):
                    stop.wait(.5)
        worker = threading.Thread(target=work, daemon=True); worker.start()
        server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('REVIEW_PORT', '8098'))), handler(store, token))
        try:
            server.serve_forever()
        finally:
            stop.set(); server.server_close(); worker.join(timeout=185)


if __name__ == '__main__':
    main()
