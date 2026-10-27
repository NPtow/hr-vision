"""Connect existing employer-owned imports to the same core and display service.

No assessment algorithm, candidate nomination, consent or UI lives here. Browser
reads are read-only; one durable worker submits exact full snapshots to the core.
"""
import json
import os
from pathlib import Path
import time

import assessment_core_bridge as core_bridge
import canonical_materials
from core_source_client import ServiceHTTP
from domain import Problem, require
import employer_intake
from interview_assistant import transcript_turns
import transcript_bundle
import vacancy_profile_bridge


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS er_intake_preparations (
      intake_id TEXT PRIMARY KEY REFERENCES em_intake_candidates(id),
      state TEXT NOT NULL, source_revision TEXT, run_id TEXT, preparation_id TEXT,
      assessment TEXT, error TEXT, next_check REAL NOT NULL DEFAULT 0, updated REAL NOT NULL)''')


def exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='er_intake_preparations'").fetchone() is not None


def projection(db, wid, vid, cid):
    person, _ = employer_intake._review_person(db, wid, vid, {'id': cid})
    require(not person.get('archived'), 'Кандидат недоступен.', 404)
    row = db.execute('SELECT state,assessment FROM er_intake_preparations WHERE intake_id=?', (cid,)).fetchone() if exists(db) else None
    return {'assessment': json.loads(row[1]) if row and row[1] else None,
            'status': 'completed' if row and row[0] == 'ready' else 'failed' if row and row[0] == 'failed' else 'pending',
            'refreshAfterMs': 15000, 'readOnly': True}


def tasks(db):
    return [] if not exists(db) else [{'intakeId': cid, 'error': error, 'updated': updated,
        'title': 'Проверить аналитику кандидата', 'action': 'manual_retry'}
        for cid, error, updated in db.execute("SELECT intake_id,error,updated FROM er_intake_preparations WHERE state='failed'")]


class Bridge:
    def __init__(self, portal, *, core=None, service=None, rubric=None):
        self.connect, self.lock = portal.connect, portal.lock
        self.core = core or ServiceHTTP(os.environ.get('HR_ASSESSMENT_CORE_URL', 'http://127.0.0.1:8097'),
            os.environ.get('HR_ASSESSMENT_CORE_TOKEN', ''), timeout=15)
        self.service = service or ServiceHTTP(os.environ.get('HR_EMPLOYER_REVIEW_URL', 'http://127.0.0.1:8098'),
            os.environ.get('HR_EMPLOYER_REVIEW_TOKEN', ''), timeout=15)
        self.rubric = rubric or json.loads(Path(os.environ['HR_ASSESSMENT_CORE_RUBRIC']).read_text())
        self.verifier = core_bridge.Bridge(self.connect, self.lock, lambda cid: None, None, self.core,
            self.rubric, os.environ.get('HR_ASSESSMENT_CORE_MODEL', 'jev-1.14.0'))
        with self.lock, self.connect() as db:
            init(db)

    def identity(self, cid):
        with self.connect() as db:
            row = db.execute('''SELECT i.workspace_id,i.vacancy_id,i.body FROM em_intake_candidates i
              JOIN em_vacancies v ON v.vacancy_id=i.vacancy_id AND v.workspace_id=i.workspace_id
              WHERE i.id=? AND v.archived_at IS NULL''', (cid,)).fetchone()
            require(row and not json.loads(row[2]).get('archived'), 'intake_access_changed', 404)
            person, _ = employer_intake._review_person(db, row[0], row[1], {'id': cid})
        return row[0], row[1], person

    def snapshot(self, cid):
        wid, vid, person = self.identity(cid)
        with self.connect() as db:
            materials = canonical_materials.read_intake_db(db, wid, vid, cid)
            files = db.execute('SELECT id,text_content,parse_status FROM em_intake_files WHERE candidate_id=? ORDER BY id', (cid,)).fetchall()
        bundle = transcript_bundle.bundle(materials)
        profile = person.get('preparedProfile') or {}
        # Only the verified-import adapter can assert a shared external identity.
        candidate_id = ('dsa-panel:' + materials['source']['candidateId']
                        if materials.get('materialOrigin') == 'verified-import' else 'intake:' + cid)
        sources = []
        cv = profile.get('cvText') or ''
        if cv.strip():
            sources.append({'source_id': 'cv:' + candidate_id, 'version': core_bridge.digest(cv), 'kind': 'cv',
                            'units': [{'unit_id': 'stored', 'text': cv, 'role': 'candidate'}]})
        for fid, text, status in files:
            require(status == 'parsed' and text.strip(), 'resume_text_unavailable', 409)
            if text != cv:
                sources.append({'source_id': 'file:' + fid, 'version': core_bridge.digest(text), 'kind': 'cv',
                                'units': [{'unit_id': 'stored', 'text': text, 'role': 'candidate'}]})
        for part in bundle['parts']:
            if not part['segments'] and part['text'].startswith('WEBVTT\n'):
                # One complete table instead of repeated JSON keys or verbose
                # VTT markup. Every parsed word, voice and millisecond survives;
                # source/version still identifies the original immutable VTT.
                turns = transcript_turns(part['text'])
                require(bool(turns), 'transcript_format_unsupported', 422)
                speakers = list(dict.fromkeys(t['speakerName'] for t in turns))
                rows = [[t['startMs'], t['endMs'], speakers.index(t['speakerName']), t['text']] for t in turns]
                table = json.dumps({'columns': ['start_ms', 'end_ms', 'speaker_index', 'text'],
                    'speakers': speakers, 'rows': rows}, ensure_ascii=False, separators=(',', ':'))
                sources.append({'source_id': part['recordingId'], 'version': core_bridge.digest(part),
                    'kind': 'interview', 'units': [{'unit_id': 'transcript', 'text': table, 'role': 'unknown'}]})
                continue
            turns = part['segments'] or transcript_turns(part['text'])
            require(bool(turns), 'transcript_format_unsupported', 422)
            units = []
            for index, turn in enumerate(turns):
                voice = turn.get('speakerName') or turn.get('speakerId') or ''
                units.append({'unit_id': str(index), 'text': turn['text'], 'speaker': voice,
                    'role': {'Кандидат': 'candidate', 'Рекрутер': 'interviewer'}.get(voice, 'unknown'),
                    'start_ms': round(turn['startMs']), 'end_ms': round(turn['endMs'])})
            sources.append({'source_id': part['recordingId'], 'version': core_bridge.digest(part),
                            'kind': 'interview', 'units': units})
        corpus = {'candidate_id': candidate_id, 'sources': sources}
        chunks = transcript_bundle.chunks(bundle, person.get('name', ''))
        def authorize_vacancy(value):
            require(self.identity(cid)[1] == value, 'intake_access_changed', 404)
        value = vacancy_profile_bridge.configured(self.connect, self.lock, authorize_vacancy).read(vid)
        require(value.get('state') not in ('error', 'outcome_unknown', 'dispatch_unknown'), 'vacancy_profile_failed', 422)
        require(value.get('profile') is not None, 'waiting_for_vacancy_profile', 409)
        vacancy = value['profile']
        revision = core_bridge.digest([corpus, self.rubric, vacancy, core_bridge.REQUEST_FORMAT])
        self.identity(cid)
        return {'corpus': corpus, 'chunks': chunks, 'sourceRevision': transcript_bundle.fingerprint(bundle),
                'vacancy': vacancy, 'revision': revision}

    def discover(self):
        with self.lock, self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO er_intake_preparations(intake_id,state,updated)
              SELECT i.id,'waiting_sources',? FROM em_intake_candidates i JOIN em_vacancies v
              ON v.vacancy_id=i.vacancy_id AND v.workspace_id=i.workspace_id
              WHERE v.archived_at IS NULL AND COALESCE(json_extract(i.body,'$.archived'),0)=0''', (time.time(),))

    def save(self, cid, **values):
        self.identity(cid)
        with self.lock, self.connect() as db:
            db.execute('UPDATE er_intake_preparations SET ' + ','.join(k+'=?' for k in values)
                       + ',updated=? WHERE intake_id=?', [*values.values(), time.time(), cid])

    def tick(self):
        self.discover()
        with self.lock, self.connect() as db:
            row = db.execute('''SELECT e.intake_id,e.state,e.source_revision,e.run_id,e.preparation_id
              FROM er_intake_preparations e JOIN em_intake_candidates i ON i.id=e.intake_id
              JOIN em_vacancies v ON v.vacancy_id=i.vacancy_id AND v.workspace_id=i.workspace_id
              WHERE e.next_check<=? AND e.state!='failed' AND v.archived_at IS NULL
              AND COALESCE(json_extract(i.body,'$.archived'),0)=0 ORDER BY e.next_check,e.updated LIMIT 1''', (time.time(),)).fetchone()
        if not row:
            return False
        cid, state, revision, run_id, pid = row
        self.save(cid, next_check=time.time() + (60 if state == 'ready' else 15))
        try:
            snap = self.snapshot(cid)
            if state == 'ready' and revision == snap['revision']:
                return False
            changed = revision != snap['revision']
            if changed:
                run_id, pid = None, None
            receipt = self.core.call('GET', '/v1/assessments/' + run_id) if run_id else self.core.call(
                'POST', '/v1/assessments', {'corpus': snap['corpus'], 'rubric': self.rubric})
            self.verifier.validate_receipt(receipt, snap['corpus']['candidate_id'], run_id)
            self.save(cid, state='evaluating', source_revision=snap['revision'], run_id=receipt['run_id'], preparation_id=pid)
            require(receipt['status'] not in ('failed', 'outcome_unknown'), 'core_assessment_failed', 422)
            if receipt['status'] != 'completed':
                return True
            body = {'candidateId': snap['corpus']['candidate_id'], 'assessment': receipt['assessment'],
                    'sourceRevision': snap['sourceRevision'], 'chunks': snap['chunks']}
            if not pid:
                levels = self.service.call('POST', '/v1/levels', {k: v for k, v in
                    {**body, 'vacancyProfile': snap['vacancy']}.items() if k != 'chunks'})
                self.publish(cid, snap, receipt, levels, state='preparing')
                quote = self.service.call('POST', '/v1/preparations', body)
                require(quote.get('candidateId') == body['candidateId'] and quote.get('coreRunId') == receipt['run_id']
                        and quote.get('sourceRevision') == body['sourceRevision'], 'review_receipt_mismatch', 502)
                pid = quote['preparationId']
                self.save(cid, preparation_id=pid)
            else:
                quote = self.service.call('GET', '/v1/preparations/' + pid)
            require(quote.get('coreRunId') == receipt['run_id'] and quote.get('sourceRevision') == body['sourceRevision'], 'review_receipt_mismatch', 502)
            require(quote['status'] != 'failed', 'quote_preparation_failed', 422)
            if quote['status'] == 'ready':
                output = self.service.call('POST', '/v1/presentations', {'preparationId': pid,
                    'preferredParameterIds': [], 'vacancyProfile': snap['vacancy']})
                self.publish(cid, snap, receipt, output, state='ready')
            return True
        except Problem as error:
            waiting = error.message in ('waiting_for_transcript', 'waiting_for_vacancy_profile')
            code = error.message
            if not isinstance(code, str) or not code.replace('_', '').isalnum() or len(code) > 100:
                code = 'intake_review_failed'
            self.save(cid, state='waiting_sources' if waiting else 'failed', error=None if waiting else code)
        except Exception:
            self.save(cid, state='failed', error='intake_review_failed')
        return True

    def publish(self, cid, snap, receipt, output, *, state):
        require(output.get('assessment', {}).get('revisionId') == receipt['run_id'] and
                output['assessment'].get('provenance', {}).get('vacancyProfileId') == snap['vacancy']['profile_id'],
                'review_projection_mismatch', 502)
        require(self.snapshot(cid)['revision'] == snap['revision'], 'intake_materials_changed', 409)
        self.save(cid, state=state, assessment=json.dumps(output['assessment'], ensure_ascii=False), error=None)

    def retry(self, cid, request_id):
        self.identity(cid)
        require(isinstance(request_id, str) and 0 < len(request_id) <= 100, 'invalid_retry', 400)
        with self.connect() as db:
            row = db.execute('SELECT state,preparation_id FROM er_intake_preparations WHERE intake_id=?', (cid,)).fetchone()
        require(row and row[0] == 'failed', 'review_retry_not_allowed', 409)
        if row[1]:
            result = self.service.call('GET', '/v1/preparations/' + row[1])
            if result['status'] == 'failed':
                self.service.call('POST', '/v1/preparations/' + row[1] + '/retry', {'requestId': request_id})
        self.save(cid, state='waiting_sources', error=None, next_check=0)
        return {'intakeId': cid, 'status': 'queued'}
