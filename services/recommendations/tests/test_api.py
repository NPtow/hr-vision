from copy import deepcopy
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from recommendations import api
from recommendations.engine import canonical_json
from recommendations.store import ResultStore
from fixtures import request_fixture, synthetic_rubric, set_distribution

TOKEN = "a1" * 32


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "private.db"
        self.store = ResultStore(self.path, rubric=synthetic_rubric())
        self.logs = []
        self.server = api.BoundedHTTPServer(("127.0.0.1", 0), api.handler(self.store, TOKEN, log=self.logs.append))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, value=None, *, token=TOKEN, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        actual_headers = {"Content-Type": "application/json"}
        if token is not None:
            actual_headers["Authorization"] = "Bearer " + token
        actual_headers.update(headers or {})
        body = canonical_json(value).encode() if value is not None else None
        connection.request(method, path, body=body, headers=actual_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), json.loads(response.read())
        connection.close()
        return result

    def raw_request(self, headers, body=b"", *, shutdown_write=False):
        connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3)
        self.addCleanup(connection.close)
        connection.sendall(("POST /v1/recommendations HTTP/1.0\r\nAuthorization: Bearer " + TOKEN
                            + "\r\nContent-Type: application/json\r\n" + headers + "\r\n").encode() + body)
        if shutdown_write:
            connection.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(connection)
        response.begin()
        return response.status, json.loads(response.read())

    def test_real_http_post_get_idempotence_and_read_only_result(self):
        value = request_fixture()
        status, headers, result = self.request("POST", "/v1/recommendations", value)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(len(headers["X-Request-ID"]), 32)
        operation_id = result["recommendation_id"]
        self.assertEqual(self.store.metadata()["saved_results"], 1)
        self.assertEqual(self.request("POST", "/v1/recommendations", value)[2], result)
        with patch.object(self.store, "submit", side_effect=AssertionError("read must not submit")):
            self.assertEqual(self.request("GET", "/v1/recommendations/" + operation_id)[2], result)
        self.assertEqual(self.store.metadata()["saved_results"], 1)
        self.assertEqual(self.logs[-1]["operation_id"], operation_id)

    def test_http_rounded_probabilities_preserve_saved_inputs_and_conditional_output(self):
        value = request_fixture()
        probabilities = [0, 0.16, 0.4, 0.23, 0]
        set_distribution(value["candidate_profiles"][0]["parameters"][0], probabilities, unknown=0.2)
        status, _, result = self.request("POST", "/v1/recommendations", value)
        self.assertEqual(status, 200)
        parameter = result["items"][0]["parameters"][0]
        self.assertEqual([level["p_raw"] for level in parameter["levels"]], probabilities)
        self.assertAlmostEqual(sum(level["q"] for level in parameter["levels"]), 1)
        with self.store.db() as db:
            snapshot = json.loads(db.execute("SELECT input FROM recommendations").fetchone()[0])
        self.assertEqual(snapshot, value)
        self.assertEqual(self.request("GET", "/v1/recommendations/" + result["recommendation_id"])[2], result)

    def test_auth_is_required_for_results_readiness_and_operator_metadata(self):
        for path in ("/readyz", "/v1/operator", "/v1/recommendations/" + "0" * 64):
            for token in (None, "b2" * 32):
                self.assertEqual(self.request("GET", path, token=token)[0], 401)
        self.assertEqual(self.request("POST", "/v1/recommendations", request_fixture(), token=None)[0], 401)
        status, _, health = self.request("GET", "/healthz", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(set(health), {"status", "version", "service"})
        self.assertEqual(self.request("GET", "/readyz")[2]["status"], "ready")
        operator = self.request("GET", "/v1/operator")[2]
        self.assertEqual(operator["storage"]["saved_results"], 0)
        self.assertNotIn(TOKEN, canonical_json(operator))
        self.assertEqual(self.store.metadata()["saved_results"], 0)

    def test_safe_domain_rejections_and_atomic_incompatibility(self):
        value = request_fixture(count=2)
        value["candidate_profiles"][1]["rubric_sha256"] = "f" * 64
        self.assertEqual(self.request("POST", "/v1/recommendations", value)[0], 422)
        value = request_fixture()
        value["candidate_profiles"][0]["sources"][0]["text"] = "PRIVATE BODY TEXT"
        _, _, error = self.request("POST", "/v1/recommendations", value)
        self.assertEqual(error, {"error": "invalid_input"})
        self.assertNotIn("PRIVATE BODY", canonical_json(self.logs))
        self.assertEqual(self.store.metadata()["saved_results"], 0)

    def test_invalid_json_duplicate_keys_bad_encoding_and_nonfinite_are_rejected(self):
        for raw in (b'{"key":1,"key":2}', b'{"value":NaN}', b'{"value":Infinity}', b"\xff", b"PRIVATE INVALID JSON"):
            with self.subTest(raw=raw):
                self.assertEqual(self.raw_request("Content-Length: " + str(len(raw)) + "\r\n", raw)[0], 422)
        self.assertEqual(self.store.metadata()["saved_results"], 0)

    def test_framing_and_payload_limits_reject_without_reading_or_truncation(self):
        for header, expected in (("", 422), ("Content-Length: -1\r\n", 422),
                                 ("Content-Length: +2\r\n", 422), ("Content-Length: 0\r\n", 422),
                                 ("Content-Length: 2\r\nContent-Length: 2\r\n", 422),
                                 ("Content-Length: 2\r\nTransfer-Encoding: chunked\r\n", 422),
                                 ("Content-Length: " + str(api.MAX_REQUEST_BYTES + 1) + "\r\n", 413)):
            with self.subTest(header=header):
                self.assertEqual(self.raw_request(header)[0], expected)
        self.assertEqual(self.raw_request("Content-Length: 20\r\n", b"{}", shutdown_write=True)[0], 422)
        self.assertEqual(self.request("POST", "/v1/recommendations", request_fixture(), headers={"Content-Type": "text/plain"})[0], 422)
        with patch("recommendations.api.READ_TIMEOUT_SECONDS", 0.03):
            self.assertEqual(self.raw_request("Content-Length: 100\r\n", b"{")[0], 408)
        self.assertEqual(self.store.metadata()["saved_results"], 0)

    def test_real_storage_outage_readiness_and_post_fail_then_recover(self):
        moved = self.path.with_suffix(".away")
        self.path.rename(moved)
        self.path.mkdir()
        try:
            self.assertEqual(self.request("GET", "/healthz")[0], 200)
            self.assertEqual(self.request("GET", "/readyz")[0], 503)
            self.assertEqual(self.request("POST", "/v1/recommendations", request_fixture())[0], 503)
        finally:
            self.path.rmdir()
            moved.rename(self.path)
        self.assertEqual(self.request("GET", "/readyz")[0], 200)
        self.assertEqual(self.store.metadata()["saved_results"], 0)
        self.assertEqual(self.request("POST", "/v1/recommendations", request_fixture())[0], 200)

    def test_concurrency_is_bounded_and_recovers_after_slow_connections(self):
        held = []
        try:
            # Occupy all request threads without completing HTTP headers.
            for _ in range(api.MAX_CONCURRENT_REQUESTS):
                connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3)
                connection.sendall(b"GET /healthz HTTP/1.0\r\n")
                held.append(connection)
            # Wait on semaphore acquisition instead of assuming accept timing.
            import time
            deadline = time.monotonic() + 2
            while self.server.slots._value and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(self.request("GET", "/healthz")[0], 503)
        finally:
            for connection in held:
                connection.close()
        deadline = time.monotonic() + 2
        while self.server.slots._value != api.MAX_CONCURRENT_REQUESTS and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(self.request("GET", "/healthz")[0], 200)

    def test_unknown_ids_and_routes_do_not_mutate(self):
        self.assertEqual(self.request("GET", "/v1/recommendations/" + "0" * 64)[0], 404)
        for method, path in (("GET", "/v1/recommendations"), ("POST", "/unknown"),
                             ("DELETE", "/v1/recommendations/" + "0" * 64)):
            self.assertEqual(self.request(method, path)[0], 404)
        self.assertEqual(self.store.metadata()["saved_results"], 0)


if __name__ == "__main__":
    unittest.main()
