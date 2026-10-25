"""Private Zoom boundary: real HTTP for byte transport, provider calls are fakes."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient
from domain import Problem
from materials_capture import Artifact, Source
from materials_http import DownloadError, DownloadResult
from materials_zoom import ZoomCapture
from zoom_api_client import ZoomAPI, ZoomContent
from core_source_client import ServiceHTTP, ServiceError


spec = importlib.util.spec_from_file_location('private_zoom_api', Path(__file__).with_name('app.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
TOKEN = 'test_' + 'x' * 40
ROOM = {'provider': 'zoom', 'id': '12345678901', 'name': 'test', 'meetingNumber': '12345678901',
        'meetingSessionId': 'exact-session', 'connectionId': 'zoom'}
BODY = b'recording-data'


class Media:
    configured = capture_configured = selection_enabled = True
    connection_id = 'zoom'
    _hosts = {'recruiter': 'host@test.invalid'}
    _default_host = 'host@test.invalid'

    def __init__(self):
        self.calls = []

    def room(self, *args):
        self.calls.append(args)
        return ROOM

    def credentials(self, room, user_name, owner):
        assert room == ROOM
        return {'provider': 'zoom', 'role': int(owner), **({'zak': 'host-only'} if owner else {})}

    def get_state(self, room):
        assert room == ROOM
        return {'status': 'ended', 'providerSessionId': 'exact-session'}

    def recordings(self, room):
        assert room == ROOM
        return {'id': ROOM['id'], 'uuid': 'exact-session', 'recording_count': 2,
                'next_page_token': 'opaque-provider-page', 'recording_files': [
                    {'id': 'file-one', 'meeting_id': 'exact-session', 'file_type': 'MP4',
                     'status': 'completed', 'file_size': len(BODY), 'download_url': 'https://zoom.us/secret',
                     'unknown': 'private-value'}]}

    def resolve_download(self, file):
        assert file['id'] == 'file-one'
        return {'url': file['download_url'], 'headers': {'Authorization': 'Bearer provider-secret'}}


@pytest.fixture
def boundary(tmp_path):
    media = Media()
    def download(source, path, **kwargs):
        assert source.headers['Authorization'] == 'Bearer provider-secret'
        assert kwargs['expected_size'] == len(BODY)
        path.write_bytes(BODY)
        return DownloadResult(path, len(BODY), hashlib.sha256(BODY).hexdigest(), 'video/mp4')
    with TestClient(api.create_app(media, token=TOKEN, downloader=download, temp_root=tmp_path)) as client:
        yield client, media, tmp_path


def auth():
    return {'Authorization': 'Bearer ' + TOKEN}


def test_auth_and_configuration_no_provider_credentials(boundary):
    client, _, _ = boundary
    assert client.get('/health/live').status_code == 200
    assert client.get('/v1/config').status_code == 401
    assert client.get('/v1/config', headers={'Authorization': b'Bearer\xff'}).status_code == 401
    result = client.get('/v1/config', headers=auth())
    assert result.json()['configured'] is True
    assert 'provider-secret' not in result.text


def test_input_bound_checks_bytes_not_claimed_length(boundary):
    client, _, _ = boundary
    for headers in ({'Content-Length': '1'}, {'Content-Length': '1', 'Transfer-Encoding': 'chunked'}):
        response = client.post('/v1/state', content=json.dumps({'room': ROOM, 'extra': 'x' * 100000}),
            headers={**auth(), **headers, 'Content-Type': 'application/json'})
        assert response.status_code == 413
    assert client.post('/v1/access', json={'room': ROOM, 'user_name': 'Name', 'owner': 'yes'}, headers=auth()).status_code == 422


def test_roles_and_create_never_retry(boundary):
    client, media, _ = boundary
    for owner in (False, True):
        reply = client.post('/v1/access', headers=auth(), json={'room': ROOM, 'user_name': 'Name', 'owner': owner}).json()
        assert ('zak' in reply) is owner
    response = client.post('/v1/rooms', headers=auth(), json={'meeting_id': 'i', 'host_id': 'h', 'request_id': 'r'})
    assert response.status_code == 200 and len(media.calls) == 1


def test_list_strips_urls_but_preserves_partial_evidence(boundary):
    client, _, _ = boundary
    response = client.post('/v1/recordings', headers=auth(), json={'room': ROOM})
    result = response.json()
    assert result['recording_count'] == 2 and result['listing_complete'] is False
    assert not any(secret in response.text for secret in ('https:', 'provider-page', 'private-value'))
    media = Media()
    media.recordings = lambda _: result
    capture = ZoomCapture(media, 'zoom')
    assert capture._rows(Source('zoom', 'zoom', ROOM['id'], 'test', 'exact-session'))[1] is False


def test_content_exact_file_and_cleanup(boundary):
    client, _, temp = boundary
    assert client.post('/v1/recordings/content', headers=auth(), json={'room': ROOM, 'file_id': 'other'}).status_code == 404
    response = client.post('/v1/recordings/content', headers=auth(), json={'room': ROOM, 'file_id': 'file-one'})
    assert response.content == BODY
    assert response.headers['x-content-sha256'] == hashlib.sha256(BODY).hexdigest()
    assert list(temp.iterdir()) == []
    assert client.post('/v1/recordings/content', headers=auth(), json={'room': ROOM, 'file_id': 'file-one'}).status_code == 200


def test_content_failure_redacted_and_slot_released(tmp_path):
    calls = []
    def fail(*a, **k):
        calls.append(1)
        raise OSError('provider-secret https://zoom.us/private')
    with TestClient(api.create_app(Media(), token=TOKEN, downloader=fail, temp_root=tmp_path)) as client:
        for _ in range(2):
            result = client.post('/v1/recordings/content', headers=auth(), json={'room': ROOM, 'file_id': 'file-one'})
            assert result.status_code == 502 and 'provider-secret' not in result.text
    assert len(calls) == 2 and not list(tmp_path.iterdir())


@contextmanager
def http_server(*, status=200, digest=None, delay_headers=0, delay_body=0, size=None):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            assert self.headers['Authorization'] == 'Bearer ' + TOKEN
            time.sleep(delay_headers)
            self.send_response(status)
            self.send_header('Content-Length', str(len(BODY) if size is None else size))
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('X-Content-SHA256', digest or hashlib.sha256(BODY).hexdigest())
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.flush()
            time.sleep(delay_body)
            try:
                self.wfile.write(BODY)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ZoomContent('http://127.0.0.1:' + str(server.server_port), TOKEN, ROOM, 'file-one'), requests
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_private_http_bytes_verified_and_only_fixed_endpoint(tmp_path):
    with http_server() as (source, requests):
        result = source.download(tmp_path / 'video', max_bytes=100, expected_size=len(BODY))
    assert result.path.read_bytes() == BODY and result.size == len(BODY)
    assert requests == [('/v1/recordings/content', {'room': ROOM, 'file_id': 'file-one', 'timeout_seconds': 240, 'max_bytes': 100})]
    assert 'exact-session' not in repr(source) and TOKEN not in repr(source)


@pytest.mark.parametrize('options', [{'status': 302}, {'digest': '0' * 64}, {'size': 1000}])
def test_private_http_rejects_redirect_mismatch_and_oversize(tmp_path, options):
    target = tmp_path / 'video'
    target.write_bytes(b'previous')
    with http_server(**options) as (source, requests):
        with pytest.raises(DownloadError):
            source.download(target, max_bytes=100)
    assert target.read_bytes() == b'previous' and not list(tmp_path.glob('*.part'))
    assert len(requests) == 1


def test_absolute_deadline_closes_connection_close_response(tmp_path):
    with http_server(delay_headers=.2, delay_body=1.4) as (source, _):
        started = time.monotonic()
        with pytest.raises(DownloadError):
            source.download(tmp_path / 'video', max_bytes=100, timeout_seconds=.6)
        assert time.monotonic() - started < 1.1
    assert not (tmp_path / 'video').exists()


def test_zoom_capture_uses_private_file_transport():
    media = ZoomAPI(ServiceHTTP('http://127.0.0.1:8095', TOKEN))
    media.recordings = lambda _: {'id': ROOM['id'], 'uuid': 'exact-session', 'recording_files': [
        {'id': 'file-one', 'meeting_id': 'exact-session', 'file_type': 'MP4', 'status': 'completed', 'file_size': len(BODY)}]}
    media.download_source = lambda room, file: ZoomContent('http://127.0.0.1:8095', TOKEN, room, file)
    capture = ZoomCapture(media, 'zoom')
    source = Source('zoom', 'zoom', ROOM['id'], 'test', 'exact-session')
    artifact = capture.discover(source).artifacts[0]
    transport = capture.resolve(artifact)
    assert isinstance(transport, ZoomContent) and transport.file_id == 'file-one'
