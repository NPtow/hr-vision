"""Read-only workbench projections. No scoring, collection or delivery commands.

The list reports saved state. Opening a person verifies the current materials
through the existing Core API. A closed room is not evidence of a conversation.
"""
import os
import time
from urllib.parse import quote

from domain import Problem
import assessment_core_bridge


def tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def binding(db, candidate_id):
    if 'as_bindings' not in tables(db):
        return None
    return db.execute('SELECT status,job_id,error,assessment FROM as_bindings WHERE id=?',
                      ('candidate:' + candidate_id,)).fetchone()


def overview(db, candidate, vacancy_id=None):
    legacy = candidate.get('assessment')
    assessment = {'state': 'legacy' if legacy else 'not_started',
                  'origin': 'engine-v1' if legacy else None,
                  'revisionId': legacy['id'] if legacy else None}
    if legacy and legacy['stale']:
        assessment['state'] = 'stale'
    row = binding(db, candidate['id'])
    if row:
        state = {'waiting': 'waiting_sources', 'queued': 'queued', 'running': 'processing',
                 'completed': 'saved', 'failed': 'error', 'cancelled': 'cancelled',
                 'expired': 'expired'}.get(row[0], 'unknown')
        assessment = {'state': state, 'origin': 'assessment-service', 'revisionId': row[1]}
        # A later request owns the visible state; don't substitute an older
        # engine-v1 profile when this computation is pending or failed.
    match = {'state': 'not_compared'}
    if not vacancy_id:
        match['state'] = 'no_vacancy'
    elif legacy:
        match['state'] = {'not_matched': 'not_compared'}.get(legacy['recommendation'], legacy['recommendation'])
        match['origin'] = 'engine-v1'
        if assessment['origin'] != 'engine-v1':
            match = {'state': 'not_compared'}
    if vacancy_id and row and 'as_matches' in tables(db):
        comparison = db.execute('SELECT status FROM as_matches WHERE binding_id=? AND vacancy_id=?',
                                ('candidate:' + candidate['id'], vacancy_id)).fetchone()
        if comparison:
            match = {'state': {'waiting': 'waiting_assessment', 'queued': 'processing',
                     'running': 'processing', 'completed': 'comparison_saved', 'failed': 'failed_processing',
                     'cancelled': 'cancelled', 'expired': 'stale'}.get(comparison[0], 'unknown'),
                     'origin': 'assessment-service'}
    if assessment_core_bridge.enabled():
        assessment = assessment_core_bridge.overview(db, candidate['id'])
        match = {'state': 'not_compared' if vacancy_id else 'no_vacancy'}
    return {'interview': {'state': {None: 'not_scheduled', 'booked': 'scheduled',
                'live': 'open', 'completed': 'closed'}.get(candidate['interviewStatus'], 'unknown')},
            'resume': {'state': 'available' if candidate['resumeAvailable'] else 'missing'},
            'assessment': assessment, 'match': match}


def core_http():
    from core_source_client import ServiceHTTP
    if os.environ.get('HR_CORE_CATALOG_ENABLED') != '1':
        return None
    try:
        return ServiceHTTP(os.environ['HR_MATERIALS_CORE_URL'], os.environ['HR_MATERIALS_CORE_KEY'], timeout=3)
    except (KeyError, ValueError):
        return None


