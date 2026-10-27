"""Read-only HR interview viewer over the existing canonical media projection.

No register_owner, collect, ASR, assessment or delivery command is available.
Candidate/owner membership and normal HR authorization fence every slow read.
Signed playback links are returned only to the authorized caller, never logged
or persisted by this adapter. A new GET reissues expiring links normally.
"""
import os
from urllib.parse import quote

from core_source_client import CoreClient, CoreMaterialsCatalog, ServiceHTTP, SourceMedia
from domain import Problem, require


def configured(connect, authorize):
    try:
        core = CoreClient(ServiceHTTP(os.environ['HR_MATERIALS_CORE_URL'],
                                      os.environ['HR_MATERIALS_CORE_KEY'], timeout=15))
        source = None
        if os.environ.get('HR_MATERIALS_SOURCE_URL') and os.environ.get('HR_MATERIALS_SOURCE_KEY'):
            source = SourceMedia(ServiceHTTP(os.environ['HR_MATERIALS_SOURCE_URL'],
                                             os.environ['HR_MATERIALS_SOURCE_KEY'], timeout=15))
        return Reader(core, connect, authorize, source=source)
    except (KeyError, ValueError):
        raise Problem('interview_materials_configuration_unavailable', 503) from None


def _indexed(values):
    require(isinstance(values, list), 'interview_materials_invalid_inventory', 502)
    result = {}
    for value in values:
        require(isinstance(value, dict) and isinstance(value.get('id'), str) and value['id'] and
                value['id'] not in result, 'interview_materials_invalid_inventory', 502)
        result[value['id']] = value
    return result


def inventory(owner):
    """Only public identity/state metadata; no room, raw_ref or source secrets."""
    data = owner.get('materials')
    require(isinstance(data, dict) and data.get('owner_kind') == 'interview' and
            data.get('owner_id') == owner['id'], 'interview_materials_owner_mismatch', 502)
    assets = _indexed(data.get('assets', []))
    selections = {s['recording_id']: s for s in data.get('transcript_selections', [])}
    revoked = any(s.get('revoked') for s in data.get('sources', []))
    parts = []
    for asset in assets.values():
        if asset.get('kind') not in ('audio', 'video'):
            continue
        selection = selections.get(asset['id']) or {}
        state = 'revoked' if revoked else selection.get('status', 'missing')
        parts.append({'id': asset['id'], 'recordingId': asset['id'], 'kind': asset['kind'],
            'state': 'revoked' if revoked else asset.get('state', 'unknown'),
            'partIndex': asset.get('metadata', {}).get('part_index'),
            'duration': asset.get('metadata', {}).get('duration'),
            'transcriptStatus': state, 'transcriptId': selection.get('transcript_id') if state == 'ready' else None,
            'selectionRevision': selection.get('revision'), 'generation': selection.get('generation')})
    # Preserve declared chronological part indexes. Unknown order retains the
    # canonical inventory sequence rather than inventing chronology from IDs.
    indexes = [p['partIndex'] for p in parts]
    if all(type(i) is int and i >= 0 for i in indexes) and len(set(indexes)) == len(indexes):
        parts.sort(key=lambda p: p['partIndex'])
    channels = data.get('channels', [])
    def status(field):
        states = [c.get(field, 'unknown') for c in channels]
        if revoked:
            return 'revoked'
        if not states:
            return 'missing'
        return ('error' if any(s in ('error', 'unsupported') for s in states) else
                'processing' if 'processing' in states else 'not_recorded' if all(s == 'not_recorded' for s in states)
                else 'ready' if all(s == 'ready' for s in states) else 'unknown')
    video = status('video_status')
    transcript = ('revoked' if revoked else 'error' if any(p['transcriptStatus'] in ('failed', 'uncertain') for p in parts)
                  else 'ready' if parts and all(p['transcriptStatus'] == 'ready' for p in parts)
                  else 'processing' if parts else status('transcript_status'))
    return {'id': owner['id'], 'status': owner.get('status', 'unknown'),
            'createdAt': owner.get('created_at'), 'completedAt': owner.get('completed_at'),
            'videoStatus': video, 'transcriptStatus': transcript, 'recordingCount': len(parts), 'parts': parts}


