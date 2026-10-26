"""HR-owned durable bindings to reproducible, model-free recommendations."""
import json
import os
import re
import time
from pathlib import Path
from domain import Problem, require
from core_source_client import ServiceHTTP
from assessment_core_bridge import digest, dump
import selection_facts
import vacancy_profile_bridge

MAX_VERIFICATION_ATTEMPTS = 8
POLICY = {'version': 'tanh-higher-v2', 'k': 1, 'probability_mode': 'conditional_levels'}


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rc_bindings (
        id TEXT PRIMARY KEY,vacancy_id TEXT NOT NULL,source_revision TEXT NOT NULL,
        candidate_ids TEXT NOT NULL,input_versions TEXT NOT NULL,request TEXT NOT NULL,
        recommendation_id TEXT,result TEXT,pending TEXT NOT NULL,created REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS rc_vacancy ON rc_bindings(vacancy_id,created DESC)')
    db.execute('''CREATE TABLE IF NOT EXISTS sl_recommendation_nomination_history (
        id TEXT PRIMARY KEY,body TEXT NOT NULL,archived_at REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS sl_recommendation_nominations (
        id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id),
        recommendation_id TEXT NOT NULL,binding_id TEXT NOT NULL REFERENCES rc_bindings(id),unknown_approval TEXT,
        state TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(candidate_id,vacancy_id))''')
    if 'error' not in {r[1] for r in db.execute('PRAGMA table_info(sl_recommendation_nominations)')}:
        db.execute('ALTER TABLE sl_recommendation_nominations ADD COLUMN error TEXT')
    columns = {r[1] for r in db.execute('PRAGMA table_info(sl_recommendation_nominations)')}
    if 'retry_at' not in columns:
        db.execute('ALTER TABLE sl_recommendation_nominations ADD COLUMN retry_at REAL NOT NULL DEFAULT 0')
    if 'attempts' not in columns:
        db.execute('ALTER TABLE sl_recommendation_nominations ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')


def latest(db, vid):
    cursor = db.execute('SELECT * FROM rc_bindings WHERE vacancy_id=? ORDER BY created DESC,rowid DESC LIMIT 1', (vid,))
    row = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], row)) if row else None


def configured(connect, lock, authorize_vacancy, authorize_candidate):
    import assessment_core_bridge
    try:
        return Bridge(connect, lock, authorize_vacancy, authorize_candidate,
            vacancy_profile_bridge.configured(connect, lock, authorize_vacancy),
            assessment_core_bridge.configured(connect, lock, authorize_candidate),
            ServiceHTTP(os.environ.get('HR_RECOMMENDATIONS_URL', 'http://127.0.0.1:8099'),
                        os.environ['HR_RECOMMENDATIONS_TOKEN'], timeout=10))
    except (KeyError, ValueError):
        raise Problem('Настройка рекомендаций недоступна.', 503) from None


def automatic_candidate_ids(portal, db, vacancy_id):
    """Complete HR-ready pool; account/consent restrictions govern invitations."""
    import selection
    import selection_status
    value = selection.vacancy(db, vacancy_id)
    selection.employers.ensure_open(db, value['workspaceId'], vacancy_id)
    return [cid for (cid,) in db.execute('SELECT id FROM cp_candidates ORDER BY id').fetchall()
            if selection_status.evidence(db, selection.person(db, cid))['state'] == 'ready']


def profile_comparable(value):
    """Keep source uncertainty, but permit an absent target under higher-v2."""
    profile = value.get('profile')
    if not isinstance(profile, dict):
        return False
    if value.get('state') == 'ready':
        return True  # The recommendation service validates the full contract.
    parameters = profile.get('parameters')
    return bool(value.get('state') == 'needs_review' and isinstance(parameters, list) and parameters
                and all(type(p.get('importance')) is int and 0 <= p['importance'] <= 3
                        for p in parameters))


