"""Read-only workbench projections. No scoring, collection or delivery commands.

The list reports saved state. Opening a person verifies the current materials
through the existing Core API. A closed room is not evidence of a conversation.
"""
import os
from urllib.parse import quote

from domain import Problem


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
