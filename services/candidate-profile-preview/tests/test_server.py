"""Isolated HTTP integration checks; never contact production or a model.

Run: python3 -m unittest discover -s services/candidate-profile-preview/tests -v
"""
from __future__ import annotations

import copy
import concurrent.futures
import contextlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import threading
import time
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = 'preview-test-secret-never-log'
PREFIX = '/candidate-preview/api'


class FakeUpstream(BaseHTTPRequestHandler):
    requests = []
    mode = 'normal'
    payload = {'candidates': [{'id': 'candidate_real_123', 'about': None}],
               'nextOffset': 500, 'total': 801, 'offset': 0, 'limit': 500}

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).requests.append((self.path, self.headers.get('Authorization')))
        mode = type(self).mode
        if mode == 'timeout':
            time.sleep(.7)
        if mode == 'disconnect':
            self.connection.close()
            return
        status = {'forbidden': 403, 'error': 503, 'redirect': 302}.get(mode, 200)
        data = json.dumps({'error': 'manager_required'} if mode == 'forbidden'
                          else {'error': 'temporarily_unavailable'} if mode == 'error'
                          else type(self).payload).encode()
        if mode == 'invalid_json':
            data = b'not JSON'
        if mode == 'large':
            data = b'"' + b'x' * 100000 + b'"'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        if mode == 'redirect':
            self.send_header('Location', '/redirect-secret-target')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class PreviewIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), FakeUpstream)
        cls.upstream.daemon_threads = True
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.logs = io.StringIO()
        cls.static_root = tempfile.TemporaryDirectory()
        Path(cls.static_root.name, 'index.html').write_text('<main>Preview</main>')
        cls.module = cls.load_module()
        cls.preview = cls.make_preview()
        cls.preview.daemon_threads = True
        threading.Thread(target=cls.preview.serve_forever, daemon=True).start()

    @classmethod
    def load_module(cls):
        path = Path(__file__).resolve().parents[1] / 'server' / 'app.py'
        spec = importlib.util.spec_from_file_location('candidate_preview_server', path)
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module
        sys.path.insert(0, str(path.parent))
        spec.loader.exec_module(module)
        return module

    @classmethod
    def make_preview(cls):
        # Factory configured by the backend author; upstream is strictly loopback.
        config = cls.module.Config(host='127.0.0.1', port=0,
            upstream='http://127.0.0.1:%s' % cls.upstream.server_port,
            timeout=.2, max_body=1024, max_concurrency=1, dist=Path(cls.static_root.name))
        return cls.module.create_server(config)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.preview, cls.upstream):
            server.shutdown()
            server.server_close()
        cls.static_root.cleanup()

    def setUp(self):
        FakeUpstream.mode = 'normal'
        FakeUpstream.requests.clear()

    def request(self, path=PREFIX + '/candidates/candidate_real_123/readiness',
                method='GET', token=TOKEN, extra_headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.preview.server_port, timeout=3)
        headers = {'Host': '127.0.0.1:%s' % self.preview.server_port}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        headers.update(extra_headers or {})
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        response.close()
        connection.close()
        return result

    def assert_private_headers(self, headers):
        headers = {k.lower(): v for k, v in headers.items()}
        self.assertIn('no-store', headers.get('cache-control', ''))
        self.assertEqual(headers.get('referrer-policy'), 'no-referrer')

    def test_missing_auth_never_reaches_upstream(self):
        status, headers, _ = self.request(token=None)
        self.assertEqual(status, 401)
        self.assertEqual(FakeUpstream.requests, [])
        self.assert_private_headers(headers)

    def test_actual_json_and_bearer_passthrough(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), FakeUpstream.payload)
        self.assertEqual(FakeUpstream.requests,
                         [('/api/hr/selection/candidates/candidate_real_123/readiness', 'Bearer ' + TOKEN)])
        self.assert_private_headers(headers)

    def test_all_allowlisted_candidate_routes(self):
        for suffix in ('/readiness', '/required-fields'):
            with self.subTest(suffix=suffix):
                status, _, body = self.request(PREFIX + '/candidates/candidate_123' + suffix)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), FakeUpstream.payload)
                self.assertEqual(FakeUpstream.requests[-1][0],
                                 '/api/hr/selection/candidates/candidate_123' + suffix)

    def test_unconfigured_interviews_fail_closed(self):
        for suffix in ('/interviews', '/interviews/interview_456/materials'):
            self.assertEqual(self.request(PREFIX + '/candidates/candidate_123' + suffix)[0], 503)
        self.assertEqual(FakeUpstream.requests, [])

    def test_manager_authorization_failure_is_preserved(self):
        FakeUpstream.mode = 'forbidden'
        status, headers, body = self.request()
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {'error': 'manager_required'})
        self.assert_private_headers(headers)

    def test_upstream_error_status_and_json_are_preserved(self):
        FakeUpstream.mode = 'error'
        status, headers, body = self.request()
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {'error': 'temporarily_unavailable'})
        self.assert_private_headers(headers)

    def test_timeout_is_bounded(self):
        FakeUpstream.mode = 'timeout'
        start = time.monotonic()
        status, headers, _ = self.request()
        self.assertIn(status, (502, 504))
        self.assertLess(time.monotonic() - start, .65)
        self.assert_private_headers(headers)

    def test_upstream_disconnect_is_controlled(self):
        FakeUpstream.mode = 'disconnect'
        self.assertIn(self.request()[0], (502, 504))

    def test_redirect_is_rejected_without_following(self):
        FakeUpstream.mode = 'redirect'
        status, headers, _ = self.request()
        self.assertIn(status, (502, 503))
        self.assertEqual(len(FakeUpstream.requests), 1)
        self.assertNotIn('Location', headers)

    def test_response_body_limit(self):
        FakeUpstream.mode = 'large'
        status, headers, body = self.request()
        self.assertIn(status, (502, 503))
        self.assertLess(len(body), 10000)
        self.assert_private_headers(headers)

    def test_unsupported_methods_cannot_reach_upstream(self):
        for method in ('POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'):
            with self.subTest(method=method):
                self.assertIn(self.request(method=method)[0], (404, 405))
        self.assertEqual(FakeUpstream.requests, [])

    def test_queries_routes_and_encoded_traversal_are_rejected(self):
        paths = [
            '/candidates?offset=-1', '/candidates?limit=0', '/candidates?limit=501',
            '/candidates?offset=abc', '/candidates?offset=0&offset=1',
            '/candidates?upstream=http://evil.invalid', '/candidates?token=' + TOKEN,
            '/candidates/candidate_123/readiness?unexpected=1', '/unknown',
            '/candidates/../readiness', '/candidates/%2e%2e/readiness',
            '/candidates/%252e%252e/readiness', '/candidates/a%2fb/readiness',
            '/candidates/a%5cb/readiness', '/candidates/a%00b/readiness',
            '/candidates/a/interviews/b/materials/extra',
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(self.request(PREFIX + path)[0], (400, 404))
        self.assertEqual(FakeUpstream.requests, [])

    def test_foreign_origin_and_cross_site_are_rejected(self):
        for headers in ({'Origin': 'https://evil.invalid'}, {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(extra_headers=headers)[0], 403)
        self.assertEqual(FakeUpstream.requests, [])

    def test_malformed_upstream_json_is_controlled(self):
        FakeUpstream.mode = 'invalid_json'
        status, headers, _ = self.request()
        self.assertEqual(status, 502)
        self.assert_private_headers(headers)

    def test_concurrent_upstream_requests_are_bounded(self):
        FakeUpstream.mode = 'timeout'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.request)
            deadline = time.monotonic() + 1
            while not FakeUpstream.requests and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(self.request()[0], 503)
            self.assertEqual(first.result()[0], 502)
        self.assertEqual(len(FakeUpstream.requests), 1)

    def test_static_is_scoped_and_traversal_is_blocked(self):
        status, headers, body = self.request('/candidate-preview/', token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'<main>Preview</main>')
        self.assert_private_headers(headers)
        for path in ('/', '/index.html', '/candidate-preview/../docs/CONTRACT.md',
                     '/candidate-preview/%2e%2e/docs/CONTRACT.md',
                     '/candidate-preview/%252e%252e/docs/CONTRACT.md',
                     '/candidate-preview/%2fetc/passwd'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, token=None)[0], 404)
        self.assertEqual(FakeUpstream.requests, [])

    def test_health_is_public_and_does_not_proxy(self):
        status, headers, body = self.request(PREFIX + '/health', token=None)
        self.assertEqual(status, 200)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertEqual(FakeUpstream.requests, [])
        self.assert_private_headers(headers)

    def test_no_secret_in_logs_or_error_response(self):
        with contextlib.redirect_stderr(self.logs), contextlib.redirect_stdout(self.logs):
            status, _, body = self.request(PREFIX + '/candidates?token=' + TOKEN)
            self.assertIn(status, (400, 404))
            self.assertNotIn(TOKEN.encode(), body)
        self.assertNotIn(TOKEN, self.logs.getvalue())


