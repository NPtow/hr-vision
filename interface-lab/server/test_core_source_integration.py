"""Service boundary and completion durability; all input and network are synthetic."""
import base64
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, Mock, patch
import urllib.error

from core_source_client import (CoreClient, CoreMaterialsCatalog, ServiceError,
                                ServiceHTTP, SourceMedia, clients_from_env, meeting_token)
from core_source_outbox import CompletionOutbox
from domain import Problem
import test_candidate_portal as fixtures


class FakeCore:
    def __init__(self):
        self.bindings = []
        self.fail = False
        self.on_bind = None

    def bind(self, payload):
        self.bindings.append(dict(payload))
        if self.on_bind:
            self.on_bind()
        if self.fail:
            raise RuntimeError('synthetic private payload must never persist')
        return {'binding_id': 'synthetic-binding', 'generation': 1}


class FakeSource(SourceMedia):
    def __init__(self):
        self.configured = True
        self.policy = 'remote'
        self.jobs, self.tokens = [], []
        self.fail = False
        self.crash = False

    def capabilities(self):
        return {'default_storage_policy': self.policy, 'connection_id': 'daily'}

    def room(self, mid, **kwargs):
        return {'id': mid, 'name': 'hrv-' + mid, 'url': 'https://test.daily.co/hrv-' + mid}

    def token(self, room, properties):
        self.tokens.append(properties)
        return {'token': 'synthetic-call-token'}

    def finish(self, room):
        return {'acknowledged': True}

    def enqueue(self, binding_id, generation):
        self.jobs.append({'binding_id': binding_id, 'generation': generation})
        if self.crash:
            raise SystemExit('synthetic process crash after source accepted')
        if self.fail:
            raise RuntimeError('synthetic private source failure')
        return {'accepted': True}

    def start_capture(self, room, recording_instance_id, **kwargs):
        return {'recording_requested': True, 'transcription_requested': True}


