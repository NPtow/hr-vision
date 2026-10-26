"""Employer-owned preparation gate; no evaluator, prompts or UI code here."""
import json
import os
import threading
import time

import assessment_core_bridge
from core_source_client import ServiceHTTP
from domain import Problem, require
import transcript_bundle


def enabled():
    return os.environ.get('HR_EMPLOYER_REVIEW_ENABLED') == '1'


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS er_preparations (
        application_id TEXT PRIMARY KEY REFERENCES cp_applications(id),
        state TEXT NOT NULL, preparation_id TEXT, core_run_id TEXT,
        source_revision TEXT, person_revision TEXT, assessment TEXT,
        error TEXT, next_check REAL NOT NULL DEFAULT 0, updated REAL NOT NULL,
        preferred TEXT NOT NULL DEFAULT '[]')''')


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='er_preparations'").fetchone())


def enqueue(db, application_id):
    if not enabled():
        return
    init(db)
    db.execute("INSERT OR IGNORE INTO er_preparations(application_id,state,updated) VALUES (?,'waiting_consent',?)",
               (application_id, time.time()))


def ready(db, application_id):
    if not exists(db):
        return True  # Existing applications keep their existing rollout path.
    value = projection(db, application_id)
    return value is None or value['status'] == 'completed'


def tracked(db, application_id):
    return exists(db) and bool(db.execute('SELECT 1 FROM er_preparations WHERE application_id=?', (application_id,)).fetchone())


def projection(db, application_id):
    if not tracked(db, application_id):
        return None
    row = db.execute('SELECT state,assessment,core_run_id,person_revision FROM er_preparations WHERE application_id=?',
                     (application_id,)).fetchone()
    if row[0] != 'ready':
        return {'assessment': None, 'status': row[0]}
    cid = db.execute('SELECT candidate_id FROM cp_applications WHERE id=?', (application_id,)).fetchone()[0]
    latest = assessment_core_bridge.latest(db, cid)
    import selection
    if not latest or latest['status'] != 'completed' or latest['run_id'] != row[2] or selection.person(db, cid)['sourceRevision'] != row[3]:
        return {'assessment': None, 'status': 'stale'}
    return {'assessment': json.loads(row[1]), 'status': 'completed'}


def tasks(db):
    if not exists(db):
        return {'tasks': []}
    result = []
    for aid, state, error, updated in db.execute(
            "SELECT application_id,state,error,updated FROM er_preparations WHERE state IN ('failed','ready') ORDER BY updated DESC"):
        if state == 'failed' or projection(db, aid)['status'] == 'stale':
            result.append({'applicationId': aid, 'error': error or 'review_sources_changed', 'updated': updated,
                           'title': 'Проверить подготовку цитат', 'action': 'manual_retry'})
    return {'tasks': result}


class Bridge:
    def __init__(self, portal, service=None):
        self.portal, self.connect, self.lock = portal, portal.connect, portal.lock
        self.service = service or ServiceHTTP(os.environ.get('HR_EMPLOYER_REVIEW_URL', 'http://127.0.0.1:8098'),
            os.environ.get('HR_EMPLOYER_REVIEW_TOKEN', ''), timeout=12)
        with self.lock, self.connect() as db:
            init(db)

    def prepare(self, aid, preferred):
        require(enabled(), 'review_not_enabled', 503)
        require(isinstance(preferred, list) and len(preferred) <= 40 and
                all(isinstance(p, str) and 0 < len(p) <= 100 for p in preferred) and
                len(set(preferred)) == len(preferred), 'invalid_parameter_order', 400)
        self.authorize(aid)
        with self.lock, self.connect() as db:
            enqueue(db, aid)
            row = db.execute('SELECT state,preferred FROM er_preparations WHERE application_id=?', (aid,)).fetchone()
            require(row[0] == 'waiting_consent' or json.loads(row[1]) == preferred, 'review_already_prepared', 409)
            db.execute('UPDATE er_preparations SET preferred=? WHERE application_id=?', (json.dumps(preferred), aid))
        return {'applicationId': aid, 'status': row[0]}

    def authorize(self, aid, cid=None):
        with self.lock, self.connect() as db:
            row = db.execute('''SELECT a.candidate_id,a.vacancy_id,r.workspace_id,c.profile
              FROM cp_applications a JOIN em_reviews r ON r.application_id=a.id
              JOIN cp_candidates c ON c.id=a.candidate_id
              JOIN em_vacancies v ON v.vacancy_id=a.vacancy_id AND v.workspace_id=r.workspace_id
              WHERE a.id=? AND a.status='accepted' AND a.archived_at IS NULL
              AND c.searching=1 AND v.archived_at IS NULL AND r.state!='rejected' ''', (aid,)).fetchone()
            require(row and json.loads(row[3]).get('consentReuse') is True and
                    (cid is None or cid == row[0]), 'review_consent_or_access_changed', 409)
            return {'candidateId': row[0], 'vacancyId': row[1], 'workspaceId': row[2]}

    def snapshot(self, aid):
        identity = self.authorize(aid)
        cid = identity['candidateId']
        core = assessment_core_bridge.configured(self.connect, self.lock, lambda candidate: self.authorize(aid, candidate))
        value = core.read(cid)  # GET only. This bridge never calls core.start().
        require(value.get('state') == 'ready' and value.get('coreRun', {}).get('status') == 'completed',
                'core_assessment_not_ready', 409)
        materials = self.portal.materials(cid, access_check=lambda db: self.authorize(aid, cid))
        bundle = transcript_bundle.bundle(materials)
        import selection
        with self.connect() as db:
            person = selection.person(db, cid)
        chunks = transcript_bundle.chunks(bundle, person.get('name', ''))
        fields = ('id', 'text', 'recordingId', 'transcriptId', 'speaker', 'startMs', 'endMs')
        clean = [{key: c[key] for key in fields} for c in chunks]
        self.authorize(aid, cid)
        return {'candidateId': cid, 'assessment': value['coreRun']['assessment'],
                'sourceRevision': transcript_bundle.fingerprint(bundle), 'chunks': clean}, person['sourceRevision']

    def preferred(self, aid):
        # Methodology is deliberately not invented here. An operator can supply
        # the established vocabulary order; unknown IDs are never fabricated.
        with self.connect() as db:
            return json.loads(db.execute('SELECT preferred FROM er_preparations WHERE application_id=?', (aid,)).fetchone()[0])

    def fail(self, aid, error):
        code = error.message if isinstance(error, Problem) else 'review_preparation_failed'
        # Machine codes only; no provider or transcript content in a team task.
        if not isinstance(code, str) or not code.replace('_', '').isalnum() or len(code) > 100:
            code = 'review_preparation_failed'
        with self.lock, self.connect() as db:
            db.execute("UPDATE er_preparations SET state='failed',error=?,updated=? WHERE application_id=?",
                       (code, time.time(), aid))

    def tick(self):
        with self.lock, self.connect() as db:
            row = db.execute('''SELECT e.application_id,e.state,e.preparation_id,e.core_run_id,e.source_revision
              FROM er_preparations e JOIN cp_applications a ON a.id=e.application_id
              JOIN em_reviews r ON r.application_id=a.id
              WHERE e.next_check<=? AND ((e.state='waiting_consent' AND a.status='accepted') OR e.state='preparing')
              ORDER BY e.updated LIMIT 1''', (time.time(),)).fetchone()
            if not row:
                return False
            aid, state, pid, run_id, source_revision = row
            db.execute('UPDATE er_preparations SET next_check=? WHERE application_id=?', (time.time() + 10, aid))
        try:
            self.authorize(aid)
            if state == 'waiting_consent':
                body, person_revision = self.snapshot(aid)
                with self.lock, self.connect() as db:
                    db.execute("UPDATE er_preparations SET state='dispatching',updated=? WHERE application_id=?", (time.time(), aid))
                receipt = self.service.call('POST', '/v1/preparations', body)
                require(receipt.get('candidateId') == body['candidateId'] and
                    receipt.get('coreRunId') == body['assessment']['run_id'] and
                    receipt.get('sourceRevision') == body['sourceRevision'], 'review_receipt_mismatch', 502)
                self.authorize(aid, body['candidateId'])
                with self.lock, self.connect() as db:
                    db.execute('''UPDATE er_preparations SET state='preparing',preparation_id=?,core_run_id=?,
                      source_revision=?,person_revision=?,updated=? WHERE application_id=?''',
                      (receipt['preparationId'], receipt['coreRunId'], receipt['sourceRevision'], person_revision, time.time(), aid))
            else:
                receipt = self.service.call('GET', '/v1/preparations/' + pid)
                require(receipt.get('preparationId') == pid and receipt.get('coreRunId') == run_id and
                        receipt.get('sourceRevision') == source_revision, 'review_receipt_mismatch', 502)
                if receipt['status'] == 'failed':
                    raise Problem(receipt.get('error') or 'review_preparation_failed', 502)
                if receipt['status'] != 'ready':
                    return True
                body, person_revision = self.snapshot(aid)
                require(body['assessment']['run_id'] == run_id and body['sourceRevision'] == source_revision,
                        'review_sources_changed', 409)
                output = self.service.call('POST', '/v1/presentations',
                    {'preparationId': pid, 'preferredParameterIds': self.preferred(aid)})
                require(output.get('preparationId') == pid and output.get('assessment', {}).get('revisionId') == run_id,
                        'review_projection_mismatch', 502)
                self.authorize(aid, body['candidateId'])
                with self.lock, self.connect() as db:
                    db.execute("UPDATE er_preparations SET state='ready',assessment=?,error=NULL,person_revision=?,updated=? WHERE application_id=?",
                               (json.dumps(output['assessment'], ensure_ascii=False), person_revision, time.time(), aid))
                    # Visibility and notifications use the same existing store gate.
                    import employer_notifications
                    employer_notifications.collect(db)
        except Exception as error:
            self.fail(aid, error)
        return True

    def retry(self, aid, request_id):
        self.authorize(aid)
        with self.connect() as db:
            row = db.execute('SELECT state,preparation_id,core_run_id,source_revision FROM er_preparations WHERE application_id=?', (aid,)).fetchone()
            stale = row and row[0] == 'ready' and projection(db, aid)['status'] == 'stale'
        require(row and (row[0] == 'failed' or stale), 'review_retry_not_allowed', 409)
        body, _ = self.snapshot(aid)
        reuse = row[1] and row[2] == body['assessment']['run_id'] and row[3] == body['sourceRevision']
        if reuse:
            receipt = self.service.call('GET', '/v1/preparations/' + row[1])
            if receipt['status'] == 'failed':
                self.service.call('POST', '/v1/preparations/' + row[1] + '/retry', {'requestId': request_id})
        with self.lock, self.connect() as db:
            db.execute("UPDATE er_preparations SET state=?,preparation_id=?,error=NULL,next_check=0,updated=? WHERE application_id=?",
                       ('preparing' if reuse else 'waiting_consent', row[1] if reuse else None, time.time(), aid))
        return {'applicationId': aid, 'status': 'queued'}


def start_worker(portal):
    if not enabled():
        return
    bridge = Bridge(portal)
    with portal.lock, portal.connect() as db:
        db.execute("UPDATE er_preparations SET state='failed',error='interrupted_dispatch' WHERE state='dispatching'")
    def run():
        while True:
            try:
                bridge.tick()
            except Exception:
                pass  # No failed job is retried; the next loop only finds queued work.
            time.sleep(1)
    threading.Thread(target=run, daemon=True, name='employer-review-delivery').start()