class FakeCanonical(BaseHTTPRequestHandler):
    requests = []
    mode = 'normal'
    calls = 0
    materials = {'owner_kind': 'interview', 'owner_id': 'iid',
        'sources': [{'id': 'src', 'generation': 1}],
        'assets': [{'id': 'audio1', 'source_id': 'src', 'kind': 'audio',
                    'state': 'ready', 'metadata': {'part_index': 0, 'duration': 30}}],
        'transcripts': [], 'channels': [{'video_status': 'ready', 'transcript_status': 'processing'}]}

    def log_message(self, *args):
        pass

    def do_GET(self):
        cls = type(self)
        cls.requests.append((self.path, self.headers.get('Authorization')))
        status = 200
        materials = copy.deepcopy(cls.materials)
        if cls.mode == 'bad_generation':
            materials['transcripts'] = [{'id': 'transcript1', 'asset_id': 'audio1',
                'source_id': 'src', 'version': 1, 'segments': []}]
            materials['transcript_selections'] = [{'recording_id': 'audio1',
                'transcript_id': 'transcript1', 'status': 'ready', 'generation': 999, 'revision': 1}]
        if self.path.startswith('/api/hr/selection/candidates?'):
            cls.calls += 1
            if cls.mode == 'forbidden' or (cls.mode == 'revoked' and cls.calls > 1):
                status, data = 403, {'error': 'denied'}
            else:
                data = {'candidates': [{'id': 'cid'}], 'count': 1, 'offset': 0,
                        'limit': 500, 'nextOffset': None}
        elif self.path == '/v1/candidates/cid/dossier':
            data = {'candidate_id': 'wrong' if cls.mode == 'mismatch' else 'cid',
                    'interviews': [{'id': 'iid', 'status': 'completed', 'materials': materials}]}
        elif self.path == '/v1/owners/interview/iid/materials':
            data = materials
        elif self.path == '/v1/assets/audio1/access':
            data = {'id': 'audio1', 'storage': 'owned',
                    'url': 'https://media.example.invalid/audio1?signature=private', 'expires': 12345}
        else:
            status, data = 404, {'error': 'unexpected_route'}
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AdapterHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = PreviewIntegrationTests.load_module()
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), FakeCanonical)
        cls.upstream.daemon_threads = True
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        origin = 'http://127.0.0.1:%s' % cls.upstream.server_port
        cls.preview = cls.module.create_server(cls.module.Config(port=0, upstream=origin,
            core_url=origin, core_key='core-test-secret', timeout=.3, max_body=100000))
        threading.Thread(target=cls.preview.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.preview, cls.upstream):
            server.shutdown()
            server.server_close()

    def setUp(self):
        FakeCanonical.mode = 'normal'
        FakeCanonical.calls = 0
        FakeCanonical.requests.clear()

    request = PreviewIntegrationTests.request

    def test_list_reads_canonical_dossier_with_fresh_membership(self):
        status, headers, body = self.request(PREFIX + '/candidates/cid/interviews')
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertEqual(result['interviews'][0]['parts'][0]['kind'], 'audio')
        self.assertEqual(FakeCanonical.calls, 2)
        self.assertNotIn(b'core-test-secret', body)
        self.assertNotIn(b'signature=', body)
        self.assertTrue(all(auth == ('Bearer ' + TOKEN if path.startswith('/api/')
                                   else 'Bearer core-test-secret')
                            for path, auth in FakeCanonical.requests))

    def test_materials_return_owned_audio_and_recheck_membership(self):
        status, _, body = self.request(PREFIX + '/candidates/cid/interviews/iid/materials')
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertEqual(result['recordings'][0]['id'], 'audio1')
        self.assertEqual(result['recordings'][0]['kind'], 'audio')
        self.assertEqual(result['transcripts'], [])
        self.assertGreaterEqual(FakeCanonical.calls, 5)
        self.assertTrue(all(path.startswith(('/api/hr/selection/candidates?', '/v1/'))
                            for path, _ in FakeCanonical.requests))

    def test_wrong_role_blocks_all_core_reads(self):
        FakeCanonical.mode = 'forbidden'
        self.assertEqual(self.request(PREFIX + '/candidates/cid/interviews')[0], 403)
        self.assertTrue(all(path.startswith('/api/') for path, _ in FakeCanonical.requests))

    def test_midflight_membership_revocation_blocks_response(self):
        FakeCanonical.mode = 'revoked'
        status, _, body = self.request(PREFIX + '/candidates/cid/interviews/iid/materials')
        self.assertEqual(status, 403)
        self.assertNotIn(b'signature=', body)
        self.assertFalse(any('/assets/' in path for path, _ in FakeCanonical.requests))

    def test_canonical_candidate_mismatch_fails_closed(self):
        FakeCanonical.mode = 'mismatch'
        self.assertEqual(self.request(PREFIX + '/candidates/cid/interviews')[0], 502)

    def test_mismatched_transcript_generation_fails_closed(self):
        FakeCanonical.mode = 'bad_generation'
        status, _, body = self.request(PREFIX + '/candidates/cid/interviews/iid/materials')
        self.assertEqual(status, 502, body)
        self.assertNotIn(b'signature=', body)

    def test_interview_not_owned_by_candidate_returns_404(self):
        self.assertEqual(self.request(PREFIX + '/candidates/cid/interviews/other/materials')[0], 404)
        self.assertFalse(any('/assets/' in path for path, _ in FakeCanonical.requests))

