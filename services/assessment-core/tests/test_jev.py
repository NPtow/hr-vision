"""Provider contract and transport tests; no paid API calls."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from email.message import Message
import io
import http.client
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import urllib.response

from assessment_core.providers.jev import ENDPOINT, JevClient, JevConfig, JevError, validate_response


def request_fixture(model="jev-1.14.0"):
    return {
        "model": model,
        "state": {"corpus": ["All original source material", "Another source"]},
        "questions": {
            "skill": {"type": "score", "instructions": "Assess the entire corpus.",
                      "criteria": ["No evidence", {"description": "Some evidence", "examples": ["work"]},
                                   "Strong evidence"]},
            "evidence": {"type": "choice", "instructions": "Does the corpus contain sufficient evidence?",
                         "criteria": {"yes": "Evidence present", "unknown": None}},
            "mandatory": {"type": "noul", "instructions": "Is the stated requirement explicitly mandatory?"},
            "meets": {"type": "noul", "instructions": "Do the candidate's materials demonstrate the stated requirement?",
                      "criteria": {"true": "Demonstrated in the materials", "false": "Not demonstrated"}},
        },
    }


def response_fixture():
    return {
        "model": "jev-1.14.0",
        "answers": {
            "skill": {"type": "score", "score": 1.25, "confidence": 0.45,
                      "probabilities": {"0": 0.125, "1": 0.5, "2": 0.375},
                      "legend": {str(i): description for i, description in
                                 enumerate(request_fixture()["questions"]["skill"]["criteria"])}},
            "evidence": {"type": "choice", "choice": "unknown", "confidence": 0.1,
                         "probabilities": {"yes": 0.4, "unknown": 0.6}},
            "mandatory": {"type": "noul", "noul": 0.93},
            "meets": {"type": "noul", "noul": 0.38},
        },
        "usage": {"input_tokens": 150, "output_tokens": 30},
    }


class ConfigTests(unittest.TestCase):
    def test_existing_analytics_credential_name_is_supported(self):
        with patch.dict(os.environ, {"ASSESSMENT_JEV_KEY": "analytics-test-key"}, clear=True):
            self.assertEqual(JevConfig.from_environment().api_key, "analytics-test-key")
        with patch.dict(os.environ, {"ASSESSMENT_CORE_JEV_API_KEY": "new-core-test-key",
                                    "ASSESSMENT_JEV_KEY": "analytics-test-key"}, clear=True):
            self.assertEqual(JevConfig.from_environment().api_key, "new-core-test-key")

    def test_key_is_hidden_and_configuration_is_frozen(self):
        config = JevConfig(api_key="secret-example-key")
        self.assertNotIn("secret-example-key", repr(config))
        self.assertEqual(config.model, "jev-1.14.0")
        with self.assertRaises(FrozenInstanceError):
            config.model = "jev-latest"

    def test_environment_precedence_and_explicit_model_only(self):
        names = ["JEV_API_KEY", "TYPESAFE_API_KEY", "HR_JEV_API_KEY"]
        for index, expected in enumerate(names):
            environment = {name: " key-" + name + " " for name in names[index:]}
            environment["HR_JEV_MODEL"] = "jev-preview"
            with self.subTest(expected=expected), patch.dict(os.environ, environment, clear=True):
                config = JevConfig.from_environment()
                self.assertEqual(config.api_key, "key-" + expected)
                self.assertEqual(config.model, "jev-1.14.0")
                self.assertEqual(JevConfig.from_environment(model="jev-preview").model, "jev-preview")

    def test_credential_file_and_unreadable_file_errors_are_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_text("file-secret\n", encoding="utf-8")
            with patch.dict(os.environ, {"JEV_API_KEY_FILE": str(path)}, clear=True):
                self.assertEqual(JevConfig.from_environment().api_key, "file-secret")
                path.unlink()
                with self.assertRaises(JevError) as raised:
                    JevConfig.from_environment()
                self.assertEqual(raised.exception.code, "configuration")
                self.assertNotIn(str(path), str(raised.exception))

    def test_default_credential_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            key = home / ".config" / "jev" / "api-key"
            key.parent.mkdir(parents=True)
            key.write_text("default-file-key\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), \
                    patch("assessment_core.providers.jev.Path.home", return_value=home):
                self.assertEqual(JevConfig.from_environment().api_key, "default-file-key")

    def test_invalid_configuration(self):
        for changes in ({"api_key": ""}, {"api_key": "secret\nvalue"}, {"api_key": "секрет"},
                        {"model": "other-provider"}, {"timeout_seconds": True},
                        {"timeout_seconds": math.inf}, {"timeout_seconds": 0},
                        {"max_response_bytes": False}, {"max_request_bytes": -1}):
            with self.subTest(changes=changes), self.assertRaises(JevError):
                JevConfig(**({"api_key": "test-key"} | changes))


class ResponseTests(unittest.TestCase):
    def test_noul_preserves_raw_yes_probability_without_threshold_or_confidence(self):
        for probability in (0, 0.01, 0.5, 0.93, 1):
            response = response_fixture()
            response["answers"]["mandatory"]["noul"] = probability
            with self.subTest(probability=probability):
                actual = validate_response(request_fixture(), response)
                self.assertIs(actual, response)
                self.assertEqual(actual["answers"]["mandatory"], {"type": "noul", "noul": probability})

    def test_noul_invalid_values_and_extra_fields_are_rejected(self):
        for answer in (
            *({"type": "noul", "noul": value} for value in
              (True, False, None, "0.5", math.nan, math.inf, -0.01, 1.01)),
            {"type": "noul"}, {"type": "boolean", "noul": 0.5},
            {"type": "noul", "noul": 0.5, "confidence": 0.5},
            {"type": "noul", "noul": 0.5, "probabilities": {"true": 0.5, "false": 0.5}},
        ):
            response = response_fixture()
            response["answers"]["mandatory"] = answer
            with self.subTest(answer=answer), self.assertRaises(JevError) as raised:
                validate_response(request_fixture(), response)
            self.assertEqual(raised.exception.code, "invalid_response")

    def test_noul_accepts_optional_and_structured_true_false_descriptions(self):
        for criteria in (None, {}, {"true": "Explicitly required"},
                         {"true": {"meaning": "Explicit requirement"},
                          "false": ["No explicit requirement", "Explicitly optional"]}):
            payload = request_fixture()
            payload["questions"]["mandatory"]["criteria"] = criteria
            with self.subTest(criteria=criteria):
                validate_response(payload, response_fixture())

    def test_valid_result_preserves_identity_and_all_provider_fields(self):
        response = response_fixture()
        original = deepcopy(response)
        self.assertIs(validate_response(request_fixture(), response), response)
        self.assertEqual(response, original)
        self.assertNotEqual(response["answers"]["evidence"]["confidence"], 0.6)

    def test_pin_and_resolved_alias(self):
        for model in ("jev-latest", "jev-preview"):
            self.assertEqual(validate_response(request_fixture(model), response_fixture())["model"], "jev-1.14.0")
        for actual in ("jev-1.13.0", "jev-latest", "unknown", ""):
            response = response_fixture()
            response["model"] = actual
            with self.subTest(actual=actual), self.assertRaises(JevError):
                validate_response(request_fixture(), response)

    def test_schema_and_usage_are_exact(self):
        mutations = [
            lambda r: r.update(error="PRIVATE PROVIDER ERROR"),
            lambda r: r.pop("usage"),
            lambda r: r["answers"].pop("evidence"),
            lambda r: r["answers"].update(extra=r["answers"]["skill"]),
            lambda r: r["answers"]["skill"].update(type="choice"),
            lambda r: r["answers"]["skill"].update(explanation="invented"),
            lambda r: r["usage"].update(input_tokens=True),
            lambda r: r["usage"].update(input_tokens=-1),
            lambda r: r["usage"].update(output_tokens=1.5),
            lambda r: r["usage"].update(total_tokens=180),
            lambda r: r["answers"]["skill"]["legend"].update({"1": "Changed criterion"}),
            lambda r: r["answers"]["skill"]["probabilities"].update({"3": 0}),
            lambda r: r["answers"]["evidence"]["probabilities"].pop("yes"),
            lambda r: r["answers"]["evidence"].update(choice="yes"),
            lambda r: r["answers"]["evidence"].update(choice="absent"),
        ]
        for index, mutate in enumerate(mutations):
            response = response_fixture()
            mutate(response)
            with self.subTest(index=index), self.assertRaises(JevError) as raised:
                validate_response(request_fixture(), response)
            self.assertEqual(raised.exception.code, "invalid_response")
            self.assertTrue(raised.exception.outcome_unknown)
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_every_numeric_field_rejects_bool_nonfinite_and_range_errors(self):
        for field, values in {
            "score": [True, math.nan, math.inf, -0.01, 2.01, "1.25"],
            "confidence": [False, math.nan, -0.01, 1.01, "0.5"],
            "probability": [True, math.nan, math.inf, -0.01, 1.01, "0.5"],
        }.items():
            for value in values:
                response = response_fixture()
                target = response["answers"]["skill"]
                if field == "probability":
                    target["probabilities"]["0"] = value
                else:
                    target[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(JevError):
                    validate_response(request_fixture(), response)

    def test_distribution_sum_and_score_consistency(self):
        for change in (0.00002, -0.00002):
            response = response_fixture()
            response["answers"]["skill"]["probabilities"]["0"] += change
            with self.subTest(change=change), self.assertRaises(JevError):
                validate_response(request_fixture(), response)
        response = response_fixture()
        response["answers"]["skill"]["score"] = 1.3
        with self.assertRaises(JevError):
            validate_response(request_fixture(), response)
        response["answers"]["skill"]["score"] = 1.2549
        validate_response(request_fixture(), response)

    def test_tied_choice_and_rounded_probabilities_are_accepted(self):
        response = response_fixture()
        response["answers"]["evidence"]["probabilities"] = {"yes": 0.5, "unknown": 0.5}
        response["answers"]["skill"]["probabilities"]["0"] += 0.000009
        validate_response(request_fixture(), response)

    def test_structured_legend_cannot_substitute_boolean_for_number(self):
        payload, response = request_fixture(), response_fixture()
        payload["questions"]["skill"]["criteria"][1] = {"examples": [True]}
        response["answers"]["skill"]["legend"]["1"] = {"examples": [1]}
        with self.assertRaises(JevError):
            validate_response(payload, response)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.config = JevConfig(api_key="do-not-leak-secret", timeout_seconds=7)
        self.client = JevClient(self.config)

    @staticmethod
    def incoming(raw, *, status=200, url=ENDPOINT):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = url
        response.getcode.return_value = status
        response.read.return_value = raw
        return response

    def test_one_https_post_full_corpus_and_no_environment_proxy(self):
        raw = json.dumps(response_fixture()).encode()
        incoming = self.incoming(raw)
        opener = Mock()
        opener.open.return_value = incoming
        payload = request_fixture()
        original = deepcopy(payload)
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://private-proxy.invalid"}), \
                patch("assessment_core.providers.jev.urllib.request.build_opener", return_value=opener) as build:
            result = self.client.evaluate(payload)
        self.assertEqual(result, response_fixture())
        self.assertEqual(payload, original)
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), original)
        self.assertEqual(request.get_header("Authorization"), "Bearer do-not-leak-secret")
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 7})
        self.assertEqual(build.call_args.args[0].proxies, {})
        incoming.read.assert_called_once_with(self.config.max_response_bytes + 1)

    def test_real_urllib_redirect_processing_does_not_send_another_request(self):
        def redirect(_handler, request):
            headers = Message()
            headers["Location"] = "https://untrusted.invalid/steal-key"
            response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, code=302)
            response.msg = "Found"
            return response

        with patch.object(urllib.request.HTTPSHandler, "https_open", autospec=True, side_effect=redirect) as transport:
            with self.assertRaises(JevError) as raised:
                self.client.evaluate(request_fixture())
        self.assertEqual(raised.exception.code, "redirect")
        self.assertEqual(raised.exception.status, 302)
        self.assertEqual(transport.call_count, 1)

    def test_network_failure_timeout_and_http_errors_are_never_replayed(self):
        cases = [
            (TimeoutError("do-not-leak-secret"), "timeout", True, None),
            (urllib.error.URLError(TimeoutError("PRIVATE")), "timeout", True, None),
            (urllib.error.URLError("PRIVATE"), "network_error", True, None),
            (http.client.IncompleteRead(b"PRIVATE"), "network_error", True, None),
        ]
        for status, code in [(401, "authentication"), (403, "authentication"),
                             (402, "exhausted_balance"), (429, "rate_limited"),
                             (413, "request_too_large"), (500, "http_error")]:
            cases.append((urllib.error.HTTPError(ENDPOINT, status, "PRIVATE secret", {}, io.BytesIO(b"PRIVATE")),
                          code, status >= 500, status))
        for error, code, unknown, status in cases:
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(code=code, status=status), \
                    patch("assessment_core.providers.jev.urllib.request.build_opener", return_value=opener):
                with self.assertRaises(JevError) as raised:
                    self.client.evaluate(request_fixture())
                opener.open.assert_called_once()
                self.assertEqual((raised.exception.code, raised.exception.outcome_unknown, raised.exception.status),
                                 (code, unknown, status))
                self.assertNotIn("PRIVATE", str(raised.exception))
                self.assertNotIn("do-not-leak-secret", str(raised.exception))

    def test_invalid_requests_fail_before_any_transport(self):
        for mutate in (
            lambda p: p.update(model="jev-latest"),
            lambda p: p.update(endpoint="http://untrusted.invalid"),
            lambda p: p["questions"]["skill"].update(type="noul"),
            lambda p: p["questions"]["skill"].update(criteria=["Only one level"]),
            lambda p: p["questions"]["skill"].update(criteria=[0, 1]),
            lambda p: p["state"].update(value=math.nan),
            lambda p: p["state"].update({1: "Non-string key"}),
        ):
            payload = request_fixture()
            mutate(payload)
            with patch("assessment_core.providers.jev.urllib.request.build_opener") as build:
                with self.assertRaises(JevError):
                    self.client.evaluate(payload)
                build.assert_not_called()

    def test_invalid_noul_questions_fail_before_any_transport(self):
        for question in (
            {"type": "noul"}, {"type": "noul", "instructions": ""},
            {"type": "boolean", "instructions": "Mandatory?"},
            {"type": "noul", "instructions": "Mandatory?", "threshold": 0.7},
            *({"type": "noul", "instructions": "Mandatory?", "criteria": criteria}
              for criteria in (["yes", "no"], {"yes": "Explicit"},
                               {"true": True}, {"true": None}, {"false": 0})),
        ):
            payload = request_fixture()
            payload["questions"]["mandatory"] = question
            with self.subTest(question=question), \
                    patch("assessment_core.providers.jev.urllib.request.build_opener") as build:
                with self.assertRaises(JevError) as raised:
                    self.client.evaluate(payload)
                self.assertEqual(raised.exception.code, "invalid_request")
                build.assert_not_called()

    def test_body_limits_are_enforced(self):
        with patch("assessment_core.providers.jev.urllib.request.build_opener") as build:
            with self.assertRaises(JevError) as raised:
                JevClient(JevConfig(api_key="key", max_request_bytes=5)).evaluate(request_fixture())
            self.assertEqual(raised.exception.code, "request_too_large")
            self.assertFalse(raised.exception.outcome_unknown)
            build.assert_not_called()
        opener = Mock()
        opener.open.return_value = self.incoming(b"123456")
        with patch("assessment_core.providers.jev.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(JevError) as raised:
                JevClient(JevConfig(api_key="key", max_response_bytes=5)).evaluate(request_fixture())
        self.assertEqual(raised.exception.code, "response_too_large")
        self.assertTrue(raised.exception.outcome_unknown)

    def test_malformed_json_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        valid = json.dumps(response_fixture())
        examples = [b"not JSON PRIVATE", b"\xff", b"[]", b'{"model":1,"model":2}',
                    valid.replace('"confidence": 0.45', '"confidence": NaN').encode(),
                    valid.replace('"confidence": 0.45', '"confidence": 1e999').encode(),
                    valid.replace('"score": 1.25', '"score": 1.25, "score": 1.25').encode()]
        for raw in examples:
            opener = Mock()
            opener.open.return_value = self.incoming(raw)
            with self.subTest(raw=raw[:40]), \
                    patch("assessment_core.providers.jev.urllib.request.build_opener", return_value=opener):
                with self.assertRaises(JevError) as raised:
                    self.client.evaluate(request_fixture())
                self.assertEqual(raised.exception.code, "invalid_response")
                self.assertNotIn("PRIVATE", str(raised.exception))
                opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