class BoundaryClientTests(unittest.TestCase):
    def test_source_typed_methods_never_allow_arbitrary_provider_path(self):
        http = Mock()
        http.call.return_value = {'acknowledged': True}
        source = SourceMedia(http)
        self.assertFalse(hasattr(source, 'request'))
        room = {'id': 'room-one', 'name': 'room-one'}
        source.room('meeting-one', recordable=False, screenshare=True)
        source.token(room, {'user_id': 'person-one'})
        source.start_capture(room, 'instance-one', start_recording=True, start_transcription=False)
        source.finish(room)
        source.materials(room, include_metadata=True)
        source.enqueue('binding-one', 3)
        source.access('binding-one', 'video-one')
        self.assertEqual([c.args[1] for c in http.call.call_args_list], [
            '/v1/calls/room', '/v1/calls/token', '/v1/calls/start', '/v1/calls/finish',
            '/v1/calls/materials', '/v1/jobs', '/v1/materials/access'])
        self.assertEqual(http.call.call_args_list[-2].args[2], {'binding_id': 'binding-one', 'generation': 3})

    def test_http_auth_and_identifier_remain_scoped_to_configured_endpoint(self):
        opener = MagicMock()
        response = Mock()
        response.read.return_value = b'{"assets":[]}'
        opener.open.return_value.__enter__.return_value = response
        core = CoreClient(ServiceHTTP('http://127.0.0.1:8180', 'synthetic-core-key', opener=opener))
        core.materials('interview', 'candidate/other?private=1')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8180/v1/owners/interview/candidate%2Fother%3Fprivate%3D1/materials')
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-core-key')
        with self.assertRaises(Problem):
            core.materials('arbitrary-owner', 'one')

    def test_http_error_never_echoes_body_url_or_credential(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://private.invalid/secret', 409,
            'private transcription text', {}, io.BytesIO(b'private caller token'))
        http = ServiceHTTP('https://service.example.test', 'private-credential', opener=opener)
        with self.assertRaises(ServiceError) as caught:
            http.call('POST', '/v1/jobs', {'private': 'interview text'})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.message, 'materials_service_rejected')
        for secret in ('private', 'interview', 'token', 'credential'):
            self.assertNotIn(secret, caught.exception.message)

    def test_network_error_sanitized_and_cross_origin_configuration_rejected(self):
        opener = Mock()
        opener.open.side_effect = OSError('secret URL and private text')
        with self.assertRaises(ServiceError) as caught:
            ServiceHTTP('https://example.test', 'test-key', opener=opener).call('GET', '/v1/capabilities')
        self.assertEqual(caught.exception.message, 'materials_service_unavailable')
        for base in ('http://public.example.test', 'https://user:pass@example.test', 'https://example.test/path',
                     'https://example.test?key=secret'):
            with self.assertRaises(ValueError):
                ServiceHTTP(base, 'test-key')

    def test_old_media_token_helper_preserves_fake_fixture(self):
        media = fixtures.FakeMedia()
        result = meeting_token(media, {'id': 'room'}, {'user_name': 'Synthetic'})
        self.assertEqual(result['token'], 'test-only-provider-token')
        self.assertEqual(media.tokens, [{'user_name': 'Synthetic'}])

    def test_finish_requires_source_acknowledgement(self):
        http = Mock()
        http.call.return_value = {'acknowledged': False}
        with self.assertRaises(Problem) as caught:
            SourceMedia(http).finish({'id': 'synthetic-room'})
        self.assertEqual(caught.exception.status, 502)

    def test_disabled_video_does_not_create_remote_room(self):
        http = Mock()
        with self.assertRaises(Problem) as caught:
            SourceMedia(http, enabled=False).room('synthetic-meeting')
        self.assertEqual(caught.exception.status, 503)
        http.call.assert_not_called()

    def test_webhook_client_bounds_headers_before_any_http(self):
        opener = Mock()
        client = ServiceHTTP('http://127.0.0.1:8181', 'synthetic-source-key', opener=opener)
        for headers in ({}, {'X-Webhook-Timestamp': '1', 'X-Webhook-Signature': 'x' * 257},
                        {'X-Webhook-Timestamp': '1', 'X-Webhook-Signature': 'x\r\nInjected: value'}):
            with self.assertRaises(Problem) as caught:
                client.forward_webhook(b'{}', headers)
            self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(Problem):
            client.forward_webhook(b'x' * (256 * 1024 + 1),
                {'X-Webhook-Timestamp': '1', 'X-Webhook-Signature': 'synthetic'})
        opener.open.assert_not_called()

    def test_app_modes_isolate_provider_import_and_preserve_disabled_compatibility(self):
        code = '''import json,sys,urllib.request
def forbidden(*args,**kwargs):
    raise AssertionError('Startup must not contact any service')
urllib.request.OpenerDirector.open=forbidden
import app
print(json.dumps({'media':type(app.MEDIA).__name__,'daily_loaded':'daily_media' in sys.modules,
    'runtime_loaded':'materials_runtime' in sys.modules,
    'outbox':app.CANDIDATE_PORTAL.materials_outbox is not None,'configured':app.MEDIA.configured}))
'''
        for enabled in ('0', '1'):
            with tempfile.TemporaryDirectory(prefix='portal-services-mode-') as root:
                env = dict(os.environ, HR_DATA_DIR=root, HR_TEAM_KEY='synthetic-team-key',
                    HR_MATERIALS_SERVICES_ENABLED=enabled, HR_MATERIALS_ENABLED=enabled, HR_VIDEO_ENABLED='1',
                    DAILY_API_KEY='synthetic-legacy-key')
                for key in ('HR_MATERIALS_CORE_URL', 'HR_MATERIALS_CORE_KEY', 'HR_MATERIALS_SOURCE_URL', 'HR_MATERIALS_SOURCE_KEY'):
                    env.pop(key, None)
                if enabled == '1':
                    env.update(HR_MATERIALS_CORE_URL='http://127.0.0.1:8180', HR_MATERIALS_CORE_KEY='synthetic-core-key',
                        HR_MATERIALS_SOURCE_URL='http://127.0.0.1:8181', HR_MATERIALS_SOURCE_KEY='synthetic-source-key')
                result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parent,
                    env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                mode = json.loads(result.stdout)
                self.assertEqual(mode, {'media': 'SourceMedia' if enabled == '1' else 'DailyMedia',
                    'daily_loaded': enabled == '0', 'runtime_loaded': False,
                    'outbox': enabled == '1', 'configured': True})


class CompletionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CandidatePortalTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.portal, self.connect = self.fixture.portal, self.fixture.connect
        self.source, self.core = FakeSource(), FakeCore()
        self.clock = [1800000000.]
        self.outbox = CompletionOutbox(self.connect, self.core, self.source, clock=lambda: self.clock[0])
        self.portal.media = self.source
        self.portal.materials_outbox = self.outbox

    def rows(self):
        with self.connect() as db:
            return db.execute('SELECT status,payload,last_error FROM portal_materials_outbox').fetchall()

    def test_completion_commits_intent_and_repeated_completion_deduplicates(self):
        person, _ = self.fixture.completed_person()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.core.bindings, [])
        self.assertEqual(self.source.jobs, [])
        self.fixture.call('recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0][0], 'pending')

    def test_intent_failure_rolls_back_completed_status_and_business_event(self):
        # Fail after the INSERT: both it and the business completion must roll back.
        original = self.outbox.record
        captured = []
        def fail(db, kind, identity, room, *args):
            original(db, kind, identity, room, *args)
            captured.append(identity)
            raise RuntimeError('synthetic before commit crash')
        with patch.object(self.outbox, 'record', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.fixture.completed_person()
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM cp_interviews WHERE id=?', (captured[0],)).fetchone()[0], 'live')
            self.assertEqual(db.execute("SELECT count(*) FROM fake_events WHERE type='interview.completed'").fetchone()[0], 0)
        self.assertEqual(self.rows(), [])

    def test_already_completed_retry_recovers_missing_intent(self):
        person, _ = self.fixture.completed_person()
        with self.connect() as db:
            db.execute('DELETE FROM portal_materials_outbox')
        self.fixture.call('recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        self.assertEqual(len(self.rows()), 1)

    def test_dispatch_retries_binding_and_source_job_without_network_in_transaction(self):
        self.fixture.completed_person()
        def concurrent_write():
            # A second write succeeds only if dispatcher has committed its claim.
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE portal_materials_outbox SET updated_at=updated_at")
        self.core.on_bind = concurrent_write
        self.source.fail = True
        self.assertEqual(self.outbox.dispatch_once()['state'], 'retry')
        self.assertEqual(self.rows()[0][2], 'service_delivery_failed')
        self.assertEqual(self.outbox.dispatch_once(), {'claimed': False})
        self.clock[0] += 31
        self.source.fail = False
        self.assertEqual(self.outbox.dispatch_once()['state'], 'delivered')
        self.assertEqual(self.core.bindings[0], self.core.bindings[1])
        self.assertEqual(self.source.jobs[0], self.source.jobs[1])
        self.assertEqual(set(self.source.jobs[0]), {'binding_id', 'generation'})

    def test_process_crash_after_source_acceptance_is_recoverable(self):
        self.fixture.completed_person()
        self.source.crash = True
        with self.assertRaises(SystemExit):
            self.outbox.dispatch_once()
        self.assertEqual(self.rows()[0][0], 'running')
        self.clock[0] += 121
        self.source.crash = False
        self.assertEqual(self.outbox.dispatch_once()['state'], 'delivered')
        self.assertEqual(self.source.jobs[0], self.source.jobs[1])

    def test_source_policy_pinned_before_lost_binding_response(self):
        self.fixture.completed_person()
        self.core.fail = True
        self.assertEqual(self.outbox.dispatch_once()['state'], 'retry')
        self.clock[0] += 31
        self.core.fail = False
        self.source.policy = 'owned'
        self.assertEqual(self.outbox.dispatch_once()['state'], 'delivered')
        self.assertEqual([p['storage_policy'] for p in self.core.bindings], ['remote', 'remote'])

    def test_delivery_failure_is_visible_on_materials_read(self):
        person, _ = self.fixture.completed_person()
        self.core.fail = True
        self.assertEqual(self.outbox.dispatch_once()['state'], 'retry')
        with self.connect() as db:
            meeting = self.portal.interview(db, person['id'])
        self.core.materials = Mock(side_effect=ServiceError('materials_service_rejected', 404))
        with self.assertRaises(ServiceError) as caught:
            CoreMaterialsCatalog(self.core, self.source, self.outbox).legacy_materials('interview', meeting)
        self.assertEqual(caught.exception.message, 'materials_delivery_retrying')

    def test_owned_policy_request_creates_new_durable_delivery_for_completed_owner(self):
        person, _ = self.fixture.completed_person()
        self.assertEqual(self.outbox.dispatch_once()['state'], 'delivered')
        with self.connect() as db:
            meeting = self.portal.interview(db, person['id'])
        catalog = CoreMaterialsCatalog(self.core, self.source, self.outbox)
        catalog.register_owner('interview', meeting, 'owned')
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.outbox.dispatch_once()['state'], 'delivered')
        self.assertEqual(self.core.bindings[-1]['storage_policy'], 'owned')

    def test_start_capture_persists_partial_acknowledgements_before_safe_error(self):
        person, cookie = self.fixture.register()
        slot = self.fixture.slot(recruiter=person['id'])
        self.fixture.call('candidate/book', {'slotId': slot['id']}, cookie)
        self.fixture.call('recruiter/interview/join', {'candidateId': person['id']}, manager=True)
        with patch.object(self.source, 'start_capture', return_value={
                'recording_requested': True, 'transcription_requested': False,
                'transcription_error': 'private provider text'}):
            with self.assertRaises(Problem) as caught:
                self.portal.start_interview(person['id'], True)
        self.assertEqual(caught.exception.status, 502)
        self.assertNotIn('private', caught.exception.message)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT recording_requested,transcription_requested FROM cp_recording_control').fetchone(), (1, 0))
        self.portal.start_interview(person['id'], True)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT recording_requested,transcription_requested FROM cp_recording_control').fetchone(), (1, 1))


class CatalogueProjectionTests(unittest.TestCase):
    def test_video_ready_independent_of_acquisition_and_transcription(self):
        core, source, outbox = Mock(), Mock(), Mock()
        data = {'sources': [{'binding_id': 'binding', 'generation': 1}],
            'assets': [{'id': 'video', 'kind': 'video', 'state': 'ready', 'metadata': {'duration': 10}}],
            'transcripts': [], 'channels': [{'video_status': 'ready', 'transcript_status': 'processing',
                'acquisition_complete': False, 'transcription_status': 'awaiting_configuration'}]}
        core.materials.return_value = data
        core.access.return_value = {'id': 'video', 'storage': 'remote', 'binding_id': 'binding', 'external_id': 'external-video'}
        source.access.return_value = {'url': 'https://example.test/temporary', 'expires': 1800001000}
        result = CoreMaterialsCatalog(core, source, outbox).legacy_materials('interview', {'id': 'owner'})
        self.assertEqual(result['video']['status'], 'ready')
        self.assertEqual(result['transcription']['status'], 'processing')
        self.assertEqual(result['recordings'][0]['id'], 'video')
        source.access.assert_called_once_with('binding', 'external-video')

    def test_revocation_during_remote_access_rejects_result(self):
        core, source, outbox = Mock(), Mock(), Mock()
        data = {'sources': [{'binding_id': 'binding', 'generation': 1}],
            'assets': [{'id': 'video', 'kind': 'video', 'state': 'ready'}]}
        core.materials.side_effect = [data, {'sources': [{'binding_id': 'binding', 'generation': 2, 'revoked': True}]}]
        core.access.return_value = {'storage': 'remote', 'binding_id': 'binding', 'external_id': 'external'}
        source.access.return_value = {'url': 'https://example.test/temporary'}
        with self.assertRaises(Problem) as caught:
            CoreMaterialsCatalog(core, source, outbox).legacy_materials('interview', {'id': 'owner'})
        self.assertEqual(caught.exception.status, 409)

    def test_remote_material_error_is_not_hidden_as_success(self):
        core, source, outbox = Mock(), Mock(), Mock()
        core.materials.side_effect = ServiceError('materials_service_unavailable', 503)
        with self.assertRaises(ServiceError):
            CoreMaterialsCatalog(core, source, outbox).legacy_materials('interview', {'id': 'owner'})

    def test_failed_video_access_preserves_available_transcript_and_reports_channel_error(self):
        core, source, outbox = Mock(), Mock(), Mock()
        core.materials.return_value = {'sources': [{'binding_id': 'binding', 'generation': 1}],
            'assets': [{'id': 'video', 'kind': 'video', 'state': 'ready'}],
            'transcripts': [{'id': 'text', 'asset_id': 'transcript', 'version': 1,
                'segments': [{'text': 'Synthetic transcript'}],
                'metadata': {'normalized': {'full_text': 'Synthetic transcript'}}}],
            'channels': [{'video_status': 'ready', 'transcript_status': 'ready'}]}
        core.access.side_effect = ServiceError('materials_service_unavailable', 503)
        result = CoreMaterialsCatalog(core, source, outbox).legacy_materials('interview', {'id': 'owner'})
        self.assertEqual(result['video']['status'], 'error')
        self.assertIn('error', result['video'])
        self.assertEqual(result['transcription']['status'], 'ready')
        self.assertEqual(result['transcripts'][0]['text'], 'Synthetic transcript')


class ServicesWebhookRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory(prefix='services-webhook-route-')
        cls.secret = b'synthetic-webhook-hmac-key-only'
        cls.requests = []
        cls.failure = False
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                cls.requests.append((self.path, raw, dict(self.headers)))
                timestamp, signature = self.headers.get('X-Webhook-Timestamp'), self.headers.get('X-Webhook-Signature')
                expected = base64.b64encode(hmac.digest(cls.secret, timestamp.encode() + b'.' + raw, hashlib.sha256)).decode()
                valid = hmac.compare_digest(expected, signature)
                status = 503 if cls.failure else 200 if valid else 401
                body = json.dumps({'detail': 'private upstream text must never leak'} if cls.failure
                    else {'accepted': True, 'duplicate': False} if valid else {'detail': 'bad_signature'}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.env = patch.dict(os.environ, {'HR_DATA_DIR': cls.data.name,
            'HR_TEAM_KEY': 'synthetic-team-key', 'HR_VIDEO_ENABLED': '0', 'HR_MATERIALS_ENABLED': '1',
            'HR_MATERIALS_SERVICES_ENABLED': '1', 'HR_MATERIALS_CONNECTION_ID': 'daily',
            'HR_MATERIALS_CORE_URL': 'http://127.0.0.1:8180', 'HR_MATERIALS_CORE_KEY': 'synthetic-core-key',
            'HR_MATERIALS_SOURCE_URL': 'http://127.0.0.1:' + str(cls.upstream.server_port),
            'HR_MATERIALS_SOURCE_KEY': 'synthetic-source-key', 'HR_NOTIFICATIONS_ENABLED': '0',
            'HR_HH_ENABLED': '0', 'HR_ORIGIN': 'https://test.invalid', 'HR_RECRUITER_ORIGIN': 'https://test.invalid'})
        cls.env.start()
        spec = importlib.util.spec_from_file_location('services_webhook_test_app', Path(__file__).with_name('app.py'))
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), cls.app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.env.stop()
        cls.data.cleanup()

    def post(self, raw, *, invalid=False):
        timestamp = str(int(time.time()))
        signature = base64.b64encode(hmac.digest(self.secret, timestamp.encode() + b'.' + raw, hashlib.sha256)).decode()
        if invalid:
            signature = 'invalid-synthetic-signature'
        headers = {'Content-Type': 'application/json', 'X-Webhook-Timestamp': timestamp,
            'X-Webhook-Signature': signature, 'Authorization': 'Bearer untrusted-inbound-token',
            'Cookie': 'private-inbound-cookie'}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('POST', '/api/hr/materials/webhooks/daily', body=raw, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), headers
        finally:
            connection.close()

    def test_original_bytes_and_hmac_are_forwarded_without_browser_credentials(self):
        raw = b'{ "test" : "test", "unicode" : "\\u043f" }\n'
        status, result, headers = self.post(raw)
        self.assertEqual(status, 200, result)
        self.assertTrue(result['accepted'])
        path, received, upstream_headers = self.requests[-1]
        upstream_headers = {k.lower(): v for k, v in upstream_headers.items()}
        self.assertEqual(path, '/v1/webhooks/daily')
        self.assertEqual(received, raw)
        self.assertEqual(upstream_headers['x-webhook-timestamp'], headers['X-Webhook-Timestamp'])
        self.assertEqual(upstream_headers['x-webhook-signature'], headers['X-Webhook-Signature'])
        self.assertEqual(upstream_headers['authorization'], 'Bearer synthetic-source-key')
        self.assertNotIn('cookie', upstream_headers)
        self.assertIsNone(self.app.MATERIALS_RUNTIME)

    def test_invalid_hmac_upstream_rejection_is_not_acknowledged(self):
        status, result, _ = self.post(b'{ "test" : "test" }', invalid=True)
        self.assertEqual(status, 401)
        self.assertNotIn('accepted', result)

    def test_upstream_failure_is_safe_and_provider_retry_can_then_succeed(self):
        type(self).failure = True
        try:
            status, result, _ = self.post(b'{ "test" : "test" }')
        finally:
            type(self).failure = False
        self.assertEqual(status, 503)
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('accepted', result)
        status, result, _ = self.post(b'{ "test" : "test" }')
        self.assertEqual(status, 200)
        self.assertTrue(result['accepted'])


if __name__ == '__main__':
    unittest.main()