class FakeDirectory(BaseHTTPRequestHandler):
    mode = 'normal'
    rows = [{'id': 'c%s' % i, 'name': 'Actual %s' % i, 'coreRun': {'assessment': None}}
            for i in range(13)]
    calls = []
    directory_calls = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        from urllib.parse import urlsplit, parse_qs
        cls = type(self)
        cls.calls.append((self.path, self.headers.get('Authorization')))
        status = 200
        if self.path.startswith('/api/hr/selection/candidates?'):
            cls.directory_calls += 1
            query = parse_qs(urlsplit(self.path).query)
            offset, limit = int(query['offset'][0]), int(query['limit'][0])
            if cls.mode == 'revoke' and cls.directory_calls > 1:
                status, data = 403, {'error': 'revoked'}
            else:
                rows = cls.rows[offset:offset + limit]
                data = {'candidates': rows, 'count': len(cls.rows), 'offset': offset,
                        'limit': limit, 'nextOffset': offset + len(rows) if offset + len(rows) < len(cls.rows) else None}
        elif self.path.startswith('/v1/candidates/') and self.path.endswith('/dossier'):
            cid = self.path.split('/')[3]
            if cls.mode == 'core_failure' and cid == 'c4':
                status, data = 503, {'error': 'core_down'}
            else:
                materials = copy.deepcopy(FakeCanonical.materials)
                index = int(cid[1:])
                if index == 10:
                    materials['assets'] = []
                elif index == 11:
                    materials['sources'][0]['revoked'] = True
                elif index == 12:
                    materials['assets'][0]['state'] = 'processing'
                if cls.mode == 'source_mismatch' and index == 4:
                    materials['assets'][0]['source_id'] = 'not_this_source'
                data = {'candidate_id': cid, 'interviews': [{'id': 'iid',
                    'status': 'planned' if index == 9 else 'completed', 'materials': materials}]}
        else:
            status, data = 404, {'error': 'unexpected'}
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FilteredDirectoryHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = PreviewIntegrationTests.load_module()
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), FakeDirectory)
        cls.upstream.daemon_threads = True
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        origin = 'http://127.0.0.1:%s' % cls.upstream.server_port
        cls.preview = cls.module.create_server(cls.module.Config(port=0, upstream=origin,
            core_url=origin, core_key='core-test-secret', timeout=.3, max_body=100000))
        threading.Thread(target=cls.preview.serve_forever, daemon=True).start()

    tearDownClass = classmethod(AdapterHTTPTests.tearDownClass.__func__)
    request = PreviewIntegrationTests.request

    def setUp(self):
        FakeDirectory.mode = 'normal'
        FakeDirectory.directory_calls = 0
        FakeDirectory.calls.clear()

    def test_more_than_seven_actual_interviews_not_assessment_or_hardcoded_count(self):
        status, _, body = self.request(PREFIX + "/candidates?offset=0&limit=500")
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        ids = [row['id'] for row in data['candidates']]
        self.assertEqual(ids, ['c%s' % i for i in range(9)])
        self.assertEqual(data['count'], 9)
        self.assertIsNone(data['nextOffset'])
        self.assertNotIn('c9', ids)  # Planned interview does not qualify.
        self.assertNotIn('c10', ids)  # No recording does not qualify.
        self.assertNotIn('c11', ids)  # Revoked source does not qualify.
        self.assertNotIn('c12', ids)  # Processing recording does not qualify.

    def test_filtered_pagination_is_stable(self):
        ids = []
        for offset in (0, 4, 8):
            status, _, body = self.request(PREFIX + '/candidates?offset=%s&limit=4' % offset)
            self.assertEqual(status, 200, body)
            data = json.loads(body)
            self.assertEqual((data['offset'], data['limit'], data['count']), (offset, 4, 9))
            self.assertEqual(data['nextOffset'], offset + 4 if offset < 8 else None)
            ids.extend(row['id'] for row in data['candidates'])
        self.assertEqual(ids, ['c%s' % i for i in range(9)])
        self.assertEqual(len(ids), len(set(ids)))

    def test_full_upstream_directory_beyond_500_is_read_before_filtering(self):
        original = FakeDirectory.rows
        try:
            FakeDirectory.rows = [{'id': 'c%s' % i} for i in range(505)]
            status, _, body = self.request(PREFIX + '/candidates?offset=500&limit=500')
            self.assertEqual(status, 200, body)
            data = json.loads(body)
            self.assertEqual(data['count'], 501)
            self.assertEqual([row['id'] for row in data['candidates']], ['c504'])
            self.assertIsNone(data['nextOffset'])
            pages = [path for path, _ in FakeDirectory.calls if path.startswith('/api/')]
            self.assertEqual(pages, ['/api/hr/selection/candidates?offset=0&limit=500',
                                    '/api/hr/selection/candidates?offset=500&limit=500'] * 2)
        finally:
            FakeDirectory.rows = original

    def test_mismatched_source_invalidates_directory(self):
        FakeDirectory.mode = 'source_mismatch'
        status, _, body = self.request(PREFIX + '/candidates?offset=0&limit=500')
        self.assertEqual(status, 502, body)
        self.assertNotIn(b'\"candidates\"', body)

    def test_core_failure_is_not_silently_excluded(self):
        FakeDirectory.mode = 'core_failure'
        status, _, body = self.request(PREFIX + "/candidates?offset=0&limit=500")
        self.assertIn(status, (502, 503), body)
        self.assertNotIn(b'"candidates"', body)

    def test_access_revocation_during_directory_read_fails_closed(self):
        FakeDirectory.mode = 'revoke'
        status, _, body = self.request(PREFIX + "/candidates?offset=0&limit=500")
        self.assertEqual(status, 403, body)
        self.assertNotIn(b'"candidates"', body)


if __name__ == '__main__':
    unittest.main()
