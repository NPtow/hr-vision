"""Regression tests for the release patch, isolated from database/app imports.

The production methods are compiled directly from the patched source. The fake
HTTP transport makes no network requests and verifies authorization failures are
not confused with service/network failures. Run with Python's unittest module.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode, urlsplit, urlunsplit


class RequestError(Exception):
    pass


class ConnectionClosed(Exception):
    pass


class InvalidStatus(Exception):
    def __init__(self, status):
        self.response = SimpleNamespace(status_code=status)


class Response:
    def __init__(self, status, body=None):
        self.status_code = status
        self.body = {"api_key_id": "synthetic-id", "scopes": ["usage:write"]} if body is None else body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class DeepgramValidationTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parent / "patches" / "check_validity.py"
        tree = ast.parse(path.read_text())
        original_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UserConfigurationValidator")
        methods = [node for node in original_class.body if isinstance(node, ast.FunctionDef) and node.name in {"_check_api_key", "_check_deepgram_api_key", "_check_deepgram_streaming_key"}]
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            ast.ClassDef(name="Validator", bases=[], keywords=[], body=methods, decorator_list=[]),
        ], type_ignores=[])
        ast.fix_missing_locations(module)
        self.calls = []
        self.checked_urls = []
        self.response = Response(200)
        self.request_error = None
        self.ws_calls = []
        self.ws_outcomes = [TimeoutError(), TimeoutError(), TimeoutError()]

        def get(url, **kwargs):
            self.calls.append((url, kwargs))
            if self.request_error:
                raise self.request_error
            return self.response

        owner = self

        class Stream:
            def __enter__(self):
                self.outcome = owner.ws_outcomes.pop(0)
                if isinstance(self.outcome, Exception):
                    raise self.outcome
                return self

            def __exit__(self, *args):
                pass

            def send(self, data):
                owner.assertEqual(json.loads(data), {"type": "CloseStream"})

            def recv(self, timeout):
                owner.assertEqual(timeout, 2.0)
                return json.dumps(self.outcome)

        def websocket_connect(url, **kwargs):
            self.ws_calls.append((url, kwargs))
            return Stream()

        providers = SimpleNamespace(**{
            name: SimpleNamespace(value=name.lower()) for name in
            ("OPENAI", "ATLASCLOUD", "HOPPER", "OPENAI_REALTIME", "DEEPGRAM")
        })
        namespace = {
            "httpx": SimpleNamespace(get=get, RequestError=RequestError),
            "json": json,
            "urlencode": urlencode, "urlsplit": urlsplit, "urlunsplit": urlunsplit,
            "websocket_connect": websocket_connect,
            "ConnectionClosed": ConnectionClosed, "InvalidStatus": InvalidStatus,
            "ServiceProviders": providers,
            "validate_user_configured_service_url": lambda url, **kwargs: self.checked_urls.append(url),
        }
        exec(compile(module, str(path), "exec"), namespace)
        self.validator = namespace["Validator"]()
        self.validator._validator_map = {"deepgram": self.validator._check_deepgram_api_key}
        self.key = "synthetic-secret-must-not-appear-in-errors"

    def validate(self, config=None):
        return self.validator._check_api_key("deepgram", self.key, config)

    def assert_failure(self, expected, excluded="Invalid Deepgram API key"):
        with self.assertRaises(ValueError) as caught:
            self.validate()
        message = str(caught.exception)
        self.assertIn(expected, message)
        self.assertNotIn(self.key, message)
        if excluded:
            self.assertNotIn(excluded, message)

    def test_usage_scoped_key_validates_without_project_management_request(self):
        self.assertTrue(self.validate())
        self.assertEqual(len(self.calls), 1)
        url, kwargs = self.calls[0]
        self.assertEqual(url, "https://api.deepgram.com/v1/auth/token")
        self.assertEqual(kwargs["headers"]["Authorization"], "Token " + self.key)
        self.assertEqual(kwargs["timeout"], 3.0)
        self.assertFalse(kwargs["follow_redirects"])

    def test_dispatch_preserves_configured_region_and_validates_destination(self):
        config = SimpleNamespace(base_url="https://api.eu.deepgram.com/")
        self.assertTrue(self.validate(config))
        self.assertEqual(self.calls[0][0], "https://api.eu.deepgram.com/v1/auth/token")
        self.assertEqual(self.checked_urls, ["https://api.eu.deepgram.com"])

    def test_host_only_configuration_is_normalized(self):
        self.assertTrue(self.validate(SimpleNamespace(base_url="api.eu.deepgram.com")))
        self.assertEqual(self.calls[0][0], "https://api.eu.deepgram.com/v1/auth/token")

    def test_invalid_credential_fails(self):
        self.response = Response(401)
        self.assert_failure("Invalid Deepgram API key", excluded=None)
        self.assertEqual(self.ws_calls, [])

    def test_permission_failure_is_distinct(self):
        self.response = Response(403)
        self.assert_failure("authorization failed")

    def test_network_failure_does_not_claim_key_is_invalid(self):
        self.request_error = RequestError(self.key)
        self.assert_failure("Could not reach Deepgram")
        self.assertEqual(len(self.ws_calls), 3)

    def test_streaming_fallback_requires_authenticated_metadata(self):
        self.request_error = RequestError(self.key)
        self.ws_outcomes = [{"type": "Metadata", "duration": 0.0}]
        self.assertTrue(self.validate(SimpleNamespace(base_url="https://api.eu.deepgram.com", model="nova-3-general", language="ru")))
        url, kwargs = self.ws_calls[0]
        self.assertTrue(url.startswith("wss://api.eu.deepgram.com/v1/listen?"))
        self.assertIn("model=nova-3-general", url)
        self.assertIn("language=ru", url)
        self.assertEqual(kwargs["additional_headers"], {"Authorization": "Token " + self.key})
        self.assertEqual(kwargs["open_timeout"], 3.0)
        self.assertNotIn("ssl", kwargs)
        self.assertNotIn("proxy", kwargs)

    def test_streaming_retries_transport_only_until_verified_success(self):
        self.request_error = RequestError()
        self.ws_outcomes = [TimeoutError(self.key), ConnectionClosed(self.key), {"type": "Metadata"}]
        self.assertTrue(self.validate())
        self.assertEqual(len(self.ws_calls), 3)

    def test_streaming_auth_failure_is_immediate(self):
        self.request_error = RequestError()
        for status in (401, 403):
            with self.subTest(status=status):
                self.ws_calls.clear()
                self.ws_outcomes = [InvalidStatus(status), {"type": "Metadata"}]
                self.assert_failure("authorization failed")
                self.assertEqual(len(self.ws_calls), 1)

    def test_streaming_http_failures_are_not_retried_or_accepted(self):
        self.request_error = RequestError()
        for status in (400, 429, 500, 503):
            with self.subTest(status=status):
                self.ws_calls.clear()
                self.ws_outcomes = [InvalidStatus(status), {"type": "Metadata"}]
                self.assert_failure("rate limited" if status == 429 else f"HTTP {status}")
                self.assertEqual(len(self.ws_calls), 1)

    def test_streaming_unexpected_response_never_validates(self):
        self.request_error = RequestError()
        for payload in ({}, {"type": "Results"}, {"type": "Metadata", "err_code": "failed"}, []):
            with self.subTest(payload=payload):
                self.ws_calls.clear()
                self.ws_outcomes = [payload, {"type": "Metadata"}]
                self.assert_failure("unexpected streaming validation response")
                self.assertEqual(len(self.ws_calls), 1)

    def test_rate_limit_is_not_auth_failure(self):
        self.response = Response(429)
        self.assert_failure("rate limited")

    def test_provider_errors_are_not_accepted(self):
        for status in (400, 500, 502, 503):
            with self.subTest(status=status):
                self.response = Response(status)
                self.assert_failure(f"HTTP {status}")

    def test_redirect_is_not_followed_or_accepted(self):
        self.response = Response(302)
        self.assert_failure("HTTP 302")
        self.assertFalse(self.calls[0][1]["follow_redirects"])

    def test_success_status_requires_json_details(self):
        for body in ([], {}, {"err_code": "INVALID_AUTH"}, ValueError(self.key)):
            with self.subTest(body_type=type(body).__name__):
                self.response = Response(200, body)
                self.assert_failure("unexpected validation response")


if __name__ == "__main__":
    unittest.main()
