"""Security and byte-range regression tests. All upstream data is synthetic."""

import email.message
import http.client
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError


SCRIPT = Path(__file__).with_name("serve-assessment-preview.py")
SPEC = importlib.util.spec_from_file_location("assessment_preview_server", SCRIPT)
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class Response(io.BytesIO):
    def __init__(self, body, status=206, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = email.message.Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self.read_sizes = []

    def read(self, amount=-1):
        if amount < 0 or amount > preview.CHUNK_SIZE:
            raise AssertionError("Unbounded or oversized read")
        self.read_sizes.append(amount)
        return super().read(amount)


class FakeOpener:
    def __init__(self):
        self.requests = []
        self.factory = lambda: Response(b"cdef", headers={
            "Content-Length": "4", "Content-Range": "bytes 2-5/10",
            "Content-Type": "video/mp4", "Accept-Ranges": "bytes",
        })

    def open(self, request, timeout):
        self.requests.append(request)
        return self.factory()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "resumes").mkdir()
        (self.root / "resumes/one.pdf").write_bytes(b"%PDF-0123456789")
        (self.root / "assessment.json").write_text('{"version":1}')
        (self.root / "candidates.json").write_text(json.dumps({"candidates": [{
            "id": "one", "videoUrl": f"https://{preview.VIDEO_HOST}/api/video/one",
            "resumePath": "/must/not/be/used.pdf",
        }]}))
        self.opener = FakeOpener()
        self.server = preview.PreviewServer(self.root, 0, self.opener)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path="/assessment", method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_binding_and_reloaded_private_json(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.request()[2], b'{"version":1}')
        (self.root / "assessment.json").write_text('{"version":2}')
        status, headers, body = self.request(headers={"Origin": "http://127.0.0.1:6032"})
        self.assertEqual((status, body), (200, b'{"version":2}'))
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://127.0.0.1:6032")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Credentials", headers)

    def test_foreign_origins_hosts_and_no_cors_embed_denied(self):
        for headers in ({"Origin": "https://evil.test"}, {"Origin": "null"},
                        {"Origin": "http://127.0.0.1:9999"}, {"Host": "evil.test"},
                        {"Host": "127.0.0.1.evil.test"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(headers=headers)[0], 403)

    def test_only_allowlisted_routes_and_read_methods(self):
        for path in ("/", "/candidates.json", "/assessment?file=secret", "/resume/other.pdf",
                     "/video/other", "/resume/../assessment.json", "/resume/%2e%2e.pdf"):
            self.assertEqual(self.request(path)[0], 404)
        for method in ("POST", "DELETE", "PATCH", "PUT"):
            self.assertEqual(self.request(method=method)[0], 405)
        self.assertEqual(len(self.opener.requests), 0)

    def test_preflight_is_scoped(self):
        origin = {"Origin": "http://localhost:6032", "Access-Control-Request-Method": "GET",
                  "Access-Control-Request-Headers": "Range, If-Range"}
        self.assertEqual(self.request("/video/one", "OPTIONS", origin)[0], 204)
        self.assertEqual(self.request("/unknown", "OPTIONS", origin)[0], 404)
        self.assertEqual(self.request(method="OPTIONS", headers={**origin, "Access-Control-Request-Method": "POST"})[0], 403)
        self.assertEqual(self.request(method="OPTIONS", headers={**origin, "Access-Control-Request-Headers": "Authorization"})[0], 403)

    def test_pdf_ranges_head_and_symlinks(self):
        status, headers, body = self.request("/resume/one.pdf", headers={"Range": "bytes=0-3"})
        self.assertEqual((status, body, headers["Content-Range"]), (206, b"%PDF", "bytes 0-3/15"))
        self.assertEqual(self.request("/resume/one.pdf", headers={"Range": "bytes=-4"})[2], b"6789")
        status, headers, body = self.request("/resume/one.pdf", "HEAD")
        self.assertEqual((status, headers["Content-Length"], body), (200, "15", b""))
        status, headers, _ = self.request("/resume/one.pdf", headers={"Range": "bytes=99-100"})
        self.assertEqual((status, headers["Content-Range"]), (416, "bytes */15"))
        (self.root / "resumes/one.pdf").unlink()
        (self.root / "resumes/one.pdf").symlink_to(self.root / "assessment.json")
        self.assertEqual(self.request("/resume/one.pdf")[0], 403)

    def test_video_range_is_forwarded_and_response_preserved(self):
        status, headers, body = self.request("/video/one", headers={"Range": "bytes=2-5", "If-Range": '"test"'})
        self.assertEqual((status, body), (206, b"cdef"))
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")
        self.assertEqual(headers["Content-Length"], "4")
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        request = self.opener.requests[-1]
        self.assertEqual(request.full_url, f"https://{preview.VIDEO_HOST}/api/video/one")
        self.assertEqual(request.get_header("Range"), "bytes=2-5")
        self.assertEqual(request.get_header("If-range"), '"test"')
        self.assertEqual(request.get_header("Accept-encoding"), "identity")

    def test_video_head_and_rejected_range(self):
        self.assertEqual(self.request("/video/one", "HEAD")[2], b"")
        self.assertEqual(self.opener.requests[-1].method, "HEAD")
        count = len(self.opener.requests)
        for value in ("bytes=-", "bytes=0-1,3-4", "nonsense", "bytes=" + "1" * 100 + "-"):
            self.assertEqual(self.request("/video/one", headers={"Range": value})[0], 416)
        self.assertEqual(len(self.opener.requests), count)

    def test_stream_is_bounded_with_known_and_unknown_lengths(self):
        body = b"x" * (preview.CHUNK_SIZE * 3 + 9)
        response = Response(body, 200, {"Content-Length": str(len(body))})
        self.opener.factory = lambda: response
        self.assertEqual(self.request("/video/one")[2], body)
        self.assertGreater(len(response.read_sizes), 3)
        self.assertLessEqual(max(response.read_sizes), preview.CHUNK_SIZE)
        self.opener.factory = lambda: Response(body, 200)
        status, headers, received = self.request("/video/one")
        self.assertEqual((status, received, headers["Connection"]), (200, body, "close"))

    def test_upstream_errors_do_not_expose_or_follow_redirects(self):
        def error(code, headers=None):
            raise HTTPError("https://secret.invalid", code, "Private upstream", headers or {}, io.BytesIO(b"private secret"))
        self.opener.factory = lambda: error(416, {"Content-Range": "bytes */10"})
        status, headers, body = self.request("/video/one")
        self.assertEqual((status, headers["Content-Range"]), (416, "bytes */10"))
        self.assertNotIn(b"secret", body)
        for code in (302, 401, 500):
            self.opener.factory = lambda: error(code)
            self.assertEqual(self.request("/video/one")[0], 502)
        self.assertIsNone(preview.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_video_url_allowlist_cannot_be_bypassed(self):
        valid = f"https://{preview.VIDEO_HOST}/api/video/one"
        self.assertEqual(preview.approved_video_url("one", valid), valid)
        for url in ("http" + valid[5:], valid + "?url=http://127.0.0.1", valid + "#fragment",
                    valid.replace(preview.VIDEO_HOST, "evil.test"), valid.replace("/one", "/other"),
                    valid.replace(preview.VIDEO_HOST, "user:pass@" + preview.VIDEO_HOST),
                    valid.replace(preview.VIDEO_HOST, preview.VIDEO_HOST + ":444")):
            self.assertIsNone(preview.approved_video_url("one", url))
            self.server.candidates["one"]["videoUrl"] = url
            self.assertEqual(self.request("/video/one")[0], 404)
        self.assertEqual(len(self.opener.requests), 0)

    def test_slow_video_does_not_block_assessment(self):
        started, release = threading.Event(), threading.Event()
        def slow_response():
            started.set()
            release.wait(2)
            return Response(b"x", 200, {"Content-Length": "1"})
        self.opener.factory = slow_response
        result = []
        video = threading.Thread(target=lambda: result.append(self.request("/video/one")))
        video.start()
        self.assertTrue(started.wait(1))
        try:
            self.assertEqual(self.request()[0], 200)
        finally:
            release.set()
            video.join()
        self.assertEqual(result[0][0], 200)


if __name__ == "__main__":
    unittest.main()
