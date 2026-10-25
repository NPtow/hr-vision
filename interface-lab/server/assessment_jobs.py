"""HR-owned durable intents and projections for the private Jev service.

This adapter knows authorizations/source identities. The isolated assessment
process receives sanitized transcript chunks, never accounts or database access.
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from domain import Problem
import selection
import interview_assessment
from interview_assistant import transcript_turns, search_chunks
from structured_model import NoRedirect, scan


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS as_bindings (
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT NOT NULL,
      vacancy_id TEXT, workspace_id TEXT, status TEXT NOT NULL,
      source_revision TEXT, recording_id TEXT, job_id TEXT, assessment TEXT,
      error TEXT, next_check REAL NOT NULL DEFAULT 0, updated REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS as_matches (
      id TEXT PRIMARY KEY,binding_id TEXT NOT NULL REFERENCES as_bindings(id),
      vacancy_id TEXT NOT NULL,workspace_id TEXT,status TEXT NOT NULL,
      source_revision TEXT,job_id TEXT,result TEXT,error TEXT,next_check REAL NOT NULL DEFAULT 0)''')


def enqueue(db, kind, owner_id, vacancy_id=None, workspace_id=None):
    # Small insert in the same transaction as the product event. No network or model.
    init(db)
    identity = kind + ':' + owner_id
    db.execute('''INSERT OR IGNORE INTO as_bindings
      (id,kind,owner_id,vacancy_id,workspace_id,status,updated) VALUES (?,?,?,?,?,'waiting',?)''',
      (identity, kind, owner_id, vacancy_id, workspace_id, time.time()))
    if vacancy_id:
        db.execute("INSERT OR IGNORE INTO as_matches (id,binding_id,vacancy_id,workspace_id,status) VALUES (?,?,?,?,'waiting')",
                   (identity + ':' + vacancy_id, identity, vacancy_id, workspace_id))


def read(db, kind, owner_id, revision, recording_id=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='as_bindings'").fetchone():
        return None
    row = db.execute('SELECT source_revision,status,assessment,error,recording_id FROM as_bindings WHERE id=?', (kind + ':' + owner_id,)).fetchone()
    if not row:
        return None
    if row[0] != revision:
        return {'assessment': None, 'status': 'pending'}
    if row[1] == 'failed':
        return {'assessment': None, 'status': 'failed', 'error': row[3]}
    if recording_id is not None and row[4] != recording_id:
        return {'assessment': None, 'status': 'pending'}
    result = json.loads(row[2]) if row[2] and row[1] == 'completed' else None
    return {'assessment': result, 'status': row[1], 'error': row[3]}


def read_candidate(db, candidate_id, recording_id=None, vacancy_id=None):
    value = read(db, 'candidate', candidate_id, candidate_revision(db, candidate_id), recording_id)
    return with_match(db, value, 'candidate:' + candidate_id, vacancy_id)


def candidate_revision(db, candidate_id):
    row = db.execute('SELECT id,status,completed_at,room FROM cp_interviews WHERE candidate_id=?', (candidate_id,)).fetchone()
    if not row or row[1] != 'completed':
        return None
    room = json.loads(row[3]) if row[3] else None
    identity = {key: room.get(key) for key in ('provider', 'id', 'name', 'meetingNumber', 'meetingSessionId', 'connectionId')} if isinstance(room, dict) else room
    # Resume/contact/vacancy edits must not recharge the same interview.
    return selection.digest([list(row[:3]), identity])


def read_intake(db, workspace_id, owner_id, fingerprint):
    row = db.execute('SELECT workspace_id FROM em_intake_candidates WHERE id=?', (owner_id,)).fetchone()
    if not row or row[0] != workspace_id:
        return None
    return read(db, 'intake', owner_id, fingerprint)


def with_match(db, value, binding_id, vacancy_id):
    if not value or not value.get('assessment') or not vacancy_id:
        return value
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='as_matches'").fetchone():
        return value
    row = db.execute('SELECT result,source_revision,status FROM as_matches WHERE binding_id=? AND vacancy_id=?', (binding_id, vacancy_id)).fetchone()
    if row:
        try:
            base = value['assessment'].get('provenance', {}).get('baseRevisionId', value['assessment']['revisionId'])
            revision = selection.digest([base, selection.vacancy(db, vacancy_id)['contentHash']])
            if revision == row[1] and row[2] == 'completed':
                return {**value, 'assessment': {**value['assessment'], 'vacancyMatch': json.loads(row[0])}}
        except Problem:
            pass
    return {**value, 'matchStatus': row[2] if row else None}


class Bridge:
    def __init__(self, portal, request=None):
        self.portal, self.connect = portal, portal.connect
        self.request = request or self.http
        with self.connect() as db:
            init(db)
            # Explicit production backfill: currently active public profiles only.
            for (cid,) in db.execute('SELECT DISTINCT candidate_id FROM cp_public_profiles WHERE revoked_at IS NULL').fetchall():
                enqueue(db, 'candidate', cid)

    @staticmethod
    def http(method, path, payload=None):
        token = os.environ.get('HR_ASSESSMENT_TOKEN', '')
        if not re.fullmatch('[a-f0-9]{64}', token):
            raise Problem('assessment_service_not_configured', 503)
        req = urllib.request.Request('http://127.0.0.1:8096' + path,
            data=selection.dump(payload).encode() if payload is not None else None,
            method=method, headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=12) as response:
                raw = response.read(1000001)
                if len(raw) > 1000000:
                    raise ValueError()
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except urllib.error.HTTPError as error:
            if error.code == 422:
                try:
                    code = json.loads(error.read(4096)).get('error', '')
                except (ValueError, AttributeError):
                    code = ''
                raise Problem(code if isinstance(code, str) and re.fullmatch('[a-z_0-9]{1,80}', code)
                              else 'invalid_assessment_input', 422) from None
            raise Problem('assessment_service_unavailable', 503) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise Problem('assessment_service_unavailable', 503) from None

    def active(self, db, row):
        if row['kind'] == 'candidate':
            cid = row['owner_id']
            public = db.execute('SELECT 1 FROM cp_public_profiles WHERE candidate_id=? AND revoked_at IS NULL', (cid,)).fetchone()
            application = db.execute('SELECT 1 FROM cp_applications WHERE candidate_id=? AND archived_at IS NULL', (cid,)).fetchone()
            cohort = db.execute('SELECT 1 FROM sl_cohort WHERE candidate_id=?', (cid,)).fetchone()
            if not (public or application or cohort):
                return None
            return candidate_revision(db, cid)
        found = db.execute('SELECT body FROM em_intake_candidates WHERE id=? AND workspace_id=? AND vacancy_id=?',
                           (row['owner_id'], row['workspace_id'], row['vacancy_id'])).fetchone()
        if not found or json.loads(found[0]).get('archived'):
            return None
        _, fingerprint, _ = interview_assessment.intake_binding(db, row['workspace_id'],
            {'source': 'intake', 'vacancyId': row['vacancy_id'], 'id': row['owner_id'], 'index': 0}, allow_missing_transcript=True)
        return fingerprint

    def snapshot(self, row):
        with self.connect() as db:
            revision = self.active(db, row)
            if not revision:
                raise Problem('waiting_for_interview', 409)
            if row['kind'] == 'intake':
                person = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (row['owner_id'],)).fetchone()[0])
                profile = person.get('preparedProfile', {})
                recording = profile['recordings'][0]
                transcript = profile['transcriptText']
                recording_id = str(recording.get('id') or '0')
                source_hash = interview_assessment.recording_fingerprint(profile, 0)
        if row['kind'] == 'candidate':
            cid = row['owner_id']
            def authorize(db):
                if self.active(db, row) != revision:
                    raise Problem('assessment_source_changed', 409)
            materials = self.portal.materials(cid, access_check=authorize)
            recordings, transcripts = materials.get('recordings', []), materials.get('transcripts', [])
            if len(recordings) != 1 or len(transcripts) != 1 or not transcripts[0].get('text'):
                raise Problem('waiting_for_transcript', 409)
            recording_id = recordings[0]['id']
            transcript = transcripts[0]['text']
            source_hash = selection.digest([revision, recording_id, hashlib.sha256(transcript.encode()).hexdigest()])
        # Use the same timing/parser as the existing source player. No CV/contacts
        # are sent; filenames and external source links never enter the model.
        chunks = search_chunks(transcript_turns(transcript))
        if not chunks:
            raise Problem('transcript_format_unsupported', 422)
        clean = []
        for chunk in chunks:
            content, _ = scan(chunk['text'])
            clean.append({'id': chunk['id'], 'text': content, 'speaker': chunk['speakerName'],
                          'startMs': chunk['startMs'], 'endMs': chunk['endMs']})
        return revision, recording_id, {'subject': row['id'], 'sourceHash': source_hash, 'chunks': clean}

    def tick(self):
        with self.connect() as db:
            cursor = db.execute('SELECT * FROM as_bindings WHERE next_check<=? ORDER BY next_check,updated LIMIT 1', (time.time(),))
            found = cursor.fetchone()
            if not found:
                return self.tick_match()
            row = dict(zip([d[0] for d in cursor.description], found))
            # Durable retry window; a crash may repeat HTTP but never inference.
            db.execute('UPDATE as_bindings SET next_check=? WHERE id=?', (time.time() + 60, row['id']))
        try:
            with self.connect() as db:
                current = self.active(db, row)
            if not current:
                if row['job_id'] and row['status'] not in ('completed', 'failed'):
                    self.request('DELETE', '/v1/jobs/' + row['job_id'])
                with self.connect() as db:
                    db.execute("UPDATE as_bindings SET status='waiting',assessment=NULL,error='waiting_for_interview',updated=? WHERE id=?", (time.time(), row['id']))
                return True
            if row['job_id'] and row['source_revision'] == current and row['status'] in ('queued', 'running'):
                result = self.request('GET', '/v1/jobs/' + row['job_id'])
                revision, recording_id = current, row['recording_id']
            else:
                revision, recording_id, payload = self.snapshot(row)
                result = self.request('POST', '/v1/jobs', payload)
            if not re.fullmatch('[a-f0-9]{64}', result.get('id', '')) or result.get('status') not in ('queued', 'running', 'completed', 'failed', 'cancelled', 'expired'):
                raise Problem('invalid_assessment_response', 502)
            assessment = result.get('assessment')
            if result['status'] == 'completed':
                interview_assessment._checked_assessment({k: assessment[k] for k in ('revisionId', 'criteria', 'provenance')})
                if assessment['revisionId'] != result['id']:
                    raise Problem('invalid_assessment_response', 502)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if self.active(db, row) != revision:
                    raise Problem('assessment_source_changed', 409)
                if result['status'] == 'completed' and row['kind'] == 'intake':
                    assessment = {**assessment, 'revisionId': selection.digest([result['id'], revision]),
                                  'provenance': {**assessment['provenance'], 'baseRevisionId': result['id']}}
                    binding = interview_assessment.source_key('intake', {'vacancyId': row['vacancy_id'], 'id': row['owner_id'], 'index': 0})
                    interview_assessment.import_assessment(db, row['workspace_id'], binding, revision,
                        {k: assessment[k] for k in ('revisionId', 'criteria', 'provenance')})
                db.execute('''UPDATE as_bindings SET status=?,source_revision=?,recording_id=?,job_id=?,assessment=?,error=?,next_check=?,updated=? WHERE id=?''',
                    (result['status'], revision, recording_id, result['id'], selection.dump(assessment) if assessment else None,
                     result.get('error'), time.time() + (600 if result['status'] in ('completed', 'failed', 'cancelled', 'expired') else 10), time.time(), row['id']))
        except Exception as error:
            # Do not log exceptions that may embed transcript/provider responses.
            code = error.message if isinstance(error, Problem) and re.fullmatch('[a-z_0-9]{1,80}', error.message) else 'assessment_sync_failed'
            with self.connect() as db:
                if isinstance(error, Problem) and error.status in (400, 413, 422):
                    db.execute("UPDATE as_bindings SET status='failed',assessment=NULL,source_revision=?,error=?,next_check=?,updated=? WHERE id=?",
                               (current, code, time.time() + 600, time.time(), row['id']))
                else:
                    db.execute('UPDATE as_bindings SET error=?,next_check=?,updated=? WHERE id=?', (code, time.time() + 60, time.time(), row['id']))
        return True

    def tick_match(self):
        with self.connect() as db:
            cursor = db.execute('SELECT * FROM as_matches WHERE next_check<=? ORDER BY next_check LIMIT 1', (time.time(),))
            found = cursor.fetchone()
            if not found:
                return False
            row = dict(zip([d[0] for d in cursor.description], found))
            db.execute('UPDATE as_matches SET next_check=? WHERE id=?', (time.time() + 60, row['id']))
        try:
            with self.connect() as db:
                base = db.execute('SELECT job_id,status,assessment,source_revision,kind,owner_id,vacancy_id,workspace_id FROM as_bindings WHERE id=?', (row['binding_id'],)).fetchone()
                if not base or base[1] != 'completed':
                    return True
                binding = dict(zip(('job_id','status','assessment','source_revision','kind','owner_id','vacancy_id','workspace_id'), base))
                if self.active(db, binding) != base[3]:
                    return True
                v = selection.vacancy(db, row['vacancy_id'])
                revision = selection.digest([base[0], v['contentHash']])
                fields = [v.get(k) for k in ('title', 'description', 'problem', 'expectations', 'details') if v.get(k)]
                text = '\n'.join(x if isinstance(x, str) else selection.dump(x) for x in fields)
                if len(text) > 30000:
                    raise Problem('vacancy_too_large', 422)
                requirements = []
                for start in range(0, len(text), 1500):
                    clean, _ = scan(text[start:start + 1500])
                    requirements.append({'id': 'r' + str(len(requirements)), 'text': clean})
                payload = {'baseJobId': base[0], 'vacancyId': v['id'], 'vacancyHash': v['contentHash'], 'requirements': requirements}
            if row['job_id'] and row['source_revision'] == revision:
                result = self.request('GET', '/v1/jobs/' + row['job_id'])
            else:
                result = self.request('POST', '/v1/matches', payload)
            status = result.get('status')
            if status not in ('queued','running','completed','failed','cancelled','expired'):
                raise Problem('invalid_match_response', 502)
            matched = result.get('assessment')
            if status == 'completed' and (not isinstance(matched, dict) or matched.get('baseRevisionId') != base[0]
                    or matched.get('vacancyId') != v['id'] or matched.get('vacancyHash') != v['contentHash']):
                raise Problem('invalid_match_response', 502)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                latest = db.execute('SELECT job_id,status FROM as_bindings WHERE id=?', (row['binding_id'],)).fetchone()
                if tuple(latest) != (base[0], 'completed') or self.active(db, binding) != base[3] or selection.vacancy(db, v['id'])['contentHash'] != v['contentHash']:
                    raise Problem('match_source_changed', 409)
                db.execute('UPDATE as_matches SET status=?,source_revision=?,job_id=?,result=?,error=?,next_check=? WHERE id=?',
                    (status, revision, result['id'], selection.dump(matched) if matched else None, result.get('error'),
                     time.time() + (600 if status in ('completed','failed','cancelled','expired') else 10), row['id']))
        except Exception:
            with self.connect() as db:
                db.execute("UPDATE as_matches SET error='match_sync_failed',next_check=? WHERE id=?", (time.time() + 60, row['id']))
        return True


def start_worker(portal):
    if os.environ.get('HR_ASSESSMENT_ENABLED') != '1':
        return
    bridge = Bridge(portal)
    def loop():
        while True:
            try:
                if not bridge.tick():
                    time.sleep(3)
            except Exception:
                time.sleep(5)
    threading.Thread(target=loop, name='assessment-sync', daemon=True).start()
