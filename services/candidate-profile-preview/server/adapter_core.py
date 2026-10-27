"""Read-only subset of canonical materials clients; see VENDORED.md."""
import datetime as dt

import http.client

import json

import os

import urllib.error

import urllib.parse

import urllib.request

from adapter_errors import Problem, require

class ServiceError(Problem):
    def __init__(self, code='materials_service_unavailable', status=503):
        self.code = code
        super().__init__(code, status)

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class ServiceHTTP:

    def __init__(self, base_url, key, *, timeout=30, opener=None):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or (parsed.path not in ('', '/')) or (parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1')) or (not isinstance(key, str)) or (not key) or any((ord(c) < 32 for c in key)):
            raise ValueError('Invalid materials service configuration')
        (self.base_url, self.key, self.timeout) = (base_url.rstrip('/'), key, timeout)
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def call(self, method, path, body=None):
        raw = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        return self._send(method, path, raw)

    def _send(self, method, path, raw, forwarded=None):
        if not path.startswith('/v1/') or '?' in path or '#' in path:
            raise ValueError('Invalid materials service path')
        request = urllib.request.Request(self.base_url + path, data=raw, method=method, headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json', **(forwarded or {})})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = response.read(24 * 1024 ** 2 + 1)
            if len(content) > 24 * 1024 ** 2:
                raise ServiceError('materials_service_response_limit', 502)
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except urllib.error.HTTPError as error:
            status = error.code if error.code in (400, 401, 403, 404, 409, 410, 413, 422, 429) else 503
            raise ServiceError('materials_service_rejected' if status != 503 else 'materials_service_unavailable', status) from None
        except (OSError, ValueError, UnicodeError, http.client.HTTPException):
            raise ServiceError() from None

def _part(value):
    require(isinstance(value, str) and 0 < len(value) <= 500 and not any(ord(c) < 32 for c in value),
            'invalid_material_identifier', 400)
    return urllib.parse.quote(value, safe='')

class SourceMedia:

    def __init__(self, http, *, enabled=True):
        self.http = http
        self.configured = bool(enabled)

    def access(self, binding_id, external_id):
        return self.http.call('POST', '/v1/materials/access', {'binding_id': binding_id, 'external_id': external_id})

class CoreClient:

    def __init__(self, http):
        self.http = http

    def materials(self, owner_kind, owner_id):
        require(owner_kind in ('interview', 'meeting', 'intake'), 'invalid_material_owner', 400)
        return self.http.call('GET', '/v1/owners/' + owner_kind + '/' + _part(owner_id) + '/materials')

    def access(self, asset_id):
        return self.http.call('GET', '/v1/assets/' + _part(asset_id) + '/access')

class CoreMaterialsCatalog:

    def __init__(self, core, source=None, outbox=None, *, connect=None, collect=True):
        (self.core, self.source, self.outbox) = (core, source, outbox)
        require(type(collect) is bool and (collect or outbox is None), 'invalid_material_catalog_configuration', 503)
        self.collect_enabled = collect
        self.connect = outbox.connect if outbox else connect
        require(self.connect is not None, 'materials_database_required', 503)

    def _check(self, access_check):
        if access_check:
            with self.connect() as db:
                access_check(db)

    def legacy_materials(self, owner_kind, meeting, *, access_check=None):
        require(self.outbox is None or (meeting.get('room') or {}).get('provider', 'daily') == 'daily', 'materials_source_provider_unavailable', 503)
        self._check(access_check)
        try:
            data = self.core.materials(owner_kind, meeting['id'])
        except ServiceError as error:
            if error.status != 404 or not self.outbox or (not self.outbox.pending(owner_kind, meeting['id'])):
                raise
            data = {'sources': [], 'assets': [], 'transcripts': [], 'channels': []}
        if not data.get('assets') and (meeting.get('room') or {}).get('captureNeverStarted'):
            channel = {'status': 'not_recorded', 'ids': []}
            return {'recordings': [], 'transcripts': [], 'video': dict(channel), 'transcription': dict(channel), 'status': 'not_recorded', 'catalogVersion': 1, 'checkedAt': dt.datetime.now(dt.timezone.utc).isoformat()}
        if not data.get('sources') and self.outbox and (self.outbox.delivery_failed(owner_kind, meeting['id']) is True):
            raise ServiceError('materials_delivery_retrying', 503)
        active_sources = [s for s in data.get('sources', []) if not s.get('revoked')]
        require(not data.get('sources') or active_sources, 'Материалы недоступны.', 410)
        (recordings, transcripts, transcript_variants, video_errors) = ([], [], [], [])
        assets = {asset['id']: asset for asset in data.get('assets', [])}
        for asset in data.get('assets', []):
            if asset['kind'] not in ('video', 'audio') or asset['state'] not in ('ready', 'available'):
                continue
            self._check(access_check)
            try:
                access = self.core.access(asset['id'])
                if access.get('storage') == 'remote':
                    require(self.source is not None, 'materials_owned_copy_required', 503)
                    resolved = self.source.access(access['binding_id'], access['external_id'])
                    access = {'id': asset['id'], 'storage': 'remote', 'url': resolved['url'], 'expires': resolved.get('expires')}
                url = urllib.parse.urlsplit(access.get('url', ''))
                require(access.get('storage') in ('owned', 'remote') and url.scheme == 'https' and url.hostname and (not url.username) and (not url.password), 'materials_service_invalid_access', 502)
                recordings.append({'id': asset['id'], 'url': access['url'], 'expires': access.get('expires'), 'storage': access['storage'], 'duration': asset.get('metadata', {}).get('duration'), 'partIndex': asset.get('metadata', {}).get('part_index'), 'kind': asset['kind'], 'copyStatus': 'ready' if access['storage'] == 'owned' else 'not_requested'})
            except Problem as error:
                if error.status < 500:
                    raise
                video_errors.append('Не удалось получить доступ к записи. Повторите попытку.')
        latest = {}
        versions = {v['id']: v for v in data.get('transcripts', [])}
        selections = data.get('transcript_selections', [])
        required = set(data.get('transcript_selection_required_recording_ids', []))
        required.update((s['recording_id'] for s in selections))
        required.update((a['id'] for a in data.get('assets', []) if a['kind'] in ('video', 'audio')))
        selected = {s['transcript_id']: s for s in selections if s['status'] == 'ready' and s.get('transcript_id')}
        for version in versions.values():
            if version['version'] > latest.get(version['asset_id'], {}).get('version', 0):
                latest[version['asset_id']] = version
        projected = {v['id']: v for v in latest.values()}
        projected.update({tid: versions[tid] for tid in selected if tid in versions})
        for version in projected.values():
            normalized = version.get('metadata', {}).get('normalized', {})
            projection = {'id': version['id'], 'assetId': version['asset_id'], 'revision': version['version'], 'text': normalized.get('vtt') or normalized.get('full_text', ''), 'fullText': normalized.get('full_text', ''), 'segments': version['segments'], 'warnings': normalized.get('warnings', []), 'storage': 'owned'}
            asset = assets.get(version['asset_id'], {})
            external_id = asset.get('external_id') or asset.get('provider_asset_id', '')
            metadata = version.get('metadata', {})
            if required:
                projection.update(origin=metadata.get('origin'), model=metadata.get('model_version'))
                if version['id'] in selected:
                    choice = selected[version['id']]
                    projection.update(recordingId=choice['recording_id'], selectionRevision=choice['revision'], generation=choice['generation'], sha256=version.get('content_sha256'))
                    transcripts.append(projection)
                else:
                    transcript_variants.append(projection)
            elif external_id.startswith('asr:'):
                transcript_variants.append({**projection, 'origin': metadata.get('origin'), 'model': metadata.get('model_version')})
            else:
                transcripts.append(projection)

        def channel(key, values):
            statuses = [row[key] for row in data.get('channels', []) if key in row]
            state = 'ready' if values else 'processing' if not statuses or 'processing' in statuses else 'error' if any((s in ('error', 'unsupported') for s in statuses)) else 'not_recorded' if all((s == 'not_recorded' for s in statuses)) else 'processing'
            return {'status': state, 'ids': [item['id'] for item in values]}
        (video, transcription) = (channel('video_status', recordings), channel('transcript_status', transcripts))
        if required:
            ready_recordings = {s['recording_id'] for s in selections if s['status'] == 'ready' and s.get('transcript_id') in versions}
            if any((s['status'] in ('failed', 'uncertain') or (s['status'] == 'ready' and s.get('transcript_id') not in versions) for s in selections)):
                state = 'error'
            else:
                state = 'ready' if required <= ready_recordings else 'processing'
            transcription.update(status=state)
            if state != 'ready':
                transcript_variants.extend(transcripts)
                transcripts = []
                transcription['ids'] = []
        if video_errors:
            video.update(status='ready' if recordings else 'error', error=video_errors[0])
        self._check(access_check)
        if data.get('sources'):
            current = self.core.materials(owner_kind, meeting['id'])
            old = {(s.get('binding_id', s.get('id')), s['generation']) for s in active_sources}
            fresh = {(s.get('binding_id', s.get('id')), s['generation']) for s in current.get('sources', []) if not s.get('revoked')}
            require(old <= fresh and transcript_selection_identity(data) == transcript_selection_identity(current), 'Доступ к материалам изменился.', 409)
        self._check(access_check)
        states = (video['status'], transcription['status'])
        indexes = [r.get('partIndex') for r in recordings]
        explicit_order = all((type(i) is int and i >= 0 for i in indexes)) and len(set(indexes)) == len(indexes)
        recordings.sort(key=lambda r: r['partIndex'] if explicit_order else r['id'])
        return {'recordings': recordings, 'transcripts': transcripts, **({'transcriptVariants': transcript_variants} if transcript_variants else {}), 'video': video, 'transcription': transcription, 'status': 'ready' if 'ready' in states else 'error' if 'error' in states else 'processing' if 'processing' in states else 'not_recorded', 'catalogVersion': 1, 'checkedAt': dt.datetime.now(dt.timezone.utc).isoformat()}

def transcript_selection_identity(data):
    """Provider-neutral identity; timestamps do not invalidate assessments."""
    fields = ('binding_id', 'generation', 'recording_id', 'request_id', 'revision', 'status', 'transcript_id', 'error_code')
    rows = data.get('transcript_selections', data.get('selections', []))
    return {'required': sorted(data.get('transcript_selection_required_recording_ids', [])),
            'selections': sorted([[row.get(key) for key in fields] for row in rows], key=lambda row: str(row[:3])),
            'status': data.get('status') if 'selections' in data else None,
            'recordings': sorted([[a['id'], a['state'], a.get('metadata', {})] for a in data.get('assets', [])
                                  if a['kind'] in ('video', 'audio')], key=lambda a: a[0])}