def materials_version(db, person):
    import selection_status
    value = selection_status.evidence(db, person)
    # Verification timestamps change on every healthy scan, not the input.
    return {key: value.get(key) for key in ('state', 'materialsRevision', 'allPartsReady',
            'recordingCount', 'transcriptCount', 'contractVersion', 'items')}


def local_versions(db, vid, cids):
    import assessment_core_bridge
    import selection
    v = selection.vacancy(db, vid)
    vp = vacancy_profile_bridge.latest(db, vid)
    candidates = {}
    for cid in cids:
        row = assessment_core_bridge.latest(db, cid)
        person = selection.person(db, cid)
        candidates[cid] = {'binding': row['id'] if row else None, 'run': row['run_id'] if row else None,
            'source': row['source_revision'] if row else None, 'rubric': row['rubric_hash'] if row else None,
            'model': row['model'] if row else None, 'status': row['status'] if row else None, 'format': row.get('request_format') if row else None,
            'fields': selection_facts.read(db, 'candidate', cid)['revision'],
            'input': person['sourceRevision'], 'materials': materials_version(db, person)}
    try:
        runtime_rubric = digest(json.loads(Path(os.environ['HR_ASSESSMENT_CORE_RUBRIC']).read_text()))
    except (KeyError, OSError, ValueError):
        runtime_rubric = None
    return {'runtime_rubric': runtime_rubric, 'runtime_model': os.environ.get('HR_ASSESSMENT_CORE_MODEL', 'jev-1.14.0'),
            'vacancy': v['contentHash'], 'profile': vp['profile_id'] if vp else None,
            'rubric': vp['rubric_hash'] if vp else None, 'model': vp['model'] if vp else None,
            'vacancy_fields': selection_facts.read(db, 'vacancy', vid)['revision'], 'candidates': candidates,
            'policy': POLICY, 'pool': cids,
            'ready_pool': automatic_candidate_ids(None, db, vid)}


def projections(db, vid):
    row = latest(db, vid)
    if not row:
        return {}
    ids = json.loads(row['candidate_ids'])
    if local_versions(db, vid, ids) != json.loads(row['input_versions']):
        return {cid: {'state': 'stale', 'origin': 'recommendations'} for cid in ids}
    output = {p['candidateId']: {'state': 'waiting_assessment', 'origin': 'recommendations',
              'candidateState': p['state']} for p in json.loads(row['pending'])}
    if row['result']:
        output.update({i['candidate_id']: {'state': 'compared', 'origin': 'recommendations',
                       'recommendationId': row['recommendation_id'], **i} for i in json.loads(row['result'])['items']})
    for cid in ids:
        output.setdefault(cid, {'state': 'dispatch_unknown', 'origin': 'recommendations'})
    return output


def projection(db, vid, cid):
    return projections(db, vid).get(cid, {'state': 'not_compared', 'origin': 'recommendations'})


