"""Offline acceptance tests. No Zoom HTTP calls or external mutations."""
import base64
import hashlib
import hmac
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

import backend


VALUES = {name: "fixture-" + name.lower() for name in backend.SDK_KEYS + backend.REST_KEYS}


class FakeZoom:
    def __init__(self):
        self.created, self.zaks, self.reads, self.downloads = 0, 0, 0, 0
        self.fail_create = False
        self.files = []

    def create(self, topic):
        self.created += 1
        if self.fail_create:
            raise backend.ApiError("Fixture timeout", 502, "provider_unavailable")
        return {"number": "12345678901", "password": "fixture-passcode", "uuid": "fixture-uuid"}

    def zak(self):
        self.zaks += 1
        return "fixture-host-zak"

    def recordings(self, meeting):
        self.reads += 1
        return {"uuid": "fixture-recording-uuid", "recording_files": self.files}

    def download(self, url, target, expected_size=0):
        self.downloads += 1
        target.write_bytes(b"fixture-video")
        return target.stat().st_size


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = backend.Config(VALUES)
        self.provider = FakeZoom()
        self.app = backend.App(self.config, Path(self.temp.name) / "private", self.provider)
        self.server = backend.http.server.ThreadingHTTPServer(("127.0.0.1", 0), backend.make_handler(self.app))
        self.port = self.server.server_address[1]
        self.config.origin = f"http://127.0.0.1:{self.port}"
        self.config.host = f"127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, cookies="", origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        headers = {"Origin": self.config.origin if origin is None else origin}
        if cookies:
            headers["Cookie"] = cookies
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, None if body is None else json.dumps(body), headers)
        response = conn.getresponse()
        raw, status = response.read(), response.status
        cookie = response.getheader("Set-Cookie", "")
        conn.close()
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw.decode()
        return status, value, cookie

    def host(self):
        status, result, cookie = self.request("POST", "/api/session", {"hostCapability": self.app.host_capability})
        self.assertEqual(status, 200)
        self.assertTrue(result["hostSession"])
        self.assertIn("HttpOnly; SameSite=Strict", cookie)
        return cookie.split(";", 1)[0]

    def create(self):
        host = self.host()
        status, result, _ = self.request("POST", "/api/meetings", {"requestId": "fixture-request-123"}, host)
        self.assertEqual(status, 200, result)
        return host, result

    def guest(self, created):
        data = urllib.parse.parse_qs(urllib.parse.urlsplit(created["guestUrl"]).fragment)
        local_id = created["meeting"]["id"]
        status, _, cookie = self.request("POST", f"/api/meetings/{local_id}/session",
                                        {"guestCapability": data["guest"][0]})
        self.assertEqual(status, 200)
        self.assertIn("Path=/api/meetings/" + local_id, cookie)
        self.assertNotIn(backend.HOST_COOKIE, cookie)
        return cookie.split(";", 1)[0]

    def test_configuration_has_no_secret_values_and_startup_no_provider_calls(self):
        status, value, _ = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertTrue(value["configured"])
        self.assertFalse(value["hostSession"])
        self.assertEqual(self.provider.created + self.provider.zaks + self.provider.reads, 0)
        output = json.dumps(value)
        for key in ("ZOOM_MEETING_SDK_CLIENT_SECRET", "ZOOM_S2S_CLIENT_SECRET", "ZOOM_S2S_ACCOUNT_ID"):
            self.assertNotIn(VALUES[key], output)

    def test_host_and_guest_in_same_browser_have_independent_roles(self):
        host, created = self.create()
        guest = self.guest(created)
        both = host + "; " + guest
        local_id = created["meeting"]["id"]
        for mode, role in (("guest", 0), ("host", 1), ("guest", 0)):
            status, result, _ = self.request("POST", f"/api/meetings/{local_id}/join?mode={mode}",
                                            {"displayName": "Тестовый участник"}, both)
            self.assertEqual(status, 200, result)
            self.assertEqual(result["role"], role)
            self.assertEqual("zak" in result, role == 1)
            self.assertEqual(decode_claims(result["signature"])["role"], role)
        self.assertEqual(self.provider.zaks, 1)

    def test_host_cookie_does_not_authorize_guest_mode_and_guest_cannot_host(self):
        host, created = self.create()
        guest = self.guest(created)
        local_id = created["meeting"]["id"]
        for mode, cookie in (("guest", host), ("host", guest)):
            status, _, _ = self.request("POST", f"/api/meetings/{local_id}/join?mode={mode}",
                                        {"displayName": "Test"}, cookie)
            self.assertEqual(status, 401)
        self.assertEqual(self.provider.zaks, 0)
        status, _, _ = self.request("GET", f"/api/meetings/{local_id}/recordings", cookies=guest)
        self.assertEqual(status, 401)

    def test_role_and_foreign_meeting_cannot_be_supplied_in_body(self):
        host, created = self.create()
        local_id = created["meeting"]["id"]
        for body in ({"displayName": "Test", "role": 1}, {"displayName": "Test", "meetingNumber": "98765432101"}):
            status, _, _ = self.request("POST", f"/api/meetings/{local_id}/join?mode=host", body, host)
            self.assertEqual(status, 400)
        status, _, _ = self.request("POST", f"/api/meetings/{local_id}/join", {"displayName": "Test"}, host)
        self.assertEqual(status, 400)

    def test_create_idempotency_and_private_invite_reload(self):
        host, created = self.create()
        status, again, _ = self.request("POST", "/api/meetings", {"requestId": "fixture-request-123"}, host)
        self.assertEqual(status, 200)
        self.assertEqual(created, again)
        self.assertEqual(self.provider.created, 1)
        local_id = created["meeting"]["id"]
        status, loaded, _ = self.request("GET", f"/api/meetings/{local_id}?mode=host", cookies=host)
        self.assertEqual(status, 200)
        self.assertEqual(loaded["guestUrl"], created["guestUrl"])
        guest = self.guest(created)
        status, loaded, _ = self.request("GET", f"/api/meetings/{local_id}?mode=guest", cookies=guest)
        self.assertEqual(status, 200)
        self.assertNotIn("guestUrl", loaded)
        self.assertNotIn("password", loaded["meeting"])

    def test_unknown_creation_outcome_never_retries_mutation(self):
        host = self.host()
        self.provider.fail_create = True
        status, _, _ = self.request("POST", "/api/meetings", {"requestId": "uncertain-request"}, host)
        self.assertEqual(status, 502)
        status, result, _ = self.request("POST", "/api/meetings", {"requestId": "uncertain-request"}, host)
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "creation_unconfirmed")
        self.assertEqual(self.provider.created, 1)

    def test_foreign_origin_cannot_exchange_or_create(self):
        status, _, _ = self.request("POST", "/api/session", {"hostCapability": self.app.host_capability},
                                    origin="https://untrusted.example")
        self.assertEqual(status, 403)
        self.assertEqual(self.provider.created, 0)

    def test_missing_configuration_never_creates_fixture_meeting(self):
        self.config.values = {}
        host = self.host()
        status, result, _ = self.request("POST", "/api/meetings", {"requestId": "fixture-request-123"}, host)
        self.assertEqual(status, 503)
        self.assertEqual(result["code"], "not_configured")
        self.assertEqual(self.provider.created, 0)

    def test_recording_absent_processing_ready_and_private_download(self):
        host, created = self.create()
        local_id = created["meeting"]["id"]
        path = f"/api/meetings/{local_id}/recordings"
        status, value, _ = self.request("GET", path, cookies=host)
        self.assertEqual((status, value["status"]), (200, "absent"))
        self.provider.files = [{"id": "recording-fixture-123", "file_type": "MP4", "file_extension": "MP4",
                               "status": "processing", "file_size": 13,
                               "download_url": "https://zoom.us/private-fixture?access_token=DO-NOT-RETURN"}]
        status, value, _ = self.request("GET", path, cookies=host)
        self.assertEqual(value["status"], "processing")
        self.assertNotIn("download_url", json.dumps(value))
        self.assertNotIn("DO-NOT-RETURN", json.dumps(value))
        status, _, _ = self.request("POST", path + "/recording-fixture-123/download", {}, host)
        self.assertEqual(status, 409)
        self.provider.files[0]["status"] = "completed"
        status, value, _ = self.request("GET", path, cookies=host)
        self.assertEqual(value["status"], "ready")
        status, value, _ = self.request("POST", path + "/recording-fixture-123/download", {}, host)
        self.assertEqual((status, value["status"]), (200, "downloaded"))
        self.assertTrue(Path(value["localPath"]).is_relative_to(self.app.recordings_dir))
        self.assertTrue(Path(value["localPath"]).is_file())
        self.assertNotIn("DO-NOT-RETURN", json.dumps(value))
        self.request("POST", path + "/recording-fixture-123/download", {}, host)
        self.assertEqual(self.provider.downloads, 1)

    def test_private_files_permissions_and_not_served(self):
        for name in ("host-capability", "host-invite.txt", "state.sqlite3"):
            self.assertEqual((self.app.state_dir / name).stat().st_mode & 0o777, 0o600)
        status, _, _ = self.request("GET", "/../.local/host-capability")
        self.assertEqual(status, 404)

    def test_root_meeting_query_survives_reload_and_unknown_query_is_rejected(self):
        _, created = self.create()
        local_id = created["meeting"]["id"]
        for path in ("/", "/index.html"):
            status, html, _ = self.request("GET", f"{path}?meeting={local_id}&mode=host")
            self.assertEqual(status, 200)
            self.assertIn("<html", html)
        for query in ("unknown=value", "mode=admin", "mode=guest&mode=host", "meeting=invalid", "left=1"):
            status, _, _ = self.request("GET", "/?" + query)
            self.assertEqual(status, 404)


