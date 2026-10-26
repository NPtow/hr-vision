"""Bounded, single-attempt TypeSafe System One client (standard library only).

The adapter validates typed answers; it never changes scores, probabilities or
confidence. Each question sees the supplied state in full. Provider contract:
https://docs.typesafe.ai/primitives/{noul,score,choice} (2026-10-26).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
_VERSION = re.compile(r"jev-\d+\.\d+\.\d+")
_ALIASES = {"jev-latest", "jev-preview"}
_PROBABILITY_TOLERANCE = 1e-5
_CHOICE_PROBABILITY_QUANTUM = 0.01
_CHOICE_ROUNDING_EPSILON = 1e-6
# The documented examples return scores rounded to two decimal places. Allow
# half the final decimal plus the bounded probability-sum rounding error.
_SCORE_ROUNDING_TOLERANCE = 0.005
_MESSAGES = {
    "configuration": "Jev configuration is missing or invalid.",
    "invalid_request": "Jev request does not match the supported typed contract.",
    "request_too_large": "Jev request exceeds the configured size limit.",
    "context_limit": "The complete corpus and questions exceed Jev's context limit.",
    "invalid_response": "Jev returned an invalid or inconsistent typed response.",
    "response_too_large": "Jev response exceeds the configured size limit.",
    "redirect": "Jev redirected the request; redirects are disabled.",
    "authentication": "Jev rejected authentication or access.",
    "exhausted_balance": "Jev requires payment or additional credit.",
    "rate_limited": "Jev rate limit was reached.",
    "http_error": "Jev returned an unsuccessful HTTP status.",
    "timeout": "Jev request timed out; no automatic retry was attempted.",
    "network_error": "Jev network or TLS request failed; no automatic retry was attempted.",
}


class JevError(Exception):
    """Safe diagnostic metadata, without request content or provider error text."""

    def __init__(self, code: str, *, outcome_unknown: bool = False,
                 status: int | None = None, received_body: bytes | None = None):
        self.code = code if code in _MESSAGES else "http_error"
        self.outcome_unknown = outcome_unknown
        self.status = status
        # Private diagnostic only. Never part of exception text or HTTP errors.
        self.received_body = received_body
        super().__init__(_MESSAGES[self.code])


@dataclass(frozen=True)
class JevConfig:
    api_key: str = field(default="", repr=False)
    model: str = "jev-1.14.0"
    timeout_seconds: float = 60
    max_request_bytes: int = 4 * 1024 * 1024
    max_response_bytes: int = 2 * 1024 * 1024
    proxy_url: str = ""

    def __post_init__(self):
        if (not isinstance(self.api_key, str) or not self.api_key or type(self.proxy_url) is not str
                or any(char.isspace() or ord(char) < 33 or ord(char) > 126
                       for char in self.api_key)
                or not _valid_model(self.model)
                or not _finite_number(self.timeout_seconds)
                or self.timeout_seconds <= 0
                or any(type(value) is not int or value <= 0
                       for value in (self.max_request_bytes, self.max_response_bytes))):
            raise JevError("configuration")
        if self.proxy_url:
            try:
                proxy = urllib.parse.urlsplit(self.proxy_url)
                valid = (proxy.scheme == "socks5h" and proxy.hostname in ("127.0.0.1", "::1")
                         and proxy.port is not None and 1 <= proxy.port <= 65535
                         and not proxy.username and not proxy.password
                         and not proxy.path and not proxy.query and not proxy.fragment)
            except ValueError:
                valid = False
            if not valid:
                raise JevError("configuration")

    @classmethod
    def from_environment(cls, **settings):
        """Load only credentials from the environment; model changes are explicit."""
        key = next((os.environ[name].strip() for name in
                    ("ASSESSMENT_CORE_JEV_API_KEY", "ASSESSMENT_JEV_KEY",
                     "JEV_API_KEY", "TYPESAFE_API_KEY", "HR_JEV_API_KEY")
                    if os.environ.get(name, "").strip()), "")
        if not key:
            key_file = os.environ.get("JEV_API_KEY_FILE")
            path = Path(key_file).expanduser() if key_file else Path.home() / ".config/jev/api-key"
            try:
                # Keys are small; avoid reading an accidentally selected large file.
                with path.open("r", encoding="utf-8") as source:
                    key = source.read(4097).strip()
                if len(key) > 4096:
                    raise JevError("configuration")
            except (OSError, UnicodeError, ValueError):
                raise JevError("configuration") from None
        if "api_key" in settings:
            raise JevError("configuration")
        settings.setdefault("proxy_url", os.environ.get("ASSESSMENT_CORE_JEV_PROXY", ""))
        return cls(api_key=key, **settings)


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def is_choice_probability_distribution(values) -> bool:
    """Validate Choice mass without rewriting any provider probability.

    Retain the historical strict path for higher precision responses. Outside
    it, only two-decimal values can use accumulated half-quantum error, as
    declared by Vercel's native TypeSafe adapter (pinned source in README).
    The decimal-grid gate and positive-mass check are additional local guards;
    this bound does not reconstruct or certify an unrounded posterior.
    """
    try:
        probabilities = tuple(values)
    except TypeError:
        return False
    if (not probabilities or any(not _finite_number(value) or not 0 <= value <= 1
                                 for value in probabilities)):
        return False
    total = math.fsum(probabilities)
    if total <= 0:
        return False
    if math.isclose(total, 1, rel_tol=0, abs_tol=_PROBABILITY_TOLERANCE):
        return True
    if any(not math.isclose(value * 100, round(value * 100), rel_tol=0,
                            abs_tol=1e-9) for value in probabilities):
        return False
    return math.isclose(total, 1, rel_tol=0,
        abs_tol=len(probabilities) * _CHOICE_PROBABILITY_QUANTUM / 2 + _CHOICE_ROUNDING_EPSILON)


def _valid_model(value):
    return isinstance(value, str) and (value in _ALIASES or _VERSION.fullmatch(value) is not None)


def _json_value(value):
    """Reject non-JSON Python values and nonfinite numbers at every depth."""
    if value is None or type(value) in (str, bool):
        return True
    if type(value) in (int, float):
        return _finite_number(value)
    if type(value) is list:
        return all(_json_value(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _json_value(item) for key, item in value.items())
    return False


def _description(value, *, nullable=False):
    return (nullable and value is None) or type(value) in (str, dict, list)


def _same_json(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(_same_json(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _validate_request(payload):
    try:
        valid = (type(payload) is dict and set(payload) == {"model", "state", "questions"}
                 and _valid_model(payload["model"])
                 and type(payload["state"]) in (str, dict, list)
                 and type(payload["questions"]) is dict and bool(payload["questions"])
                 and _json_value(payload))
        if not valid:
            raise JevError("invalid_request")
        for name, question in payload["questions"].items():
            if (not name or type(question) is not dict
                    or not {"type", "instructions"} <= set(question)
                    or not set(question) <= {"type", "instructions", "criteria"}
                    or not _description(question["instructions"])
                    or not question["instructions"]):
                raise JevError("invalid_request")
            criteria = question.get("criteria")
            if question["type"] == "noul":
                # Noul's criteria are optional; its result is p(yes), not a
                # binary decision or a second, invented confidence value.
                valid = (criteria is None or
                         (type(criteria) is dict and set(criteria) <= {"true", "false"}
                          and all(_description(value) for value in criteria.values())))
            elif question["type"] == "score":
                valid = (type(criteria) is list and 2 <= len(criteria) <= 10
                         and all(_description(value) for value in criteria))
            elif question["type"] == "choice":
                valid = (type(criteria) is dict and 1 <= len(criteria) <= 255
                         and all(name and _description(value, nullable=True)
                                 for name, value in criteria.items()))
            else:
                valid = False
            if not valid:
                raise JevError("invalid_request")
    except (KeyError, TypeError, ValueError, RecursionError):
        raise JevError("invalid_request") from None


def validate_response(payload: dict, response: dict) -> dict:
    """Validate the exact answer set and preserve the original response object."""
    _validate_request(payload)
    try:
        if (type(response) is not dict or set(response) != {"model", "answers", "usage"}
                or not _json_value(response) or not _valid_model(response["model"])
                or response["model"] in _ALIASES
                or (payload["model"] not in _ALIASES and response["model"] != payload["model"])):
            raise ValueError
        usage = response["usage"]
        if (type(usage) is not dict or set(usage) != {"input_tokens", "output_tokens"}
                or any(type(value) is not int or value < 0 for value in usage.values())):
            raise ValueError
        answers = response["answers"]
        if type(answers) is not dict or set(answers) != set(payload["questions"]):
            raise ValueError
        for name, question in payload["questions"].items():
            answer, kind = answers[name], question["type"]
            fields = ({"type", "noul"} if kind == "noul"
                      else {"type", kind, "confidence", "probabilities"})
            if kind == "score":
                fields.add("legend")
            if type(answer) is not dict or set(answer) != fields or answer["type"] != kind:
                raise ValueError
            if kind == "noul":
                if not _finite_number(answer["noul"]) or not 0 <= answer["noul"] <= 1:
                    raise ValueError
                continue
            criteria = question["criteria"]
            expected = ({str(index) for index in range(len(criteria))}
                        if kind == "score" else set(criteria))
            probabilities = answer["probabilities"]
            if type(probabilities) is not dict or set(probabilities) != expected:
                raise ValueError
            if any(not _finite_number(value) or not 0 <= value <= 1
                   for value in probabilities.values()):
                raise ValueError
            valid_mass = (is_choice_probability_distribution(probabilities.values())
                          if kind == "choice" else
                          math.isclose(math.fsum(probabilities.values()), 1,
                                       rel_tol=0, abs_tol=_PROBABILITY_TOLERANCE))
            if not valid_mass:
                raise ValueError
            if not _finite_number(answer["confidence"]) or not 0 <= answer["confidence"] <= 1:
                raise ValueError
            if kind == "score":
                maximum = len(criteria) - 1
                if not _finite_number(answer["score"]) or not 0 <= answer["score"] <= maximum:
                    raise ValueError
                mean = math.fsum(index * probabilities[str(index)] for index in range(len(criteria)))
                if not math.isclose(answer["score"], mean, rel_tol=0,
                                    abs_tol=_SCORE_ROUNDING_TOLERANCE + maximum * _PROBABILITY_TOLERANCE):
                    raise ValueError
                if not _same_json(answer["legend"], {str(index): value for index, value in enumerate(criteria)}):
                    raise ValueError
            else:
                choice = answer["choice"]
                if (type(choice) is not str or choice not in expected
                        or probabilities[choice] != max(probabilities.values())):
                    raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise JevError("invalid_response", outcome_unknown=True) from None
    return response


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonfinite JSON number")


def _decode_response(payload, raw):
    try:
        response = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
        return validate_response(payload, response)
    except JevError as error:
        error.received_body = raw
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise JevError('invalid_response', outcome_unknown=True, received_body=raw) from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_error(status, raw=b''):
    # Only this documented/observed machine code is retained. Error bodies may
    # echo private input, so never expose their free-form messages or fields.
    if status == 400:
        try:
            if json.loads(raw).get('detail', {}).get('error_type') == 'max_tokens_exceeded':
                return JevError('context_limit', status=status, received_body=raw)
        except (ValueError, TypeError, AttributeError):
            pass
    code = ({401: "authentication", 403: "authentication", 402: "exhausted_balance",
             413: "request_too_large", 429: "rate_limited"}.get(status, "http_error"))
    if 300 <= status < 400:
        code = "redirect"
    return JevError(code, status=status, outcome_unknown=status >= 500, received_body=raw)


class JevClient:
    def __init__(self, config: JevConfig):
        self.config = config

    def evaluate(self, payload: dict) -> dict:
        """Send one POST, with optional explicit loopback egress and no retries."""
        _validate_request(payload)
        if payload["model"] != self.config.model:
            raise JevError("invalid_request")
        try:
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise JevError("invalid_request") from None
        if len(data) > self.config.max_request_bytes:
            raise JevError("request_too_large")
        if self.config.proxy_url:
            return self._via_proxy(payload, data)
        request = urllib.request.Request(ENDPOINT, data=data, method="POST", headers={
            "Authorization": "Bearer " + self.config.api_key,
            "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "AssessmentCore/1.0",
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=self.config.timeout_seconds) as incoming:
                if incoming.geturl() != ENDPOINT:
                    raise JevError("redirect", outcome_unknown=True)
                if incoming.getcode() != 200:
                    raise _http_error(incoming.getcode())
                raw = incoming.read(self.config.max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            try:
                diagnostic = error.read(4096)
            except OSError:
                diagnostic = b''
            finally:
                error.close()
            raise _http_error(error.code, diagnostic) from None
        except (TimeoutError, socket.timeout):
            raise JevError("timeout", outcome_unknown=True) from None
        except urllib.error.URLError as error:
            code = "timeout" if isinstance(error.reason, (TimeoutError, socket.timeout)) else "network_error"
            raise JevError(code, outcome_unknown=True) from None
        except (OSError, ValueError, http.client.HTTPException):
            raise JevError("network_error", outcome_unknown=True) from None
        if len(raw) > self.config.max_response_bytes:
            raise JevError("response_too_large", outcome_unknown=True)
        return _decode_response(payload, raw)

    def _via_proxy(self, payload, data):
        """Reuse analytics' curl stdin boundary for the existing loopback egress.

        Nothing is installed or reconfigured. No key/source text in argv, no
        ambient proxy, redirects or POST retries. curl >= 8.4 bounds streaming
        downloads too; deployment verifies that prerequisite.
        """
        def quote(value):
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'
        config = '\n'.join(key + ' = ' + quote(value) for key, value in (
            ('url', ENDPOINT), ('request', 'POST'),
            ('header', 'Content-Type: application/json'),
            ('header', 'Authorization: Bearer ' + self.config.api_key),
            ('proxy', self.config.proxy_url), ('data', data.decode('utf-8')))) + '\n'
        command = ['/usr/bin/curl', '--disable', '--config', '-', '--silent', '--show-error',
            '--max-time', str(self.config.timeout_seconds), '--connect-timeout', '8',
            '--max-filesize', str(self.config.max_response_bytes), '--noproxy', '', '--globoff',
            '--proto', '=https', '--max-redirs', '0', '--write-out', '\n%{http_code}']
        try:
            with tempfile.TemporaryFile() as output:
                process = subprocess.run(command, input=config.encode('utf-8'), stdout=output,
                    stderr=subprocess.DEVNULL, timeout=self.config.timeout_seconds + 3,
                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C'}, check=False)
                if process.returncode:
                    code = {28: 'timeout', 63: 'response_too_large'}.get(process.returncode, 'network_error')
                    raise JevError(code, outcome_unknown=True)
                if output.seek(0, 2) > self.config.max_response_bytes + 4:
                    raise JevError('response_too_large', outcome_unknown=True)
                output.seek(0)
                raw = output.read(self.config.max_response_bytes + 5)
        except subprocess.TimeoutExpired:
            raise JevError('timeout', outcome_unknown=True) from None
        except OSError:
            raise JevError('network_error', outcome_unknown=True) from None
        if not re.search(rb'\n[0-9]{3}$', raw):
            raise JevError('invalid_response', outcome_unknown=True)
        status = int(raw[-3:])
        if status != 200:
            raise _http_error(status, raw[:-4])
        return _decode_response(payload, raw[:-4])
