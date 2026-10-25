"""Typed portal clients. Provider credentials and raw provider paths never cross here."""
import datetime as dt
import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from domain import Problem, require


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
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or (parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'))
                or not isinstance(key, str) or not key or any(ord(c) < 32 for c in key)):
            raise ValueError('Invalid materials service configuration')
        self.base_url, self.key, self.timeout = base_url.rstrip('/'), key, timeout
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def call(self, method, path, body=None):
        raw = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        return self._send(method, path, raw)

    def forward_webhook(self, raw, headers):
        require(isinstance(raw, bytes) and 0 < len(raw) <= 256 * 1024, 'Invalid webhook.', 400)
        incoming = {key.lower(): value for key, value in headers.items()}
        forwarded = {}
        for key, maximum in (('x-webhook-timestamp', 16), ('x-webhook-signature', 256)):
            value = incoming.get(key)
            require(isinstance(value, str) and 0 < len(value) <= maximum
                    and all(32 <= ord(c) < 127 for c in value), 'Invalid webhook headers.', 400)
            forwarded[key] = value
        # Pass the exact original bytes and signature values. Source verifies HMAC.
        # Browser cookies/authorization and caller-selected destinations never pass.
        return self._send('POST', '/v1/webhooks/daily', raw, forwarded)

    def _send(self, method, path, raw, forwarded=None):
        if not path.startswith('/v1/') or '?' in path or '#' in path:
            raise ValueError('Invalid materials service path')
        request = urllib.request.Request(self.base_url + path, data=raw, method=method,
            headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json', **(forwarded or {})})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = response.read(24 * 1024**2 + 1)
            if len(content) > 24 * 1024**2:
                raise ServiceError('materials_service_response_limit', 502)
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except urllib.error.HTTPError as error:
            # Never echo service body, URL, credentials, submitted text or PII.
            status = error.code if error.code in (400, 401, 403, 404, 409, 410, 413, 422, 429) else 503
            raise ServiceError('materials_service_rejected' if status != 503 else 'materials_service_unavailable', status) from None
        except (OSError, ValueError, UnicodeError, http.client.HTTPException):
            raise ServiceError() from None


def _part(value):
    require(isinstance(value, str) and 0 < len(value) <= 500 and not any(ord(c) < 32 for c in value),
            'invalid_material_identifier', 400)
    return urllib.parse.quote(value, safe='')


class SourceMedia:
    """Source facade deliberately provides no arbitrary provider request method."""
    def __init__(self, http, *, enabled=True):
        self.http = http
        self.configured = bool(enabled)

    def capabilities(self):
        return self.http.call('GET', '/v1/capabilities')

    def room(self, meeting_id, *, recordable=True, screenshare=False):
        require(self.configured, 'Видеосервис ещё не подключён.', 503)
        return self.http.call('POST', '/v1/calls/room', {'meeting_id': meeting_id,
            'recordable': recordable, 'screenshare': screenshare})

    def token(self, room, properties):
        require(self.configured, 'Видеосервис ещё не подключён.', 503)
        return self.http.call('POST', '/v1/calls/token', {'room': room, 'properties': properties})

    def start_capture(self, room, recording_instance_id, *, start_recording, start_transcription):
        require(self.configured, 'Видеосервис ещё не подключён.', 503)
        return self.http.call('POST', '/v1/calls/start', {'room': room,
            'recording_instance_id': recording_instance_id, 'start_recording': start_recording,
            'start_transcription': start_transcription})

    def finish(self, room):
        result = self.http.call('POST', '/v1/calls/finish', {'room': room})
        require(result.get('acknowledged') is True, 'Видеосервис ещё завершает звонок. Повторите попытку.', 502)
        return result

    def materials(self, room, known=None, *, include_metadata=False):
        return self.http.call('POST', '/v1/calls/materials', {'room': room, 'known': known or {},
            'include_metadata': include_metadata})

    def enqueue(self, binding_id, generation):
        return self.http.call('POST', '/v1/jobs', {'binding_id': binding_id, 'generation': generation})

    def access(self, binding_id, external_id):
        return self.http.call('POST', '/v1/materials/access',
            {'binding_id': binding_id, 'external_id': external_id})

    def forward_webhook(self, raw, headers):
        result = self.http.forward_webhook(raw, headers)
        require(result.get('accepted') is True, 'materials_webhook_unacknowledged', 503)
        return result


def meeting_token(media, room, properties):
    """Old Daily fixtures remain supported while new mode has a typed boundary."""
    if isinstance(media, SourceMedia):
        return media.token(room, properties)
    return media.request('/meeting-tokens', {'properties': properties})


class CoreClient:
    def __init__(self, http):
        self.http = http

    def bind(self, payload):
        return self.http.call('POST', '/v1/bindings', payload)

    def materials(self, owner_kind, owner_id):
        require(owner_kind in ('interview', 'meeting'), 'invalid_material_owner', 400)
        return self.http.call('GET', '/v1/owners/' + owner_kind + '/' + _part(owner_id) + '/materials')

    def access(self, asset_id):
        return self.http.call('GET', '/v1/assets/' + _part(asset_id) + '/access')

    def collect(self, owner_kind, owner_id, storage_policy=None):
        require(owner_kind in ('interview', 'meeting'), 'invalid_material_owner', 400)
        return self.http.call('POST', '/v1/owners/' + owner_kind + '/' + _part(owner_id) + '/collect',
            {} if storage_policy is None else {'storage_policy': storage_policy})


class CoreMaterialsCatalog:
    def __init__(self, core, source=None, outbox=None, *, connect=None):
        self.core, self.source, self.outbox = core, source, outbox
        self.connect = outbox.connect if outbox else connect
        require(self.connect is not None, 'materials_database_required', 503)

    def register_owner(self, owner_kind, meeting, storage_policy=None):
        if self.outbox is None:
            return self.core.collect(owner_kind, meeting['id'], storage_policy)
        require((meeting.get('room') or {}).get('provider', 'daily') == 'daily',
                'materials_source_provider_unavailable', 503)
        require(storage_policy in (None, 'remote', 'owned'), 'Выберите способ хранения записи.', 400)
        with self.outbox.connect() as db:
            return self.outbox.record(db, owner_kind, meeting['id'], meeting.get('room') or {}, storage_policy)

    def _check(self, access_check):
        if access_check:
            with self.connect() as db:
                access_check(db)

    def legacy_materials(self, owner_kind, meeting, *, access_check=None):
        require(self.outbox is None or (meeting.get('room') or {}).get('provider', 'daily') == 'daily',
                'materials_source_provider_unavailable', 503)
        self._check(access_check)
        if (meeting.get('room') or {}).get('captureNeverStarted'):
            channel = {'status': 'not_recorded', 'ids': []}
            return {'recordings': [], 'transcripts': [], 'video': dict(channel), 'transcription': dict(channel),
                'status': 'not_recorded', 'catalogVersion': 1, 'checkedAt': dt.datetime.now(dt.timezone.utc).isoformat()}
        try:
            data = self.core.materials(owner_kind, meeting['id'])
        except ServiceError as error:
            # A newly committed outbox may not have created the binding yet.
            if error.status != 404 or not self.outbox or not self.outbox.pending(owner_kind, meeting['id']):
                raise
            data = {'sources': [], 'assets': [], 'transcripts': [], 'channels': []}
        if not data.get('sources') and self.outbox and self.outbox.delivery_failed(owner_kind, meeting['id']) is True:
            raise ServiceError('materials_delivery_retrying', 503)
        active_sources = [s for s in data.get('sources', []) if not s.get('revoked')]
        require(not data.get('sources') or active_sources, 'Материалы недоступны.', 410)
        recordings, transcripts, video_errors = [], [], []
        for asset in data.get('assets', []):
            if asset['kind'] != 'video' or asset['state'] not in ('ready', 'available'):
                continue
            self._check(access_check)
            try:
                access = self.core.access(asset['id'])
                if access.get('storage') == 'remote':
                    require(self.source is not None, 'materials_owned_copy_required', 503)
                    resolved = self.source.access(access['binding_id'], access['external_id'])
                    access = {'id': asset['id'], 'storage': 'remote', 'url': resolved['url'], 'expires': resolved.get('expires')}
                url = urllib.parse.urlsplit(access.get('url', ''))
                require(access.get('storage') in ('owned', 'remote') and url.scheme == 'https' and url.hostname
                        and not url.username and not url.password, 'materials_service_invalid_access', 502)
                recordings.append({'id': asset['id'], 'url': access['url'], 'expires': access.get('expires'),
                    'storage': access['storage'], 'duration': asset.get('metadata', {}).get('duration'),
                    'copyStatus': 'ready' if access['storage'] == 'owned' else 'not_requested'})
            except Problem as error:
                if error.status < 500:
                    raise
                video_errors.append('Не удалось получить доступ к записи. Повторите попытку.')
        latest = {}
        for version in data.get('transcripts', []):
            if version['version'] > latest.get(version['asset_id'], {}).get('version', 0):
                latest[version['asset_id']] = version
        for version in latest.values():
            normalized = version.get('metadata', {}).get('normalized', {})
            transcripts.append({'id': version['id'], 'assetId': version['asset_id'], 'revision': version['version'],
                'text': normalized.get('vtt') or normalized.get('full_text', ''),
                'fullText': normalized.get('full_text', ''), 'segments': version['segments'],
                'warnings': normalized.get('warnings', []), 'storage': 'owned'})
        def channel(key, values):
            statuses = [row[key] for row in data.get('channels', []) if key in row]
            state = ('ready' if values else 'processing' if not statuses or 'processing' in statuses else
                     'error' if any(s in ('error', 'unsupported') for s in statuses) else
                     'not_recorded' if all(s == 'not_recorded' for s in statuses) else 'processing')
            return {'status': state, 'ids': [item['id'] for item in values]}
        video, transcription = channel('video_status', recordings), channel('transcript_status', transcripts)
        if video_errors:
            video.update(status='ready' if recordings else 'error', error=video_errors[0])
        self._check(access_check)
        # Recheck core owner/binding revocation after potentially slow source I/O.
        if data.get('sources'):
            current = self.core.materials(owner_kind, meeting['id'])
            old = {(s.get('binding_id', s.get('id')), s['generation']) for s in active_sources}
            fresh = {(s.get('binding_id', s.get('id')), s['generation']) for s in current.get('sources', []) if not s.get('revoked')}
            require(old <= fresh, 'Доступ к материалам изменился.', 409)
        self._check(access_check)
        states = (video['status'], transcription['status'])
        return {'recordings': recordings, 'transcripts': transcripts, 'video': video, 'transcription': transcription,
            'status': 'ready' if 'ready' in states else 'error' if 'error' in states else
                      'processing' if 'processing' in states else 'not_recorded',
            'catalogVersion': 1, 'checkedAt': dt.datetime.now(dt.timezone.utc).isoformat()}


class ProviderMaterialsCatalog:
    """Explicit transition: Daily source API and existing Zoom collector.

    The portal passes a meeting loaded from its authorized owner row. Provider
    identity is never accepted from an HTTP materials request or inferred from
    an adapter error. A missing provider cannot fall through to another source.
    """
    def __init__(self, daily, zoom=None):
        self.daily, self.zoom = daily, zoom

    def _catalog(self, meeting):
        provider = (meeting.get('room') or {}).get('provider', 'daily')
        require(provider in ('daily', 'zoom'), 'Неизвестный источник материалов.', 409)
        catalog = self.daily if provider == 'daily' else self.zoom
        require(catalog is not None, 'Источник материалов ещё не подключён.', 503)
        return catalog

    def register_owner(self, owner_kind, meeting, storage_policy=None):
        return self._catalog(meeting).register_owner(owner_kind, meeting, storage_policy)

    def legacy_materials(self, owner_kind, meeting, *, access_check=None):
        return self._catalog(meeting).legacy_materials(owner_kind, meeting, access_check=access_check)


def clients_from_env(environ=None):
    env = os.environ if environ is None else environ
    core = CoreClient(ServiceHTTP(env.get('HR_MATERIALS_CORE_URL', ''), env.get('HR_MATERIALS_CORE_KEY', '')))
    source = SourceMedia(ServiceHTTP(env.get('HR_MATERIALS_SOURCE_URL', ''), env.get('HR_MATERIALS_SOURCE_KEY', '')),
                         enabled=env.get('HR_VIDEO_ENABLED') == '1')
    return core, source