class Bridge:
    def __init__(self, connect, lock, authorize_vacancy, authorize_candidate, vacancy, candidate, service):
        self.connect, self.lock, self.authorize_vacancy, self.authorize_candidate = connect, lock, authorize_vacancy, authorize_candidate
        self.vacancy, self.candidate, self.service = vacancy, candidate, service

    def versions(self, vid, cids):
        self.authorize_vacancy(vid)
        for cid in cids:
            self.authorize_candidate(cid)
        with self.connect() as db:
            return local_versions(db, vid, cids)

    def gather(self, vid, cids):
        versions = self.versions(vid, cids)
        profile = self.vacancy.read(vid)
        require(profile_comparable(profile), 'Уточните важность навыков вакансии.', 409)
        ready, pending = [], []
        for cid in cids:
            value = self.candidate.read(cid)
            if value['state'] == 'ready':
                core = value.get('coreRun', {}).get('assessment')
                require(isinstance(core, dict) and core.get('candidate_id') == cid, 'Не удалось подтвердить профиль кандидата.', 502)
                ready.append(core)
            else:
                pending.append({'candidateId': cid, 'state': value['state']})
        with self.connect() as db:
            request = {'vacancy_profile': profile['profile'], 'candidate_profiles': ready,
                'vacancy_fields': selection_facts.contract(db, 'vacancy', vid),
                'candidate_fields': [selection_facts.contract(db, 'candidate', p['candidate_id']) for p in ready], 'policy': POLICY}
        require(self.versions(vid, cids) == versions, 'Исходные данные изменились.', 409)
        return request, pending, versions

    def validate(self, result, request):
        vp = request['vacancy_profile']
        require(isinstance(result, dict) and result.get('contract_version') == 'recommendations-v1'
            and isinstance(result.get('recommendation_id'), str) and re.fullmatch('[a-f0-9]{64}', result['recommendation_id'])
            and result.get('vacancy_id') == vp['vacancy_id'] and result.get('vacancy_profile_id') == vp['profile_id']
            and all(result.get(key) == vp[key] for key in ('rubric_id', 'rubric_version', 'rubric_sha256'))
            and result.get('policy') == POLICY and isinstance(result.get('items'), list)
            and len(result['items']) == len(request['candidate_profiles'])
            and {i.get('candidate_id') for i in result['items']} == {i['candidate_id'] for i in request['candidate_profiles']},
            'Рекомендация относится к другим данным.', 502)
        return result

    def start(self, vid, revision, cids):
        require(isinstance(cids, list) and 1 <= len(cids) <= 1000 and all(isinstance(c, str) for c in cids)
                and len(set(cids)) == len(cids), 'Выберите явный пул до 1000 кандидатов.', 400)
        cids = sorted(cids)
        request, pending, versions = self.gather(vid, cids)
        require(revision == versions['vacancy'], 'Вакансия изменилась.', 409)
        identity = digest([request, pending, versions])
        with self.lock, self.connect() as db:
            self.authorize_vacancy(vid)
            init(db)
            db.execute('INSERT OR IGNORE INTO rc_bindings VALUES (?,?,?,?,?,?,NULL,NULL,?,?)',
                (identity, vid, revision, dump(cids), dump(versions), dump(request), dump(pending), time.time()))
            db.execute('UPDATE rc_bindings SET created=? WHERE id=?', (time.time(), identity))
            old = db.execute('SELECT recommendation_id FROM rc_bindings WHERE id=?', (identity,)).fetchone()[0]
        result = None
        if request['candidate_profiles']:
            result = self.validate(self.service.call('GET', '/v1/recommendations/' + old) if old else
                self.service.call('POST', '/v1/recommendations', request), request)
        require(self.versions(vid, cids) == versions, 'Исходные данные изменились.', 409)
        with self.lock, self.connect() as db:
            self.authorize_vacancy(vid)
            require(local_versions(db, vid, cids) == versions, 'Исходные данные изменились.', 409)
            db.execute('UPDATE rc_bindings SET recommendation_id=?,result=? WHERE id=?',
                (result['recommendation_id'] if result else None, dump(result) if result else None, identity))
        return self.envelope(vid, versions, cids, pending, result, [])

    def envelope(self, vid, versions, cids, pending, result, reasons):
        import selection
        eligibility = []
        with self.lock, self.connect() as db:
            if local_versions(db, vid, cids) != versions:
                reasons = list(dict.fromkeys([*reasons, 'changed_during_projection']))
            for cid in cids:
                p = selection.person(db, cid)
                channels = [channel for channel, key in [('email', 'emailConnected'), ('telegram', 'telegramConnected')] if p[key]]
                item = next((i for i in (result or {}).get('items', []) if i['candidate_id'] == cid), None)
                evidence = __import__('selection_status').evidence(db, p)
                existing = db.execute('SELECT 1 FROM cp_applications WHERE candidate_id=? AND vacancy_id=? AND archived_at IS NULL', (cid,vid)).fetchone()
                nominated = db.execute("SELECT 1 FROM sl_recommendation_nominations WHERE candidate_id=? AND vacancy_id=? AND NOT (state='blocked' AND recommendation_id<>?)", (cid,vid,(result or {}).get('recommendation_id',''))).fetchone()
                can = bool(channels and not existing and not nominated and not reasons and item and item['status'] == 'scored' and item['eligibility'] != 'fail'
                    and p['searching'] and evidence['state'] == 'ready')
                eligibility.append({'candidateId': cid, 'canInvite': can,
                    'reason': 'unknown_requires_approval' if can and item['eligibility'] == 'unknown' else
                              'ready' if can else 'stale' if reasons else 'already_added' if existing or nominated else 'no_delivery_channel' if not channels else 'not_eligible', 'channels': channels})
        import recommendation_lifecycle
        with self.connect() as db:
            automation = recommendation_lifecycle.projection(db, vid)
        return {'vacancyId': vid, 'revision': versions.get('vacancy'), 'state': 'stale' if reasons else
                'ready' if result else 'waiting_assessment', 'stale': bool(reasons), 'staleReasons': reasons,
                'canRun': True, 'recommendationId': result['recommendation_id'] if result else None,
                'result': result, 'pendingCandidates': pending, 'candidateIds': cids, 'invitationEligibility': eligibility,
                'automation': automation}

    def read(self, vid):
        self.authorize_vacancy(vid)
        with self.connect() as db:
            row = latest(db, vid)
        if not row:
            versions = self.versions(vid, [])
            return {**self.envelope(vid, versions, [], [], None, []), 'state': 'not_started'}
        cids, saved = json.loads(row['candidate_ids']), json.loads(row['input_versions'])
        current = self.versions(vid, cids)
        reasons = [k for k in current if current[k] != saved.get(k)]
        result = json.loads(row['result']) if row['result'] else None
        pending = json.loads(row['pending'])
        if not reasons:
            try:
                request, fresh_pending, fresh = self.gather(vid, cids)
            except Problem as problem:
                if problem.status != 409:
                    raise
                return self.envelope(vid, current, cids, pending, result, ['profile_not_ready'])
            if fresh_pending != pending or request != json.loads(row['request']):
                reasons.append('profiles')
            if result and not reasons:
                received = self.validate(self.service.call('GET', '/v1/recommendations/' + row['recommendation_id']), request)
                require(received == result, 'Сохранённая рекомендация изменилась.', 502)
            if self.versions(vid, cids) != current:
                reasons.append('changed_during_read')
        value = self.envelope(vid, current, cids, pending, result, reasons)
        if not reasons and result is None and json.loads(row['request'])['candidate_profiles']:
            value['state'] = 'dispatch_unknown'
        return value


