"""Local HTTP boundary tests; no provider network requests."""

from copy import deepcopy
import http.client
from http.server import ThreadingHTTPServer
import io
import json
import os
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from assessment_core import api
from assessment_core.engine import prepare
from assessment_core.providers.jev import JevError
from assessment_core.runstore import RunUnavailable


TOKEN = "a1" * 32


def body_fixture():
    return {
        "corpus": {"candidate_id": "private-candidate", "sources": [
            {"source_id": "private-cv", "version": "v1", "kind": "cv", "units": [
                {"unit_id": "p1", "text": "PRIVATE SOURCE TEXT", "role": "candidate"},
            ]},
        ]},
        "rubric": {"rubric_id": "skills", "version": "v2", "parameters": [
            {"parameter_id": "independence", "title": "Самостоятельность", "definition": "Выбор решения", "levels": [
                {"level_id": "guided", "label": "С помощью", "description": "Решение выбирает руководитель"},
                {"level_id": "independent", "label": "Сам", "description": "Сам выбирает решение"},
            ]},
        ]},
    }


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.submissions = []

    def submit(self, plan):
        self.submissions.append(deepcopy(plan))
        if plan["run_id"] not in self.rows:
            self.rows[plan["run_id"]] = {
                "id": plan["run_id"], "status": "queued", "plan": deepcopy(plan),
                "response": {"private": "RAW PROVIDER TEXT"}, "result": None, "error": None,
            }
        return deepcopy(self.rows[plan["run_id"]])

    def get(self, run_id):
        if run_id not in self.rows:
            raise RunUnavailable("run_not_found")
        return deepcopy(self.rows[run_id])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), api.handler(self.store, "jev-1.14.0", TOKEN))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, value=None, *, token=TOKEN, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        body = json.dumps(value, ensure_ascii=False).encode() if value is not None else None
        actual_headers = {"Content-Type": "application/json"}
        if token is not None:
            actual_headers["Authorization"] = "Bearer " + token
        actual_headers.update(headers or {})
        connection.request(method, path, body=body, headers=actual_headers)
        response = connection.getresponse()
        result = (response.status, dict(response.getheaders()), json.loads(response.read()))
        connection.close()
        return result

    def raw_request(self, headers, body=b"", *, shutdown_write=False):
        connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        self.addCleanup(connection.close)
        connection.sendall(("POST /v1/assessments HTTP/1.0\r\nAuthorization: Bearer " + TOKEN
                            + "\r\nContent-Type: application/json\r\n" + headers + "\r\n").encode() + body)
        if shutdown_write:
            connection.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(connection)
        response.begin()
        return response.status, json.loads(response.read())

    def test_health_is_public_minimal_and_disables_caching(self):
        status, headers, value = self.request("GET", "/healthz", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(set(value), {"status", "version", "model"})
        self.assertEqual(value["model"], "jev-1.14.0")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn(TOKEN, json.dumps(value))

    def test_bearer_auth_is_required_except_health_and_uses_constant_time_compare(self):
        for token in (None, "b2" * 32):
            for method, path in (("POST", "/v1/assessments"), ("GET", "/v1/assessments/" + "0" * 64)):
                with self.subTest(token=token, method=method):
                    status, _, value = self.request(method, path, body_fixture() if method == "POST" else None, token=token)
                    self.assertEqual((status, value), (401, {"error": "unauthorized"}))
        with patch("assessment_core.api.hmac.compare_digest", wraps=api.hmac.compare_digest) as compare:
            self.request("GET", "/unknown")
        compare.assert_called_once()
        self.assertEqual(compare.call_args.args, (("Bearer " + TOKEN).encode(), ("Bearer " + TOKEN).encode()))
        self.assertEqual(self.store.submissions, [])

    def test_submit_is_deterministic_and_exposes_metadata_without_sources(self):
        status, _, value = self.request("POST", "/v1/assessments", body_fixture())
        self.assertEqual(status, 202)
        expected_plan = prepare(**body_fixture(), model="jev-1.14.0")
        self.assertEqual(self.store.submissions, [expected_plan])
        self.assertEqual(set(value), {"run_id", "status", "assessment", "error", "corpus_revision", "rubric_version", "model"})
        self.assertEqual(value, {"run_id": expected_plan["run_id"], "status": "queued", "assessment": None,
                                 "error": None, "corpus_revision": expected_plan["corpus"]["sha256"],
                                 "rubric_version": "v2", "model": "jev-1.14.0"})
        self.assertNotIn("PRIVATE SOURCE", json.dumps(value))
        self.assertNotIn("RAW PROVIDER", json.dumps(value))
        status, _, repeated = self.request("POST", "/v1/assessments", body_fixture())
        self.assertEqual(status, 202)
        self.assertEqual(repeated, value)

    def test_get_completed_assessment_and_unknown_outcome_remain_distinct(self):
        _, _, initial = self.request("POST", "/v1/assessments", body_fixture())
        row = self.store.rows[initial["run_id"]]
        row.update(status="completed", result={"parameters": [{"status": "assessed", "selected_level_id": "guided"}]})
        status, _, value = self.request("GET", "/v1/assessments/" + initial["run_id"])
        self.assertEqual(status, 200)
        self.assertEqual(value["assessment"], row["result"])
        row.update(status="outcome_unknown", error={"code": "timeout", "status": None, "message": "PRIVATE PROVIDER ERROR"})
        status, _, value = self.request("GET", "/v1/assessments/" + initial["run_id"])
        self.assertEqual(status, 200)
        self.assertEqual(value["status"], "outcome_unknown")
        self.assertIsNone(value["assessment"])
        self.assertEqual(value["error"], {"code": "timeout", "status": None})
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertNotIn("insufficient_data", json.dumps(value))

    def test_unknown_routes_and_missing_ids_are_404(self):
        for method, path in (("GET", "/v1/assessments/" + "0" * 64), ("GET", "/v1/assessments/nope"),
                             ("GET", "/v1/assessments"), ("POST", "/wrong"),
                             ("DELETE", "/v1/assessments/" + "0" * 64)):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path)[0], 404)

    def test_exact_envelope_and_domain_validation_fail_without_echoing_body(self):
        for value in ({}, [], {**body_fixture(), "model": "jev-latest"},
                      {**body_fixture(), "extra": "PRIVATE BODY"},
                      {"corpus": {"private": "PRIVATE BODY"}, "rubric": body_fixture()["rubric"]}):
            with self.subTest(value=type(value).__name__):
                status, _, error = self.request("POST", "/v1/assessments", value)
                self.assertEqual(status, 422)
                self.assertNotIn("PRIVATE", json.dumps(error))
        self.assertEqual(self.store.submissions, [])

    def test_json_duplicate_keys_nonfinite_and_bad_utf8_are_422(self):
        for raw in (b'{"corpus":{},"corpus":{},"rubric":{}}', b'{"corpus":NaN,"rubric":{}}',
                    b'{"corpus":Infinity,"rubric":{}}', b"PRIVATE invalid", b"\xff"):
            with self.subTest(raw=raw):
                status, value = self.raw_request("Content-Length: " + str(len(raw)) + "\r\n", raw)
                self.assertEqual(status, 422)
                self.assertNotIn("PRIVATE", json.dumps(value))

    def test_content_length_is_strict_and_size_bounded_before_reading(self):
        for header, expected in (("", 422), ("Content-Length: -1\r\n", 422),
                                 ("Content-Length: +2\r\n", 422), ("Content-Length: 0\r\n", 422),
                                 ("Content-Length: 2\r\nContent-Length: 2\r\n", 422),
                                 ("Content-Length: 2\r\nTransfer-Encoding: chunked\r\n", 422),
                                 ("Content-Length: " + str(api.MAX_REQUEST_BYTES + 1) + "\r\n", 413)):
            with self.subTest(header=header):
                self.assertEqual(self.raw_request(header)[0], expected)
        self.assertEqual(self.raw_request("Content-Length: 20\r\n", b"{}", shutdown_write=True)[0], 422)
        self.assertEqual(self.store.submissions, [])

    def test_body_read_has_a_socket_timeout(self):
        with patch("assessment_core.api.READ_TIMEOUT_SECONDS", 0.05):
            status, value = self.raw_request("Content-Length: 100\r\n", b"{")
        self.assertEqual((status, value), (408, {"error": "request_timeout"}))
        self.assertEqual(self.store.submissions, [])

    def test_content_type_and_internal_failure_are_safe(self):
        self.assertEqual(self.request("POST", "/v1/assessments", body_fixture(), headers={"Content-Type": "text/plain"})[0], 422)
        with patch.object(self.store, "submit", side_effect=RuntimeError("PRIVATE FAILURE")):
            status, _, value = self.request("POST", "/v1/assessments", body_fixture())
        self.assertEqual((status, value), (503, {"error": "service_unavailable"}))


