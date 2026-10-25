"""Typed client of our Zoom API. No Zoom OAuth/SDK secrets or automatic retries."""
from dataclasses import dataclass, field
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
from urllib.parse import urlsplit

from core_source_client import ServiceHTTP, ServiceError
from domain import Problem


class ZoomAPI:
    def __init__(self, http):
        self.http = http
        self.config_http = ServiceHTTP(http.base_url, http.key, timeout=2, opener=http.opener)
        self._config, self._until = {}, 0
        self._lock = threading.Lock()

    def _configuration(self):
        with self._lock:
            if time.monotonic() >= self._until:
                try:
                    self._config = self.config_http.call('GET', '/v1/config')
                except ServiceError:
                    self._config = {}
                self._until = time.monotonic() + 5
            return self._config

    @property
    def configured(self):
        return self._configuration().get('configured') is True

    @property
    def capture_configured(self):
        return self._configuration().get('capture_configured') is True

    @property
    def selection_enabled(self):
        return self._configuration().get('selection_enabled') is True

    @property
    def connection_id(self):
        return self._configuration().get('connection_id')

    def host_for(self, actor_id):
        config = self._configuration()
        return config.get('hosts', {}).get(actor_id) or config.get('default_host')

    def _call(self, path, body):
        from media_runtime import provider_timeout
        # Room + access share the portal operation's existing total budget.
        http = ServiceHTTP(self.http.base_url, self.http.key,
            timeout=min(self.http.timeout, provider_timeout()), opener=self.http.opener)
        return http.call('POST', path, body)

    def room(self, meeting_id, host_id, request_id):
        return self._call('/v1/rooms', dict(meeting_id=meeting_id, host_id=host_id, request_id=request_id))

    def credentials(self, room, user_name, owner):
        return self._call('/v1/access', dict(room=room, user_name=user_name, owner=owner))

    def get_state(self, room):
        return self._call('/v1/state', {'room': room})

    def finish(self, room):
        return self._call('/v1/finish', {'room': room})

    def recordings(self, room):
        return self._call('/v1/recordings', {'room': room})

    def download_source(self, room, file_id):
        return ZoomContent(self.http.base_url, self.http.key, room, file_id)

    def materials(self, room, known=None, *, include_metadata=False):
        # Never send authenticated provider URLs to a browser through legacy UI.
        raise Problem('Материалы Zoom доступны через каталог материалов.', 503)


def zoom_from_env(environ=None):
    env = os.environ if environ is None else environ
    url = env.get('HR_ZOOM_API_URL')
    if url:
        return ZoomAPI(ServiceHTTP(url, env.get('HR_ZOOM_API_TOKEN', ''), timeout=28))
    # Explicit migration/rollback mode only, never a fallback after HTTP failure.
    from zoom_media import ZoomMedia
    return ZoomMedia.from_env(env)


@dataclass(frozen=True)
class ZoomContent:
    base_url: str = field(repr=False)
    token: str = field(repr=False)
    room: dict = field(repr=False)
    file_id: str

    def download(self, destination, *, max_bytes, timeout_seconds=300, expected_size=None, expected_sha256=None, headers=None):
        """Private service transport; public/provider downloads retain their SSRF checks."""
        from materials_http import DownloadError, DownloadResult
        # Constructor validation pins the trusted configured origin; caller cannot
        # supply a URL, redirect or provider Authorization in a catalog reference.
        ServiceHTTP(self.base_url, self.token)
        if (headers or type(max_bytes) is not int or not 0 < max_bytes <= 2 * 1024**3
                or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 1800
                or expected_size is not None and (type(expected_size) is not int or not 0 <= expected_size <= max_bytes)
                or expected_sha256 is not None and not re.fullmatch(r'[0-9a-f]{64}', expected_sha256)):
            raise DownloadError('Invalid Zoom content request', code='invalid_limits')
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, stage_name = tempfile.mkstemp(prefix='.zoom-', suffix='.part', dir=destination.parent)
        stage, deadline = Path(stage_name), time.monotonic() + timeout_seconds
        origin = urlsplit(self.base_url)
        connection_type = http.client.HTTPSConnection if origin.scheme == 'https' else http.client.HTTPConnection
        connection = connection_type(origin.hostname, origin.port, timeout=min(5, timeout_seconds))
        connected_socket = None
        expired = threading.Event()
        def expire():
            expired.set()
            active_socket = connected_socket or connection.sock
            if active_socket:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                active_socket.close()
        def check_deadline():
            if expired.is_set() or time.monotonic() >= deadline:
                raise DownloadError('Zoom download timed out', code='timeout', retryable=True)
        timer = threading.Timer(timeout_seconds, expire)
        timer.daemon = True
        timer.start()
        try:
            with os.fdopen(fd, 'wb') as output:
                connection.connect()
                connected_socket = connection.sock
                check_deadline()
                connection.sock.settimeout(max(.001, deadline - time.monotonic()))
                connection.request('POST', '/v1/recordings/content',
                    body=json.dumps({'room': self.room, 'file_id': self.file_id,
                        'timeout_seconds': max(1, int(timeout_seconds * .8)), 'max_bytes': max_bytes}).encode(),
                    headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json', 'Accept-Encoding': 'identity'})
                with connection.getresponse() as response:
                    check_deadline()
                    if response.status != 200:
                        raise DownloadError('Zoom content unavailable', code='zoom_content_unavailable',
                            retryable=response.status >= 500)
                    length = response.headers.get('Content-Length', '')
                    sha = response.headers.get('X-Content-SHA256', '')
                    if (not length.isdecimal() or int(length) > max_bytes or not re.fullmatch(r'[0-9a-f]{64}', sha)
                            or response.headers.get('Content-Encoding', 'identity') != 'identity'):
                        raise DownloadError('Invalid Zoom content response', code='invalid_response')
                    if expected_size is not None and int(length) != expected_size:
                        raise DownloadError('Zoom size mismatch', code='size_mismatch')
                    size, digest = 0, hashlib.sha256()
                    while True:
                        check_deadline()
                        chunk = response.read1(65536)
                        check_deadline()
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            raise DownloadError('Zoom download limit reached', code='download_limit', retryable=True)
                        output.write(chunk); digest.update(chunk)
                    if size != int(length) or digest.hexdigest() != sha or (expected_sha256 and sha != expected_sha256):
                        raise DownloadError('Zoom content verification failed', code='content_mismatch', retryable=True)
                    output.flush(); os.fsync(output.fileno())
                    content_type = response.headers.get('Content-Type', 'application/octet-stream').split(';')[0]
            os.replace(stage, destination)
            return DownloadResult(destination, size, sha, content_type)
        except (OSError, http.client.HTTPException):
            raise DownloadError('Zoom service transport failed', code='transport_failed', retryable=True) from None
        finally:
            timer.cancel()
            connection.close()
            stage.unlink(missing_ok=True)
