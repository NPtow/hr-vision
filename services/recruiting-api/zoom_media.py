"""Opt-in Zoom Meetings adapter; Daily and product authorization stay separate.

The caller owns durable creation claims and room persistence. A failed create is
never retried here: a network error can mean Zoom created the meeting already.
Only the authenticated join route may return ``credentials`` to the browser.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from domain import Problem, iso, now
from media_runtime import provider_timeout


SDK_VERSION = '6.10.0'
SDK_KEYS = ('ZOOM_MEETING_SDK_CLIENT_ID', 'ZOOM_MEETING_SDK_CLIENT_SECRET')
REST_KEYS = ('ZOOM_S2S_ACCOUNT_ID', 'ZOOM_S2S_CLIENT_ID', 'ZOOM_S2S_CLIENT_SECRET')
MAX_JSON = 2 * 1024 * 1024
NUMBER = re.compile(r'[0-9]{9,11}\Z')


class ZoomMediaError(Problem):
    """Public-safe error; never retains a provider body, URL, token or email."""

    def __init__(self, message, *, code='zoom_response', status=502, provider_status=None, zoom_code=None):
        super().__init__(message, status)
        self.code = code
        self.provider_status = provider_status
        self.zoom_code = zoom_code


def check(condition, message, *, code='zoom_response', status=502):
    if not condition:
        raise ZoomMediaError(message, code=code, status=status)


def _plain(value, limit=320):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _session(value):
    return _plain(value, 200) and not any(c.isspace() for c in value)


def _host_id(value):
    return _plain(value) and not any(c.isspace() for c in value)


def _encode_uuid(value):
    check(_session(value), 'Не определена сессия Zoom.', code='zoom_session', status=409)
    encoded = urllib.parse.quote(value, safe='')
    return urllib.parse.quote(encoded, safe='') if value.startswith('/') or '//' in value else encoded


def _trusted_url(value):
    check(isinstance(value, str) and 0 < len(value) <= 16384 and '\\' not in value
          and not any(ord(c) < 33 or ord(c) == 127 for c in value),
          'Zoom вернул неподдерживаемый адрес файла.', code='zoom_download_origin')
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or '').lower()
        safe = (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.fragment
                and (host in ('zoom.us', 'zoom.com') or host.endswith(('.zoom.us', '.zoom.com'))))
    except (ValueError, TypeError):
        safe = False
    check(safe, 'Zoom вернул неподдерживаемый адрес файла.', code='zoom_download_origin')
    return parsed


def _timestamp(value):
    if not _plain(value, 80):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return iso(parsed) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def sdk_signature(client_id, client_secret, meeting_number, role, *, now_seconds=None):
    check(type(role) is int and role in (0, 1), 'Неверная роль Zoom.', status=400)
    check(isinstance(meeting_number, str) and NUMBER.fullmatch(meeting_number),
          'Неверный номер встречи Zoom.', status=400)
    issued = int(time.time() if now_seconds is None else now_seconds) - 30
    claims = {'appKey': client_id, 'mn': meeting_number, 'role': role,
              'iat': issued, 'exp': issued + 7200, 'tokenExp': issued + 7200}

    def encoded(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')

    unsigned = '.'.join(encoded(json.dumps(value, separators=(',', ':')).encode())
                        for value in ({'alg': 'HS256', 'typ': 'JWT'}, claims))
    signature = hmac.new(client_secret.encode(), unsigned.encode(), hashlib.sha256).digest()
    return unsigned + '.' + encoded(signature)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ZoomMedia:
    def __init__(self, values=None, *, opener=None):
        values = values or {}
        self._values = {key: str(values.get(key, '')).strip() for key in SDK_KEYS + REST_KEYS}
        self.enabled = str(values.get('HR_ZOOM_ENABLED', '0')) == '1'
        self.connection_id = str(values.get('HR_ZOOM_CONNECTION_ID', 'zoom')).strip()
        self._connection_valid = bool(re.fullmatch(r'[A-Za-z0-9_-]{1,100}', self.connection_id))
        try:
            host_map = json.loads(str(values.get('HR_ZOOM_HOST_MAP_JSON', '{}')))
            valid = (isinstance(host_map, dict) and len(host_map) <= 256
                     and all(_plain(actor, 200) and _plain(host) and host == host.strip()
                             and not any(c.isspace() for c in host) for actor, host in host_map.items()))
            self._hosts = host_map if valid else {}
        except (ValueError, TypeError):
            self._hosts = {}
        # A shared licensed host is an explicit deployment setting, not a PoC
        # fallback. Individual mappings take precedence. Zoom still enforces
        # the configured host's own participant/concurrent-meeting limits.
        default_host = values.get('HR_ZOOM_DEFAULT_HOST_ID', '')
        self._default_host = default_host if _host_id(default_host) else None
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self._token, self._token_expiry = '', 0
        self._token_lock = threading.Lock()

    @classmethod
    def from_env(cls, environ=None):
        # Never loads a PoC .env or a default host implicitly.
        return cls(os.environ if environ is None else environ)

    @property
    def capture_configured(self):
        return bool(self._connection_valid and all(self._values[key] for key in REST_KEYS))

    @property
    def recording_account_id(self):
        return self._values['ZOOM_S2S_ACCOUNT_ID']

    @property
    def configured(self):
        return bool(self.capture_configured and all(self._values[key] for key in SDK_KEYS))

    @property
    def selection_enabled(self):
        return bool(self.enabled and self.configured and (self._hosts or self._default_host))

    def host_for(self, actor_id):
        return (self._hosts.get(actor_id) or self._default_host) if _plain(actor_id, 200) else None

    def _json(self, request):
        try:
            with self.opener.open(request, timeout=min(15, provider_timeout())) as response:
                status = response.status
                raw = response.read(MAX_JSON + 1)
            check(len(raw) <= MAX_JSON, 'Ответ Zoom слишком большой.')
            if request.get_method() == 'PUT' and request.full_url.endswith('/status'):
                check(status == 204, 'Zoom не подтвердил завершение встречи.', code='zoom_finish')
            if request.get_method() == 'DELETE':
                check(status == 204, 'Zoom не подтвердил перемещение записи в корзину.', code='zoom_trash')
            if status == 204:
                check(not raw, 'Zoom вернул неверное подтверждение операции.')
                return {}
            result = json.loads(raw)
            check(isinstance(result, dict), 'Zoom вернул неверный ответ.')
            return result
        except urllib.error.HTTPError as error:
            # A bounded body is parsed only for a numerical code. Never retain
            # its message, request URL or response data in an exception/log.
            zoom_code = None
            try:
                raw = error.read(16 * 1024 + 1)
                data = json.loads(raw) if len(raw) <= 16 * 1024 else {}
                if isinstance(data, dict) and type(data.get('code')) is int:
                    zoom_code = data['code']
            except (OSError, ValueError, http.client.HTTPException):
                pass
            finally:
                error.close()
            message = ('Zoom не разрешил операцию. Проверьте права подключения.'
                       if error.code in (401, 403) else
                       'Zoom ограничил частоту запросов. Повторите позже.' if error.code == 429 else
                       'Zoom не выполнил запрос. Повторите чтение или завершение позже.')
            raise ZoomMediaError(message, code=f'zoom_http_{error.code}',
                                 provider_status=error.code, zoom_code=zoom_code) from None
        except (OSError, http.client.HTTPException):
            raise ZoomMediaError('Zoom не ответил. Исход операции пока не подтверждён.',
                                 code='zoom_unavailable') from None
        except ValueError:
            raise ZoomMediaError('Zoom вернул неверный ответ.') from None

    def token(self):
        check(self.capture_configured, 'Серверное подключение Zoom не настроено.', code='zoom_config', status=503)
        acquired = self._token_lock.acquire(timeout=min(5, provider_timeout()))
        check(acquired, 'Предыдущий запрос доступа Zoom ещё выполняется.', code='zoom_busy', status=503)
        try:
            if self._token and time.monotonic() < self._token_expiry:
                return self._token
            basic = base64.b64encode((self._values['ZOOM_S2S_CLIENT_ID'] + ':' +
                                      self._values['ZOOM_S2S_CLIENT_SECRET']).encode()).decode('ascii')
            body = urllib.parse.urlencode({'grant_type': 'account_credentials',
                                           'account_id': self._values['ZOOM_S2S_ACCOUNT_ID']}).encode()
            result = self._json(urllib.request.Request('https://zoom.us/oauth/token', data=body,
                headers={'Authorization': 'Basic ' + basic, 'Content-Type': 'application/x-www-form-urlencoded'},
                method='POST'))
            token, lifetime = result.get('access_token'), result.get('expires_in')
            check(_plain(token, 16384) and type(lifetime) in (int, float) and 60 < lifetime <= 86400,
                  'Zoom не выдал серверный доступ.')
            self._token, self._token_expiry = token, time.monotonic() + lifetime - 60
            return token
        finally:
            self._token_lock.release()

    def request(self, path, body=None, method=None):
        check(isinstance(path, str) and path.startswith('/') and not path.startswith('//')
              and not any(c in path for c in ('\r', '\n', '#', '\\')),
              'Неверный путь запроса Zoom.', status=400)
        method = method or ('GET' if body is None else 'POST')
        allowed_trash = (method == 'DELETE' and body is None and
                         re.fullmatch(r'/meetings/[A-Za-z0-9%_.~+-]+/recordings/[A-Za-z0-9_-]{1,200}\?action=trash', path))
        check(method in ('GET', 'POST', 'PUT', 'PATCH') or allowed_trash, 'Неверный метод Zoom.', status=400)
        request = urllib.request.Request('https://api.zoom.us/v2' + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + self.token(), 'Content-Type': 'application/json'}, method=method)
        try:
            return self._json(request)
        except ZoomMediaError as error:
            if error.provider_status == 401:
                # Refresh only on a later explicit operation, never replay a
                # create/mutation whose outcome might be uncertain.
                self._token_expiry = 0
            raise

    def _room_identity(self, room):
        check(isinstance(room, dict) and room.get('provider') == 'zoom',
              'Комната не принадлежит Zoom.', code='zoom_room', status=409)
        number = room.get('meetingNumber') or room.get('id')
        check(isinstance(number, str) and NUMBER.fullmatch(number)
              and room.get('id', number) == number and room.get('connectionId') == self.connection_id,
              'Привязка комнаты Zoom изменилась.', code='zoom_room', status=409)
        session = room.get('meetingSessionId')
        check(_session(session), 'Не определена сессия Zoom.', code='zoom_session', status=409)
        return number, session

    def room(self, meeting_id, host_id, request_id):
        check(self.configured, 'Zoom ещё не настроен.', code='zoom_config', status=503)
        check(_plain(meeting_id, 200) and _plain(request_id, 200), 'Не определена встреча.', status=400)
        check(_host_id(host_id) and (host_id in self._hosts.values() or host_id == self._default_host),
              'Организатору не назначен аккаунт Zoom.', code='zoom_host', status=409)
        opaque = hashlib.sha256((meeting_id + ':' + request_id).encode()).hexdigest()[:16]
        password = 'aA1' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(7))
        result = self.request('/users/' + urllib.parse.quote(host_id, safe='') + '/meetings', {
            'topic': 'МашаДаша · интервью ' + opaque, 'type': 2, 'duration': 45, 'password': password,
            'settings': {'host_video': False, 'participant_video': False, 'join_before_host': False,
                         'waiting_room': False, 'auto_recording': 'cloud', 'use_pmi': False}})
        number, session = str(result.get('id', '')), result.get('uuid')
        settings = result.get('settings')
        check(NUMBER.fullmatch(number) and _session(session) and _plain(result.get('password'), 100)
              and isinstance(settings, dict) and settings.get('auto_recording') == 'cloud'
              and settings.get('waiting_room') is False and settings.get('join_before_host') is False,
              'Zoom не подтвердил автоматическую облачную запись и настройки входа.')
        parsed = _trusted_url(result.get('join_url', ''))
        check(parsed.path == '/j/' + number, 'Zoom вернул ссылку другой встречи.')
        return {'provider': 'zoom', 'id': number, 'name': number,
                'url': urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', '')),
                'meetingNumber': number, 'password': result['password'], 'meetingSessionId': session,
                'zoomHostId': host_id, 'connectionId': self.connection_id, 'captureInstanceId': opaque,
                'recordingPolicy': 'automatic-cloud'}

    def credentials(self, room, user_name, owner):
        check(self.configured, 'Zoom ещё не настроен.', code='zoom_config', status=503)
        number, _ = self._room_identity(room)
        check(type(owner) is bool and _plain(user_name, 200) and _plain(room.get('password'), 100),
              'Неверные данные входа в Zoom.', status=400)
        role = 1 if owner else 0
        result = {'provider': 'zoom', 'meetingNumber': number, 'passWord': room['password'],
                  'userName': user_name, 'role': role, 'sdkVersion': SDK_VERSION,
                  'signature': sdk_signature(self._values[SDK_KEYS[0]], self._values[SDK_KEYS[1]], number, role)}
        if owner:
            host = room.get('zoomHostId')
            check(_plain(host), 'Не определён организатор Zoom.', code='zoom_host', status=409)
            response = self.request('/users/' + urllib.parse.quote(host, safe='') + '/token?type=zak')
            check(_plain(response.get('token'), 16384), 'Zoom не выдал доступ организатора.')
            result['zak'] = response['token']
        return result

    def get_state(self, room):
        """Read-only lifecycle. Waiting/404 alone never proves a completed call.

        Before first pin, the current meeting can identify the actual instance
        after a missed joined receipt. A waiting UUID is only a lookup hint:
        exact past-instance proof is still required to report it ended. Once
        pinned, another current UUID can never replace the saved instance.
        """
        number, session = self._room_identity(room)
        pinned = room.get('sessionPinned') is True
        try:
            result = self.request('/meetings/' + number)
        except ZoomMediaError as error:
            if error.provider_status != 404 or error.zoom_code != 3001:
                raise
            result = None
        if result is not None:
            check(str(result.get('id')) == number and result.get('status') in ('waiting', 'started')
                  and _session(result.get('uuid')), 'Zoom вернул неверный статус встречи.')
            if result['status'] == 'started':
                check(not pinned or result['uuid'] == session,
                      'Zoom открыл другой сеанс. Нужна проверка команды.', code='zoom_session', status=409)
                return {'provider': 'zoom', 'status': 'started', 'meetingNumber': number,
                        'meetingSessionId': result['uuid'], 'providerSessionId': result['uuid']}
        # Do not query recordings by reusable number or enumerate account history.
        # Current meeting metadata may provide the exact UUID missed by /connected.
        past_session = result['uuid'] if result is not None and not pinned else session
        try:
            ended = self.request('/past_meetings/' + _encode_uuid(past_session))
        except ZoomMediaError as error:
            if error.provider_status != 404 or error.zoom_code != 3001:
                raise
            check(not pinned or result is None or result['uuid'] == session,
                  'Zoom открыл другой сеанс. Нужна проверка команды.', code='zoom_session', status=409)
            return {'provider': 'zoom', 'status': 'waiting' if result else 'unknown',
                    'meetingNumber': number, 'meetingSessionId': past_session,
                    'providerSessionId': past_session}
        ended_at = _timestamp(ended.get('end_time'))
        check(str(ended.get('id')) == number and ended.get('uuid') == past_session and ended_at,
              'Zoom не подтвердил завершение нужной сессии.', code='zoom_session')
        return {'provider': 'zoom', 'status': 'ended', 'meetingNumber': number,
                'meetingSessionId': past_session, 'providerSessionId': past_session, 'endedAt': ended_at}

    def finish(self, room):
        number, session = self._room_identity(room)
        state = self.get_state(room)
        if state['status'] == 'ended':
            return {'status': 'ended'}
        check(state['status'] in ('started', 'waiting') and state.get('providerSessionId') == session,
              'Сессия Zoom изменилась. Завершение требует повторной проверки.', code='zoom_session', status=409)
        try:
            # The documented 204 response acknowledges ending the meeting.
            self.request('/meetings/' + number + '/status', {'action': 'end'}, method='PUT')
        except ZoomMediaError as error:
            # Auth, rate limiting, malformed input and transport failure cannot
            # be reinterpreted as success. A missing meeting is safe only after
            # read-only proof that this exact instance has already ended.
            if error.provider_status != 404 or error.zoom_code != 3001:
                raise
            if self.get_state(room)['status'] != 'ended':
                raise error
        return {'status': 'ended'}

    def recordings(self, room):
        """Private provider metadata; exact instance, never latest numeric ID."""
        number, session = self._room_identity(room)
        try:
            result = self.request('/meetings/' + _encode_uuid(session) + '/recordings')
        except ZoomMediaError as error:
            if error.provider_status == 404 and error.zoom_code in (3001, 3301):
                return {'id': number, 'uuid': session, 'recording_files': [], 'status': 'absent'}
            raise
        files = result.get('recording_files')
        check(str(result.get('id')) == number and result.get('uuid') == session
              and isinstance(files, list) and len(files) <= 100 and all(isinstance(item, dict) for item in files),
              'Zoom вернул материалы другой встречи или неверный ответ.', code='zoom_session')
        for item in files:
            if item.get('meeting_id') is not None:
                check(item['meeting_id'] == session, 'Zoom вернул файл другой встречи.', code='zoom_session')
        return result

    def resolve_download(self, file):
        """Internal transport only. Callers must validate every redirect too."""
        check(isinstance(file, dict) and file.get('status') == 'completed',
              'Запись Zoom ещё не готова.', code='zoom_processing', status=409)
        url = file.get('download_url')
        _trusted_url(url)
        return {'url': url, 'headers': {'Authorization': 'Bearer ' + self.token()}}

    def recording_retention_settings(self):
        """Read-only provider guard for the verified-copy retention worker."""
        check(_plain(self.recording_account_id), 'Не определён аккаунт Zoom.', code='zoom_config', status=503)
        result = self.request('/accounts/' + urllib.parse.quote(self.recording_account_id, safe='') + '/settings')
        recording = result.get('recording')
        check(isinstance(recording, dict), 'Zoom не подтвердил настройки хранения записей.')
        return {key: recording.get(key) for key in ('allow_recovery_deleted_cloud_recordings', 'auto_delete_cmr')}

    def trash_recording_file(self, room, recording_id):
        """Trash one exact pinned file. Eligibility belongs to the retention worker.

        No permanent delete and no automatic replay of an uncertain mutation.
        """
        _, session = self._room_identity(room)
        check(room.get('sessionPinned') is True, 'Сессия Zoom ещё не закреплена.', code='zoom_session', status=409)
        check(isinstance(recording_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', recording_id),
              'Не определён файл записи Zoom.', code='zoom_recording', status=400)
        self.request('/meetings/' + _encode_uuid(session) + '/recordings/' + recording_id + '?action=trash', method='DELETE')
        return {'status': 'trashed'}

    def materials(self, room, known=None, *, include_metadata=False):
        # The capture catalog is the only path for private authenticated files.
        # Never serialize provider download URLs or Bearer headers in legacy UI.
        self._room_identity(room)
        state = {'status': 'error', 'ids': [], 'code': 'zoom_catalog_required',
                 'error': 'Материалы Zoom доступны через каталог материалов.'}
        return {'recordings': [], 'transcripts': [], 'video': dict(state),
                'transcription': dict(state), 'checkedAt': iso(now())}