def materials(candidate_id, http=None):
    """Metadata only, from the canonical dossier and primary-text selection.

    Never mint download URLs, call a provider, collect files or select another
    transcript here. Failure/absence remain distinct from confirmed zero files.
    """
    http = http or core_http()
    if http is None:
        return {'state': 'unavailable', 'recordings': None, 'transcripts': None, 'items': []}
    try:
        dossier = http.call('GET', '/v1/candidates/' + quote(candidate_id, safe='') + '/dossier')
        if dossier.get('candidate_id') != candidate_id:
            raise Problem('materials_identity_mismatch', 502)
        items = []
        recording_ids, transcript_ids = set(), set()
        for kind, key in (('interview', 'interviews'), ('meeting', 'meetings')):
            for owner in dossier.get(key, []):
                data = owner.get('materials', {})
                primary = http.call('GET', '/v1/owners/' + kind + '/' + quote(owner['id'], safe='') + '/primary-transcripts')
                if primary.get('owner_id') != owner['id'] or primary.get('owner_kind') != kind:
                    raise Problem('materials_identity_mismatch', 502)
                videos = {a['id'] for a in data.get('assets', []) if a.get('kind') == 'video'
                          and a.get('state') in ('ready', 'available')}
                selected = {t['id'] for t in primary.get('transcripts', [])} if primary.get('status') == 'ready' else set()
                recording_ids.update(videos)
                transcript_ids.update(selected)
                items.append({'id': owner['id'], 'kind': kind, 'recordings': len(videos),
                    'transcripts': len(selected), 'transcriptState': primary.get('status', 'unknown'),
                    'captureStates': [c.get('video_status', 'unknown') for c in data.get('channels', [])]})
        # A future/unrecorded meeting doesn't make an existing transcript
        # incomplete. Only owners with recorded or pending material participate.
        states = [item['transcriptState'] for item in items if item['recordings'] or item['transcripts']
                  or item['transcriptState'] in ('processing', 'error')
                  or any(s in ('processing', 'error') for s in item['captureStates'])]
        capture = [state for item in items for state in item.pop('captureStates')]
        state = ('error' if 'error' in states else 'processing' if 'processing' in states else
                 'ready' if states and all(s == 'ready' for s in states) else
                 'needs_transcript' if recording_ids else 'error' if any(s in ('error', 'unsupported') for s in capture) else
                 'processing' if 'processing' in capture else 'missing' if not items or capture and all(s == 'not_recorded' for s in capture) else 'unknown')
        return {'state': state, 'recordings': len(recording_ids), 'transcripts': len(transcript_ids), 'items': items}
    except (Problem, OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'state': 'unavailable', 'recordings': None, 'transcripts': None, 'items': []}


def verified_assessment(db, candidate_id, vacancy_id=None):
    """Reuse the public profile's current-revision read; never enqueue from GET."""
    import assessment_jobs
    stored = binding(db, candidate_id)
    if stored is None:
        return {'state': 'not_started', 'result': None}
    try:
        value = assessment_jobs.read_candidate(db, candidate_id, vacancy_id=vacancy_id)
    except (Problem, OSError, ValueError, KeyError):
        return {'state': 'unavailable', 'result': None, 'match': {'state': 'unavailable'}}
    if value is None:
        return {'state': 'unknown', 'result': None, 'match': {'state': 'unknown'}}
    result = value.get('assessment')
    if result:
        criteria = result.get('criteria', [])
        state = 'ready' if any(c.get('state') == 'supported' for c in criteria) else 'insufficient_data'
        match_state = ('no_vacancy' if not vacancy_id else 'compared' if result.get('vacancyMatch') else
            {'completed': 'stale', 'waiting': 'waiting_assessment', 'queued': 'processing',
             'running': 'processing', 'failed': 'failed_processing', 'cancelled': 'cancelled',
             'expired': 'stale'}.get(value.get('matchStatus'), 'not_compared'))
        return {'state': state, 'result': result, 'match': {'state': match_state}}
    if value.get('status') == 'pending':
        state = 'stale' if stored[3] else {
            'queued': 'queued', 'running': 'processing', 'failed': 'error'
        }.get(stored[0], 'waiting_sources')
        return {'state': state, 'result': None, 'match': {'state': 'no_vacancy' if not vacancy_id else
                'stale' if state == 'stale' else 'waiting_assessment'}}
    return {'state': {'waiting': 'waiting_sources', 'queued': 'queued',
                     'running': 'processing', 'failed': 'error', 'cancelled': 'cancelled',
                     'expired': 'expired'}.get(value.get('status'), 'unknown'), 'result': None,
            'match': {'state': 'no_vacancy' if not vacancy_id else 'waiting_assessment'}}


def init_evidence(db):
    db.execute('''CREATE TABLE IF NOT EXISTS sl_evidence (
        candidate_id TEXT PRIMARY KEY,source_revision TEXT NOT NULL,corpus_revision TEXT,
        body TEXT NOT NULL,checked REAL NOT NULL)''')


EVIDENCE_VERSION = 'hr-interview-readiness-v1'
EVIDENCE_MAX_AGE_SECONDS = 120