class WorkerTests(unittest.TestCase):
    def test_worker_calls_one_queued_plan_once_and_does_not_convert_provider_error(self):
        plan = prepare(**body_fixture())
        store, client = Mock(), Mock()
        store.next_queued.side_effect = [plan, None]
        store.execute.side_effect = JevError("timeout", outcome_unknown=True)
        # False requests a bounded backoff, without retrying or reclassifying.
        self.assertFalse(api.work_once(store, client))
        self.assertFalse(api.work_once(store, client))
        store.execute.assert_called_once_with(plan, client)
        client.evaluate.assert_not_called()
        self.assertEqual(store.method_calls, [unittest.mock.call.next_queued(), unittest.mock.call.execute(plan, client),
                                             unittest.mock.call.next_queued()])

    def test_main_requires_token_before_any_storage_and_binds_localhost(self):
        with patch.dict(os.environ, {"ASSESSMENT_CORE_SERVICE_TOKEN": "short-private-token"}, clear=True), \
                patch("assessment_core.api.RunStore") as store, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(api.main(), 2)
            store.assert_not_called()
            self.assertNotIn("short-private-token", output.getvalue())
        with patch.dict(os.environ, {"ASSESSMENT_CORE_SERVICE_TOKEN": TOKEN, "ASSESSMENT_CORE_PORT": "8097",
                                    "ASSESSMENT_CORE_DB": "/private/local.db"}, clear=True), \
                patch("assessment_core.api.RunStore") as store, patch("assessment_core.api.JevConfig.from_environment") as config, \
                patch("assessment_core.api.JevClient"), patch("assessment_core.api.threading.Thread") as thread, \
                patch("assessment_core.api.ThreadingHTTPServer") as server:
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            self.assertEqual(api.main(), 0)
            self.assertEqual(server.call_args.args[0], ("127.0.0.1", 8097))
            config.assert_called_once_with(model="jev-1.14.0")
            store.assert_called_once_with("/private/local.db")
            store.return_value.recover.assert_called_once_with()
            thread.return_value.start.assert_called_once_with()

    def test_second_server_binding_failure_does_not_recover_active_ledger(self):
        with patch.dict(os.environ, {"ASSESSMENT_CORE_SERVICE_TOKEN": TOKEN}, clear=True), \
                patch("assessment_core.api.RunStore") as store, patch("assessment_core.api.JevConfig.from_environment"), \
                patch("assessment_core.api.ThreadingHTTPServer", side_effect=OSError("address in use")), \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(api.main(), 2)
            store.return_value.recover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