class NominationWorker:
    """Durable consent continuation. Only GETs before the final local transaction."""
    def __init__(self, connect, lock, portal, *, bridge_factory=None, interval=5, clock=time.time):
        import threading
        self.connect, self.lock, self.portal = connect, lock, portal
        self.bridge_factory = bridge_factory or configured
        self.interval, self.clock = interval, clock
        self.stop_event = threading.Event()
        self.thread = None
        self.last_error = None

    def status(self):
        with self.connect() as db:
            counts = dict(db.execute('SELECT state,COUNT(*) FROM sl_recommendation_nominations GROUP BY state'))
        return {'running': bool(self.thread and self.thread.is_alive()), 'states': counts, 'error': self.last_error, 'maxAttempts': MAX_VERIFICATION_ATTEMPTS}

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def run_once(self, limit=20):
        import selection
        import selection_status
        from domain import iso, now
        with self.connect() as db:
            rows = db.execute("SELECT id,candidate_id,vacancy_id,binding_id,recommendation_id FROM sl_recommendation_nominations WHERE state='pending_verification' AND retry_at<=? ORDER BY retry_at,created_at,id LIMIT ?", (self.clock(),limit)).fetchall()
        processed = 0
        for nid, cid, vid, bid, rid in rows:
            if self.stop_event.is_set():
                break
            with self.connect() as db:
                binding = latest(db, vid)
                pool = json.loads(binding['candidate_ids']) if binding and binding['id'] == bid else []
            def authorize_vacancy(resource_id):
                require(resource_id == vid, 'nomination_vacancy_access_denied', 403)
                with self.connect() as db:
                    row = db.execute('SELECT state,binding_id FROM sl_recommendation_nominations WHERE id=?', (nid,)).fetchone()
                    require(row and row[0] == 'pending_verification' and row[1] == bid, 'nomination_changed', 409)
                    selection.vacancy(db, vid)
            def authorize_candidate(resource_id):
                require(resource_id in pool, 'nomination_candidate_access_denied', 403)
                authorize_vacancy(vid)
                with self.connect() as db:
                    selection.person(db, resource_id)
            error = None
            try:
                bridge = self.bridge_factory(self.connect, self.lock, authorize_vacancy, authorize_candidate)
                verified = bridge.read(vid)
                if self.stop_event.is_set():
                    break
                require(verified['state'] == 'ready' and verified['recommendationId'] == rid and not verified['staleReasons'],
                        'recommendation_stale', 409)
                with self.lock, self.connect() as db:
                    row = db.execute('SELECT state FROM sl_recommendation_nominations WHERE id=?', (nid,)).fetchone()
                    require(row and row[0] == 'pending_verification' and selection.recommendation_nomination_current(db,nid), 'nomination_changed',409)
                    p = selection.person(db,cid)
                    require(p['searching'] and p['accountActive'] and p['consentReuse'] and
                            selection_status.evidence(db,p)['state'] == 'ready', 'candidate_consent_or_sources_changed',409)
                    v = selection.vacancy(db,vid)
                    selection.employers.ensure_open(db,v['workspaceId'],vid)
                    item = next((i for i in verified['result']['items'] if i['candidate_id'] == cid), None)
                    require(item and item['status'] == 'scored' and item['eligibility'] != 'fail', 'candidate_not_eligible',409)
                    selection.employers.import_batch(db,v['workspaceId'],vid,{'source':'core','version':'recommendations-v1',
                        'externalBatchId':nid,'candidates':[{'candidateId':cid,'rank':item['rank'],
                        'reason':'Рекомендация по проверенной версии профилей и обязательных полей.'}]}, self.portal)
                    db.execute("UPDATE sl_recommendation_nominations SET state='proposed',error=NULL,retry_at=0,updated_at=? WHERE id=?",(iso(now()),nid))
            except Problem as problem:
                error = 'recommendation_stale' if problem.status == 409 else 'verification_unavailable' if problem.status >= 500 else 'verification_rejected'
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                error = 'verification_unavailable'
            if error:
                with self.lock, self.connect() as db:
                    if error == 'verification_unavailable':
                        attempts = db.execute('SELECT attempts FROM sl_recommendation_nominations WHERE id=?', (nid,)).fetchone()[0] + 1
                        delay = min(300, self.interval * 2 ** min(attempts - 1, 8))
                        db.execute("UPDATE sl_recommendation_nominations SET state=?,error=?,attempts=?,retry_at=?,updated_at=? WHERE id=? AND state='pending_verification'", ('verification_failed' if attempts >= MAX_VERIFICATION_ATTEMPTS else 'pending_verification',error,attempts,0 if attempts >= MAX_VERIFICATION_ATTEMPTS else self.clock()+delay,iso(now()),nid))
                    else:
                        db.execute("UPDATE sl_recommendation_nominations SET state='blocked',error=?,retry_at=0,updated_at=? WHERE id=? AND state='pending_verification'",(error,iso(now()),nid))
            processed += 1
        return processed

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.run_once()
                self.last_error = None
            except Exception:
                self.last_error = 'nomination_worker_unavailable'
            self.stop_event.wait(self.interval)


def start_background(connect, lock, portal):
    import threading
    import assessment_core_bridge
    if not assessment_core_bridge.enabled():
        return None
    with lock, connect() as db:
        init(db)
    worker = NominationWorker(connect, lock, portal)
    worker.thread = threading.Thread(target=worker.run, name='recommendation-nominations', daemon=True)
    worker.thread.start()
    return worker
