"""Project current core parameters for every consented employer application."""
import json
import os
import threading
import time

import assessment_core_bridge
from core_source_client import ServiceHTTP
from domain import Problem, require
import transcript_bundle
import vacancy_profile_bridge


class Waiting(Exception):
    """An upstream result is pending, not a failed quote call."""


def enabled():
    return os.environ.get('HR_EMPLOYER_REVIEW_ENABLED') == '1'


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS er_preparations (
        application_id TEXT PRIMARY KEY REFERENCES cp_applications(id),
        state TEXT NOT NULL, preparation_id TEXT, core_run_id TEXT,
        source_revision TEXT, person_revision TEXT, assessment TEXT,
        error TEXT, next_check REAL NOT NULL DEFAULT 0, updated REAL NOT NULL,
        preferred TEXT NOT NULL DEFAULT '[]')''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(er_preparations)')}
    if 'published' not in columns:
        db.execute('ALTER TABLE er_preparations ADD COLUMN published INTEGER NOT NULL DEFAULT 0')
        db.execute("UPDATE er_preparations SET published=1 WHERE state='ready'")
    if 'input_revision' not in columns:
        db.execute('ALTER TABLE er_preparations ADD COLUMN input_revision TEXT')


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
    row = db.execute('SELECT published FROM er_preparations WHERE application_id=?', (application_id,)).fetchone()
    return row is None or bool(row[0])


def tracked(db, application_id):
    return exists(db) and bool(db.execute('SELECT 1 FROM er_preparations WHERE application_id=?', (application_id,)).fetchone())


def input_revision(db, application_id):
    import selection
    cid, vid = db.execute('SELECT candidate_id,vacancy_id FROM cp_applications WHERE id=?', (application_id,)).fetchone()
    core = assessment_core_bridge.latest(db, cid) or {}
    has_profile = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vp_bindings'").fetchone()
    profile = (vacancy_profile_bridge.latest(db, vid) or {}) if has_profile else {}
    vacancy = db.execute('SELECT body FROM cp_vacancies WHERE id=?', (vid,)).fetchone()[0]
    return assessment_core_bridge.digest([core.get('id'), core.get('run_id'), core.get('status'),
        profile.get('id'), profile.get('profile_id'), profile.get('status'), vacancy,
        selection.person(db, cid)['sourceRevision']])


def projection(db, application_id):
    if not tracked(db, application_id):
        return None
    row = db.execute('SELECT state,assessment,input_revision FROM er_preparations WHERE application_id=?',
                     (application_id,)).fetchone()
    current = row[0] == 'ready' and row[2] == input_revision(db, application_id)
    return {'assessment': json.loads(row[1]) if row[1] else None,
            'status': 'completed' if current else 'failed' if row[0] == 'failed' else 'pending',
            'refreshAfterMs': 15000}


def tasks(db):
    import employer_intake_review
    result = employer_intake_review.tasks(db)
    if not exists(db):
        return {'tasks': result}
    for aid, state, error, updated in db.execute(
            "SELECT application_id,state,error,updated FROM er_preparations WHERE state='failed' ORDER BY updated DESC"):
        result.append({'applicationId': aid, 'error': error, 'updated': updated,
                       'title': 'Проверить подготовку цитат', 'action': 'manual_retry'})
    return {'tasks': result}


