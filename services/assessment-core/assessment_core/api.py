"""Private localhost API and one durable queue consumer.

The authenticated HR backend supplies an already authorized complete corpus.
This process has no HR database access or tenant/source authorization logic.
HTTP and worker structure adapted from the independent analytics service.
"""

from __future__ import annotations

import hmac
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import threading

from . import __version__
from .engine import prepare
from .providers.jev import JevClient, JevConfig, JevError
from .runstore import RunStore, RunUnavailable


MAX_REQUEST_BYTES = 4 * 1024 * 1024
READ_TIMEOUT_SECONDS = 15
_PINNED_MODEL = re.compile(r"jev-\d+\.\d+\.\d+")
_RUN_ROUTE = re.compile(r"/v1/assessments/([a-f0-9]{64})")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonfinite JSON number")


def _public_error(error):
    """Only ledger diagnostic metadata crosses HTTP; never arbitrary messages."""
    if error is None:
        return None
    if type(error) is not dict:
        return {"code": "evaluation_failed"}
    code = error.get("code")
    result = {"code": code if type(code) is str and re.fullmatch(r"[a-z0-9_]{1,80}", code)
              else "evaluation_failed"}
    status = error.get("status")
    if status is None or type(status) is int and 100 <= status <= 599:
        result["status"] = status
    return result


def envelope(row, model):
    plan = row["plan"]
    return {
        "run_id": row["id"], "status": row["status"],
        "assessment": row["result"] if row["status"] == "completed" else None,
        "error": _public_error(row["error"]),
        "corpus_revision": plan["corpus"]["sha256"],
        "rubric_version": plan["rubric"]["version"],
        "model": plan["request"]["model"],
    }


def handler(store, model, service_token, runtime=None):
    if not isinstance(model, str) or not _PINNED_MODEL.fullmatch(model):
        raise ValueError("an explicit Jev model version is required")
    if not isinstance(service_token, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", service_token):
        raise ValueError("a 64-character hexadecimal service token is required")
    expected_auth = ("Bearer " + service_token).encode("ascii")

    class Handler(BaseHTTPRequestHandler):
        # Responses close connections, including rejected bodies that were not
        # read. No body or Authorization header is written to access logs.
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args):
            pass

        def setup(self):
            self.request.settimeout(READ_TIMEOUT_SECONDS)
            super().setup()

        def send_json(self, status, value):
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

        def handle_request(self):
            try:
                if self.command == "GET" and self.path == "/healthz":
                    return self.send_json(200, {"status": "ok", "version": __version__, "model": model})
                auth = self.headers.get_all("Authorization", [])
                if len(auth) != 1 or not hmac.compare_digest(auth[0].encode("utf-8"), expected_auth):
                    return self.send_json(401, {"error": "unauthorized"})
                if self.command == 'GET' and self.path == '/readyz':
                    store.ready()
                    if runtime and (runtime['stopping'].is_set() or not runtime.get('worker')
                                    or not runtime['worker'].is_alive()):
                        return self.send_json(503, {'status': 'not_ready'})
                    return self.send_json(200, {'status': 'ready', 'version': __version__, 'model': model})
                if self.command == 'GET' and self.path == '/v1/operator':
                    return self.send_json(200, {'service': 'assessment-core', 'version': __version__,
                        'contract_version': 'full-corpus-level-choice-v1', 'model': model,
                        'storage': store.diagnostics(), 'worker_running':
                            runtime['worker'].is_alive() if runtime and runtime.get('worker') else None})
                if self.command == "POST" and self.path == "/v1/assessments":
                    lengths = self.headers.get_all("Content-Length", [])
                    if (len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0])
                            or self.headers.get("Transfer-Encoding") is not None):
                        return self.send_json(422, {"error": "invalid_content_length"})
                    size = int(lengths[0])
                    if size > MAX_REQUEST_BYTES:
                        return self.send_json(413, {"error": "request_too_large"})
                    if size == 0:
                        return self.send_json(422, {"error": "invalid_input"})
                    if self.headers.get_content_type() != "application/json":
                        return self.send_json(422, {"error": "invalid_content_type"})
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        return self.send_json(422, {"error": "incomplete_body"})
                    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                                       parse_constant=_reject_constant)
                    if type(value) is not dict or set(value) != {"corpus", "rubric"}:
                        raise ValueError("invalid envelope")
                    plan = prepare(value["corpus"], value["rubric"], model=model)
                    return self.send_json(202, envelope(store.submit(plan), model))
                match = _RUN_ROUTE.fullmatch(self.path)
                if self.command == "GET" and match:
                    return self.send_json(200, envelope(store.get(match[1]), model))
                return self.send_json(404, {"error": "not_found"})
            except (TimeoutError, socket.timeout):
                return self.send_json(408, {"error": "request_timeout"})
            except (ValueError, TypeError, UnicodeError, RecursionError):
                return self.send_json(422, {"error": "invalid_input"})
            except RunUnavailable as error:
                return self.send_json(404 if str(error) == "run_not_found" else 503,
                                      {"error": "not_found" if str(error) == "run_not_found" else "run_unavailable"})
            except (OSError, KeyError):
                return self.send_json(503, {"error": "service_unavailable"})
            except Exception:
                return self.send_json(503, {"error": "service_unavailable"})

        do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_OPTIONS = do_HEAD = handle_request

    return Handler


def work_once(store, client):
    """Process one plan; return false on idle/failure so the loop backs off."""
    plan = store.next_queued()
    if plan is None:
        return False
    try:
        store.execute(plan, client)
    except Exception:
        # execute persists provider failure before raising. A saved response
        # awaiting presentation is also never re-enqueued or re-billed here.
        return False
    return True


def worker(store, client, stop_event):
    while not stop_event.is_set():
        try:
            worked = work_once(store, client)
        except Exception:
            worked = False
        if not worked:
            stop_event.wait(1)


def main():
    resources = ExitStack()
    try:
        token = os.environ.get("ASSESSMENT_CORE_SERVICE_TOKEN", "")
        model = os.environ.get("ASSESSMENT_CORE_MODEL", "jev-1.14.0")
        port_text = os.environ.get("ASSESSMENT_CORE_PORT", "8097")
        if not re.fullmatch(r"[0-9]{1,5}", port_text) or not 1 <= int(port_text) <= 65535:
            raise ValueError("invalid port")
        # Validate the private HTTP boundary before opening storage or a worker.
        handler(None, model, token)
        config = JevConfig.from_environment(model=model)
        path = os.environ.get("ASSESSMENT_CORE_DB") or str(Path.home() / ".local/share/hr-vision-assessment-core/runs.sqlite3")
        store = RunStore(path)
        resources.enter_context(store.runtime_lock())
        stop_event = threading.Event()
        runtime = {'stopping': stop_event, 'worker': None}
        server = ThreadingHTTPServer(("127.0.0.1", int(port_text)), handler(store, model, token, runtime))
        # A second process on the same port must fail before touching another
        # worker's in-flight ledger rows during startup recovery.
        try:
            store.recover()
        except Exception:
            server.server_close()
            raise
    except (ValueError, OSError, JevError, RunUnavailable):
        resources.close()
        print('{"error":"service_configuration_error"}')
        return 2
    thread = threading.Thread(target=worker, args=(store, JevClient(config), stop_event),
                              name="assessment-core-worker", daemon=True)
    runtime['worker'] = thread
    thread.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()
        # Keep database ownership until the in-flight provider call finishes.
        # A new process must not recover a still-running call as interrupted.
        thread.join()
        resources.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