def unavailable_evidence(*, checked=None, error='materials_unverified'):
    return {'state': 'unavailable', 'recordingCount': None, 'transcriptCount': None,
            'allPartsReady': False, 'contractVersion': EVIDENCE_VERSION,
            'materialsRevision': None, 'checkedAt': checked, 'errorCode': error}


def invalidate_evidence(db, cid, *, error='materials_unavailable'):
    """Replace previously ready evidence after a failed canonical verification.

    Caller must authorize first; no raw exception/source text is accepted.
    """
    # Machine codes are deliberately bounded; never persist exception strings.
    if error not in ('materials_unavailable', 'materials_changed', 'materials_configuration_unavailable'):
        error = 'materials_unavailable'
    return save_evidence(db, cid, {'manifest': {}, 'errorCode': error})


def save_evidence(db, cid, snapshot):
    """Durable metadata projection refreshed by canonical source reads/scan."""
    import json
    import selection
    init_evidence(db)
    conversations = snapshot.get('manifest', {}).get('conversations', [])
    interviews = [i for i in conversations if i.get('owner_kind') == 'interview']
    relevant = [i for i in interviews if i.get('recordings') or i.get('status') not in ('not_conducted', 'not_recorded')]
    recordings = [r for i in relevant for r in i.get('recordings', [])]
    videos = [r for r in recordings if r.get('kind') == 'video']
    selected = [r for r in recordings if (r.get('selection') or {}).get('status') == 'ready'
                and r.get('transcript_id')]
    ready = bool(videos and relevant and all(i.get('status') == 'ready'
        and i.get('lifecycle_status') == 'completed' and i.get('primary_status') == 'ready' for i in relevant)
        and len(selected) == len(recordings))
    checked = time.time()
    value = {'state': 'ready' if ready else 'incomplete' if recordings else 'processing' if relevant else 'missing',
             'recordingCount': len(videos), 'transcriptCount': len(selected), 'allPartsReady': ready,
             'contractVersion': EVIDENCE_VERSION, 'materialsRevision': snapshot.get('revision'),
             'checkedAt': checked,
             'items': [{'ownerId': item.get('owner_id'), 'state': item.get('status'),
                        'lifecycleStatus': item.get('lifecycle_status'),
                        'primaryStatus': item.get('primary_status'),
                        'parts': [{'recordingId': part.get('recording_id'), 'sourceId': part.get('source_id'),
                                   'kind': part.get('kind'), 'selection': part.get('selection'),
                                   'transcriptId': part.get('transcript_id'),
                                   'contentSha256': part.get('content_sha256')}
                                  for part in item.get('recordings', [])]}
                       for item in interviews]}
    if 'conversations' not in snapshot.get('manifest', {}):
        value = unavailable_evidence(checked=checked, error=snapshot.get('errorCode', 'materials_unavailable'))
    # Standalone bridge consumers can use a minimal identity directory.
    # Their successful assessment must not require the portal's optional schema.
    if not {'cp_interviews', 'cp_resumes', 'cp_intake'} <= tables(db):
        return value
    p = selection.person(db, cid)
    db.execute('INSERT INTO sl_evidence VALUES (?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET source_revision=excluded.source_revision,corpus_revision=excluded.corpus_revision,body=excluded.body,checked=excluded.checked',
               (cid, p['sourceRevision'], snapshot.get('revision'), json.dumps(value), checked))
    return value


def evidence(db, candidate):
    import json
    init_evidence(db)
    row = db.execute('SELECT source_revision,body,checked,corpus_revision FROM sl_evidence WHERE candidate_id=?', (candidate['id'],)).fetchone()
    if row and row[0] == candidate['sourceRevision']:
        if not 0 <= time.time() - row[2] <= EVIDENCE_MAX_AGE_SECONDS:
            return unavailable_evidence(checked=row[2], error='materials_verification_expired')
        try:
            value = json.loads(row[1])
            if not isinstance(value, dict):
                raise ValueError()
            return {**value, 'checkedAt': row[2], 'materialsRevision': row[3],
                    'contractVersion': value.get('contractVersion', EVIDENCE_VERSION)}
        except (ValueError, TypeError):
            pass
    return unavailable_evidence()