class Reader:
    def __init__(self, core, connect, authorize, *, source=None):
        self.core, self.connect, self.authorize = core, connect, authorize
        self.source = source

    def _dossier(self, cid):
        require(self.authorize(cid) is not False, 'candidate_access_denied', 403)
        value = self.core.http.call('GET', '/v1/candidates/' + quote(cid, safe='') + '/dossier')
        require(isinstance(value, dict) and value.get('candidate_id') == cid,
                'interview_candidate_identity_mismatch', 502)
        owners = _indexed(value.get('interviews'))
        require(self.authorize(cid) is not False, 'candidate_access_denied', 403)
        return owners

    def list(self, cid):
        owners = self._dossier(cid)
        return {'candidateId': cid, 'state': 'ready' if owners else 'missing',
                'interviews': [inventory(owner) for owner in owners.values()]}

    def materials(self, cid, iid):
        owner = self._dossier(cid).get(iid)
        require(owner is not None, 'interview_not_found_for_candidate', 404)
        before = inventory(owner)
        require(before['status'] == 'completed', 'interview_not_completed', 409)
        require(before['videoStatus'] != 'revoked', 'interview_materials_revoked', 410)
        def access(_db):
            require(self.authorize(cid) is not False, 'candidate_access_denied', 403)
        core = self.core
        allowed_assets = set()
        class ScopedCore:
            def materials(_self, kind, identity):
                require(kind == 'interview' and identity == iid, 'interview_scope_mismatch', 403)
                data = core.materials(kind, identity)
                require(data.get('owner_kind') == kind and data.get('owner_id') == identity,
                        'interview_materials_owner_mismatch', 502)
                sources = _indexed(data.get('sources', []))
                require(not any(s.get('revoked') for s in sources.values()), 'interview_materials_revoked', 410)
                assets = _indexed(data.get('assets', []))
                require(all(a.get('source_id') in sources for a in assets.values()),
                        'interview_materials_source_mismatch', 502)
                allowed_assets.update(a['id'] for a in assets.values() if a.get('kind') in ('audio', 'video'))
                return data

            def access(_self, asset_id):
                require(asset_id in allowed_assets, 'interview_recording_scope_mismatch', 403)
                return core.access(asset_id)
        catalog = CoreMaterialsCatalog(ScopedCore(), self.source, connect=self.connect, collect=False)
        result = catalog.legacy_materials('interview', {'id': iid}, access_check=access)
        # The catalogue already fences source generations/revocations/primary
        # selections around link issuance. Recheck candidate membership as well.
        def final_check():
            current = self._dossier(cid).get(iid)
            require(current is not None and current.get('status') == 'completed',
                    'interview_access_changed', 409)
            current_inventory = inventory(current)
            require(current_inventory == before, 'interview_materials_changed', 409)
            require(not any(s.get('revoked') for s in current['materials'].get('sources', [])),
                    'interview_materials_revoked', 410)
        final_check()
        if not before['parts'] and before['videoStatus'] == 'missing':
            result.update(video={'status': 'missing', 'ids': []},
                          transcription={'status': 'missing', 'ids': []}, status='missing')
        # Explicit linkage lets the UI show every missing/error part while the
        # existing player receives only accessible media and selected text.
        playable = {r['id'] for r in result['recordings']}
        exact = {p['id']: p['transcriptId'] for p in before['parts'] if p['transcriptStatus'] == 'ready'}
        selected = {t.get('recordingId'): t for t in result['transcripts'] + result.pop('transcriptVariants', [])
                    if t.get('recordingId') in exact and t['id'] == exact[t['recordingId']]}
        parts = [{**p, 'mediaAvailable': p['id'] in playable,
                  'selectedTranscriptAvailable': p['id'] in selected} for p in before['parts']]
        return {'candidateId': cid, 'interviewId': iid, **result, 'parts': parts,
                'partTranscripts': list(selected.values())}