class Bridge:
    def __init__(self, portal, service=None):
        self.portal, self.connect, self.lock = portal, portal.connect, portal.lock
        self.service = service or ServiceHTTP(os.environ.get('HR_EMPLOYER_REVIEW_URL', 'http://127.0.0.1:8098'),
            os.environ.get('HR_EMPLOYER_REVIEW_TOKEN', ''), timeout=12)
        with self.lock, self.connect() as db:
            init(db)
        self.discover_at = 0

    def discover(self):
        # The Workbench catalogue is cp_candidates. Existing employer approvals
        # remain the sole membership authority; discovering data never nominates.
        with self.lock, self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO er_preparations(application_id,state,updated,published)
              SELECT a.id,'waiting_consent',?,1 FROM cp_applications a
              JOIN em_reviews r ON r.application_id=a.id JOIN cp_candidates c ON c.id=a.candidate_id
              JOIN em_vacancies v ON v.vacancy_id=a.vacancy_id AND v.workspace_id=r.workspace_id
              WHERE a.status='accepted' AND a.archived_at IS NULL AND c.searching=1
              AND json_extract(c.profile,'$.consentReuse')=1 AND v.archived_at IS NULL AND r.state!='rejected' ''',
              (time.time(),))

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
        require(value.get('state') not in ('error', 'outcome_unknown', 'dispatch_unknown'), 'core_assessment_failed', 409)
        if value.get('state') != 'ready' or value.get('coreRun', {}).get('status') != 'completed':
            raise Waiting()
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

    def vacancy_profile(self, aid):
        identity = self.authorize(aid)
        def authorize(vid):
            require(self.authorize(aid)['vacancyId'] == vid, 'review_vacancy_mismatch', 404)
        value = vacancy_profile_bridge.configured(self.connect, self.lock, authorize).read(identity['vacancyId'])
        require(value.get('state') not in ('error', 'outcome_unknown', 'dispatch_unknown'), 'vacancy_profile_failed', 409)
        if value.get('state') not in ('ready', 'needs_review') or not value.get('profile'):
            raise Waiting()
        return value['profile']

    def fail(self, aid, error):
        code = error.message if isinstance(error, Problem) else 'review_preparation_failed'
        # Machine codes only; no provider or transcript content in a team task.
        if not isinstance(code, str) or not code.replace('_', '').isalnum() or len(code) > 100:
            code = 'review_preparation_failed'
        with self.lock, self.connect() as db:
            db.execute("UPDATE er_preparations SET state='failed',error=?,updated=? WHERE application_id=?",
                       (code, time.time(), aid))

    def tick(self):
        if time.time() >= self.discover_at:
            self.discover()
            self.discover_at = time.time() + 30
        with self.lock, self.connect() as db:
            row = db.execute('''SELECT e.application_id,e.state,e.preparation_id,e.core_run_id,e.source_revision,e.input_revision
              FROM er_preparations e JOIN cp_applications a ON a.id=e.application_id
              JOIN em_reviews r ON r.application_id=a.id
              JOIN cp_candidates c ON c.id=a.candidate_id
              JOIN em_vacancies v ON v.vacancy_id=a.vacancy_id AND v.workspace_id=r.workspace_id
              WHERE e.next_check<=? AND a.status='accepted' AND a.archived_at IS NULL
              AND c.searching=1 AND v.archived_at IS NULL AND r.state!='rejected'
              AND e.state IN ('waiting_consent','waiting_sources','preparing','ready')
              ORDER BY e.next_check,e.updated,e.application_id LIMIT 1''', (time.time(),)).fetchone()
            if not row:
                return False
            aid, state, pid, run_id, source_revision, previous = row
            revision = input_revision(db, aid)
            db.execute('UPDATE er_preparations SET next_check=? WHERE application_id=?', (time.time() + 15, aid))
            if state == 'ready' and previous == revision:
                return False
        try:
            self.authorize(aid)
            if state != 'preparing':
                body, person_revision = self.snapshot(aid)
                self.vacancy_profile(aid)  # Wait for the vacancy; do not invent an order.
                with self.lock, self.connect() as db:
                    if input_revision(db, aid) != revision:
                        raise Waiting()
                    db.execute("UPDATE er_preparations SET state='dispatching',updated=? WHERE application_id=?", (time.time(), aid))
                receipt = self.service.call('POST', '/v1/preparations', body)
                require(receipt.get('candidateId') == body['candidateId'] and
                    receipt.get('coreRunId') == body['assessment']['run_id'] and
                    receipt.get('sourceRevision') == body['sourceRevision'], 'review_receipt_mismatch', 502)
                self.authorize(aid, body['candidateId'])
                with self.lock, self.connect() as db:
                    db.execute('''UPDATE er_preparations SET state='preparing',preparation_id=?,core_run_id=?,
                      source_revision=?,person_revision=?,input_revision=?,updated=? WHERE application_id=?''',
                      (receipt['preparationId'], receipt['coreRunId'], receipt['sourceRevision'], person_revision, revision, time.time(), aid))
            else:
                receipt = self.service.call('GET', '/v1/preparations/' + pid)
                require(receipt.get('preparationId') == pid and receipt.get('coreRunId') == run_id and
                        receipt.get('sourceRevision') == source_revision, 'review_receipt_mismatch', 502)
                if receipt['status'] == 'failed':
                    raise Problem(receipt.get('error') or 'review_preparation_failed', 502)
                if receipt['status'] != 'ready':
                    return True
                body, person_revision = self.snapshot(aid)
                if body['assessment']['run_id'] != run_id or body['sourceRevision'] != source_revision:
                    raise Waiting()
                vacancy = self.vacancy_profile(aid)
                output = self.service.call('POST', '/v1/presentations',
                    {'preparationId': pid, 'preferredParameterIds': [], 'vacancyProfile': vacancy})
                require(output.get('preparationId') == pid and output.get('assessment', {}).get('revisionId') == run_id,
                        'review_projection_mismatch', 502)
                require(output['assessment'].get('provenance', {}).get('vacancyProfileId') == vacancy['profile_id'],
                        'review_vacancy_mismatch', 502)
                self.authorize(aid, body['candidateId'])
                with self.lock, self.connect() as db:
                    if input_revision(db, aid) != revision:
                        raise Waiting()
                    published = db.execute('SELECT published FROM er_preparations WHERE application_id=?', (aid,)).fetchone()[0]
                    db.execute("UPDATE er_preparations SET state='ready',assessment=?,error=NULL,person_revision=?,input_revision=?,published=1,updated=? WHERE application_id=?",
                               (json.dumps(output['assessment'], ensure_ascii=False), person_revision, revision, time.time(), aid))
                    # Visibility and notifications use the same existing store gate.
                    if not published:
                        import employer_notifications
                        employer_notifications.collect(db)
        except Waiting:
            with self.lock, self.connect() as db:
                db.execute("UPDATE er_preparations SET state='waiting_sources',preparation_id=NULL,error=NULL WHERE application_id=?", (aid,))
        except Exception as error:
            self.fail(aid, error)
        return True

    def retry(self, aid, request_id):
        self.authorize(aid)
        with self.connect() as db:
            row = db.execute('SELECT state,preparation_id,core_run_id,source_revision FROM er_preparations WHERE application_id=?', (aid,)).fetchone()
            stale = row and row[0] == 'ready' and projection(db, aid)['status'] == 'pending'
        require(row and (row[0] == 'failed' or stale), 'review_retry_not_allowed', 409)
        try:
            body, _ = self.snapshot(aid)
        except Waiting:
            raise Problem('core_assessment_not_ready', 409) from None
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
    with portal.lock, portal.connect() as db:
        if exists(db):
            init(db)  # Keep reads compatible when the worker flag is disabled.
    if not enabled():
        return
    bridge = Bridge(portal)
    import employer_intake_review
    intake_bridge = employer_intake_review.Bridge(portal)
    with portal.lock, portal.connect() as db:
        db.execute("UPDATE er_preparations SET state='failed',error='interrupted_dispatch' WHERE state='dispatching'")
    def run():
        while True:
            try:
                bridge.tick()
            except Exception:
                pass  # No failed job is retried; the next loop only finds queued work.
            try:
                intake_bridge.tick()
            except Exception:
                pass  # The durable intake state exposes terminal failures to the team.
            time.sleep(1)
    threading.Thread(target=run, daemon=True, name='employer-review-delivery').start()
