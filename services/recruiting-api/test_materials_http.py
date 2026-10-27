import hashlib
import io
from pathlib import Path
import socket
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from materials_http import DownloadError, _PinnedAdapter, download, resolve_public, validate_url


class Reply:
    def __init__(self, body=b'abc', status=200, headers=None):
        self.status_code = status; self.headers = headers or {}; self.raw = io.BytesIO(body)
    def close(self): pass


class Session:
    replies = []; calls = []
    def __init__(self): self.headers = {}; self.trust_env = True
    def mount(self, *args): pass
    def get(self, url, **kw):
        self.calls.append((url, kw)); return self.replies.pop(0)
    def close(self): pass


class DownloadTests(unittest.TestCase):
    def setUp(self): Session.calls = []; Session.replies = []

    def fetch(self, replies, **kw):
        Session.replies = replies
        with tempfile.TemporaryDirectory() as tmp, patch('materials_http.requests.Session', Session), \
             patch('materials_http.resolve_public', return_value=(socket.AF_INET, ('8.8.8.8', 443))):
            path = Path(tmp) / 'file'
            result = download('https://media.example/file?secret=hidden', path, max_bytes=10, **kw)
            return result, path.read_bytes()

    def test_stream_verify_and_redirect_drops_all_source_credentials(self):
        from types import SimpleNamespace
        Session.replies = [Reply(status=302, headers={'Location': 'https://cdn.example/file'}), Reply(headers={'Content-Length': '3'})]
        with tempfile.TemporaryDirectory() as tmp, patch('materials_http.requests.Session', Session), \
             patch('materials_http.resolve_public', return_value=(socket.AF_INET, ('8.8.8.8', 443))):
            source = SimpleNamespace(url='https://media.example/file', headers={'Authorization': 'secret', 'X-API-Key': 'secret'}, expires_at=None)
            result = download(source, Path(tmp) / 'file', max_bytes=10, expected_size=3, expected_sha256=hashlib.sha256(b'abc').hexdigest())
        self.assertEqual(result.size, 3)
        self.assertNotIn('Authorization', Session.calls[1][1]['headers'])
        self.assertNotIn('X-API-Key', Session.calls[1][1]['headers'])
        self.assertFalse(Session.calls[0][1]['allow_redirects'])
        self.assertTrue(Session.calls[0][1]['stream'])

    def test_truncation_hash_size_and_oversize_never_publish_partial(self):
        for reply, kw in [(Reply(headers={'Content-Length': '4'}), {}), (Reply(), {'expected_size': 4}),
                          (Reply(), {'expected_sha256': '0' * 64}), (Reply(body=b'x'*11), {}),
                          (Reply(headers={'Content-Encoding': 'gzip'}), {}), (Reply(status=206), {})]:
            with self.subTest(kw=kw), self.assertRaises(DownloadError): self.fetch([reply], **kw)

    def test_failed_attempt_preserves_existing_destination_and_removes_stage(self):
        Session.replies = [Reply(body=b'bad', headers={'Content-Length': '4'})]
        with tempfile.TemporaryDirectory() as tmp, patch('materials_http.requests.Session', Session), \
             patch('materials_http.resolve_public', return_value=(socket.AF_INET, ('8.8.8.8', 443))):
            path = Path(tmp) / 'file'; path.write_bytes(b'old complete')
            with self.assertRaises(DownloadError): download('https://media.example/file', path, max_bytes=10)
            self.assertEqual(path.read_bytes(), b'old complete')
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_actual_connection_pins_numeric_address_without_dns(self):
        import time
        from unittest.mock import MagicMock
        adapter = _PinnedAdapter('media.example', (socket.AF_INET, ('8.8.8.8', 443)), time.monotonic() + 10)
        fake_socket = MagicMock()
        connection = adapter.pool.ConnectionCls('media.example', port=443)
        with patch('materials_http.socket.socket', return_value=fake_socket), \
             patch('materials_http.socket.getaddrinfo') as dns:
            self.assertIs(connection._new_conn(), fake_socket)
        dns.assert_not_called()
        fake_socket.connect.assert_called_once_with(('8.8.8.8', 443))
        self.assertEqual(connection.host, 'media.example')
        adapter.close()

    @unittest.skipUnless(shutil.which('openssl'), 'openssl required for synthetic TLS fixture')
    def test_real_requests_tls_stream_uses_original_hostname_and_pinned_endpoint(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '3')
                self.end_headers()
                if self.path == '/slow':
                    import time
                    try:
                        for byte in b'abc':
                            time.sleep(0.08)
                            self.wfile.write(bytes([byte])); self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                        pass
                else:
                    self.wfile.write(b'abc')
            def log_message(self, *args): pass
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = Path(tmp) / 'cert.pem', Path(tmp) / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-subj', '/CN=media.example', '-addext', 'subjectAltName=DNS:media.example',
                            '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
            observed = []
            context.set_servername_callback(lambda sock, name, ctx: observed.append(name))
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                # This fixture alone routes a validated public-name seam to a
                # synthetic local TLS server. Production still rejects local IPs.
                endpoint = (socket.AF_INET, ('127.0.0.1', server.server_port))
                with patch('materials_http.resolve_public', return_value=endpoint), \
                     patch('requests.adapters.DEFAULT_CA_BUNDLE_PATH', str(cert)):
                    result = download('https://media.example/synthetic', Path(tmp) / 'file', max_bytes=10, timeout_seconds=5)
                self.assertEqual(result.size, 3)
                self.assertEqual(result.path.read_bytes(), b'abc')
                self.assertEqual(observed, ['media.example'])
                import time
                started = time.monotonic()
                with patch('materials_http.resolve_public', return_value=endpoint), \
                     patch('requests.adapters.DEFAULT_CA_BUNDLE_PATH', str(cert)), self.assertRaises(DownloadError):
                    download('https://media.example/slow', Path(tmp) / 'slow', max_bytes=10, timeout_seconds=0.13)
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertFalse((Path(tmp) / 'slow').exists())
            finally:
                server.shutdown(); server.server_close(); thread.join(2)

    def test_each_redirect_resolves_and_checks_dns_again(self):
        Session.replies = [Reply(status=302, headers={'Location': 'https://other.example/a'})]
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with tempfile.TemporaryDirectory() as tmp, patch('materials_http.requests.Session', Session), \
             patch('materials_http.socket.getaddrinfo', side_effect=[public, private]) as dns:
            with self.assertRaises(DownloadError):
                download('https://media.example/a', Path(tmp) / 'file', max_bytes=10)
        self.assertEqual(dns.call_count, 2)
        self.assertEqual(len(Session.calls), 1)

    def test_ssrf_and_mixed_dns_denied(self):
        for value in ('http://8.8.8.8/a', 'https://user:password@host.example/a', 'https://127.0.0.1/a',
                      'https://[::ffff:8.8.8.8]/a', 'https://[64:ff9b::7f00:1]/a',
                      'https://metadata.google.internal/a', 'https://host.example:8443/a'):
            with self.subTest(value=value), self.assertRaises(DownloadError): validate_url(value)
        entries = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch('materials_http.socket.getaddrinfo', return_value=entries), self.assertRaises(DownloadError):
            resolve_public('public.example', 1)
        with self.assertRaises(DownloadError): self.fetch([Reply(status=302, headers={'Location':'https://169.254.169.254/latest'})])


if __name__ == '__main__':
    unittest.main()