def decode_claims(signature):
    value = signature.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))


class ProviderTests(unittest.TestCase):
    def test_jwt_current_claims_signature_and_minimum_ttl(self):
        signature = backend.sdk_signature("client-id", "client-secret", "12345678901", 0, now=100000)
        claims = decode_claims(signature)
        self.assertEqual(claims, {"appKey": "client-id", "mn": "12345678901", "role": 0,
                                  "iat": 99970, "exp": 107170, "tokenExp": 107170})
        signing_input, actual = signature.rsplit(".", 1)
        expected = backend.b64(hmac.new(b"client-secret", signing_input.encode(), hashlib.sha256).digest())
        self.assertEqual(actual, expected)
        self.assertGreaterEqual(claims["exp"] - claims["iat"], 1800)

    def test_provider_create_never_enables_auto_recording(self):
        provider = backend.ZoomProvider(backend.Config(VALUES))
        captured = []
        def request(path, body=None):
            captured.append((path, body))
            return {"id": 12345678901, "password": "test", "settings": {"auto_recording": "none"}}
        provider.request = request
        provider.create("Test only")
        self.assertEqual(captured[0][1]["settings"]["auto_recording"], "none")
        self.assertTrue(captured[0][1]["settings"]["waiting_room"])
        self.assertFalse(captured[0][1]["settings"]["join_before_host"])

    def test_zak_uses_explicit_type_for_configured_host(self):
        provider = backend.ZoomProvider(backend.Config(VALUES))
        with patch.object(provider, "request", return_value={"token": "fixture"}) as request:
            self.assertEqual(provider.zak(), "fixture")
            self.assertTrue(request.call_args[0][0].endswith("/token?type=zak"))

    def test_provider_errors_do_not_echo_bodies(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://zoom.us/private", 403, "secret reason", {},
                                                       io.BytesIO(b'{"secret":"NEVER OUTPUT"}'))
        provider = backend.ZoomProvider(backend.Config(VALUES), opener)
        with self.assertRaises(backend.ApiError) as caught:
            provider._json(urllib.request.Request("https://zoom.us/fixture"))
        self.assertEqual(caught.exception.code, "zoom_http_403")
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("NEVER", str(caught.exception))

    def test_download_rejects_non_zoom_origin_before_any_authenticated_request(self):
        opener = unittest.mock.Mock()
        provider = backend.ZoomProvider(backend.Config(VALUES), opener)
        provider.token = lambda: "fixture-token"
        with tempfile.TemporaryDirectory() as folder:
            for url in ("https://evil.example/file", "http://zoom.us/file", "https://zoom.us.evil.example/file"):
                with self.assertRaises(backend.ApiError) as caught:
                    provider.download(url, Path(folder) / "file.mp4")
                self.assertEqual(caught.exception.code, "download_origin")
        opener.open.assert_not_called()

    def test_recording_response_must_match_exact_meeting(self):
        provider = backend.ZoomProvider(backend.Config(VALUES))
        with patch.object(provider, "request", return_value={"id": 98765432101, "recording_files": []}):
            with self.assertRaises(backend.ApiError):
                provider.recordings({"number": "12345678901"})

    def test_recording_uuid_double_encoding(self):
        provider = backend.ZoomProvider(backend.Config(VALUES))
        with patch.object(provider, "request", return_value={"id": 12345678901, "uuid": "/uuid//test", "recording_files": []}) as request:
            provider.recordings({"number": "12345678901", "recording_uuid": "/uuid//test"})
            self.assertIn("%252Fuuid%252F%252Ftest", request.call_args[0][0])

    def test_env_reader_never_executes_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env.local"
            path.write_text("ZOOM_MEETING_SDK_CLIENT_SECRET='$(touch should-not-exist)'\n")
            self.assertEqual(backend.read_env(path)["ZOOM_MEETING_SDK_CLIENT_SECRET"], "$(touch should-not-exist)")


if __name__ == "__main__":
    unittest.main()
