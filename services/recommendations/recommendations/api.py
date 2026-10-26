"""Authenticated loopback HTTP API with bounded payload and concurrency."""

from __future__ import annotations

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import threading
import time
import uuid

from . import __version__
from .engine import InvalidInput, VERSION, canonical_json, default_rubric, digest, rubric_parameters
from .store import ResultStore, StoreUnavailable

MAX_REQUEST_BYTES = 16 * 1024 * 1024
READ_TIMEOUT_SECONDS = 15
MAX_CONCURRENT_REQUESTS = 8
RESULT_ROUTE = re.compile(r"/v1/recommendations/([a-f0-9]{64})")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput()
        result[key] = value
    return result


def reject_constant(_value):
    raise InvalidInput()


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                body = b'{"error":"service_busy"}'
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\n"
                                b"Cache-Control: no-store\r\nConnection: close\r\nContent-Length: "
                                + str(len(body)).encode() + b"\r\n\r\n" + body)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Metrics:
    def __init__(self):
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.requests = self.errors = 0
        self.total_seconds = self.max_seconds = 0
        self.last_operation_id = None

    def record(self, code, elapsed, operation_id):
        with self.lock:
            self.requests += 1
            self.errors += code >= 400
            self.total_seconds += elapsed
            self.max_seconds = max(self.max_seconds, elapsed)
            if operation_id:
                self.last_operation_id = operation_id

    def snapshot(self):
        with self.lock:
            return {"uptime_seconds": time.monotonic() - self.started,
                    "requests": self.requests, "errors": self.errors,
                    "request_seconds_total": self.total_seconds,
                    "request_seconds_max": self.max_seconds,
                    "last_operation_id": self.last_operation_id}


def handler(store, service_token, *, metrics=None, log=None):
    if type(service_token) is not str or not re.fullmatch(r"[a-fA-F0-9]{64}", service_token):
        raise ValueError("64-character hexadecimal service token required")
    expected_auth = ("Bearer " + service_token).encode("ascii")
    metrics = Metrics() if metrics is None else metrics
    rubric = default_rubric() if store is None or store.rubric is None else store.rubric
    rubric_parameters(rubric)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args):
            pass

        def setup(self):
            self.request.settimeout(READ_TIMEOUT_SECONDS)
            super().setup()

        def send_json(self, status, value):
            body = canonical_json(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Request-ID", self.request_id)
            self.end_headers()
            self.response_code = status
            if self.command != "HEAD":
                self.wfile.write(body)

        def handle_request(self):
            started = time.monotonic()
            self.request_id = uuid.uuid4().hex
            self.operation_id = None
            self.response_code = 503
            try:
                self.dispatch()
            except (BrokenPipeError, ConnectionResetError):
                # A disconnected caller can retrieve a committed result later.
                pass
            except (TimeoutError, socket.timeout):
                self.send_json(408, {"error": "request_timeout"})
            except InvalidInput as error:
                self.send_json(422, {"error": error.code})
            except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
                self.send_json(422, {"error": "invalid_input"})
            except StoreUnavailable as error:
                self.send_json(404 if str(error) == "not_found" else 503,
                               {"error": "not_found" if str(error) == "not_found" else "store_unavailable"})
            except (sqlite3.Error, OSError):
                self.send_json(503, {"error": "service_unavailable"})
            except Exception:
                self.send_json(503, {"error": "service_unavailable"})
            finally:
                elapsed = time.monotonic() - started
                metrics.record(self.response_code, elapsed, self.operation_id)
                if log is not None:
                    # No user-supplied path/header/body or freeform error text.
                    log({"request_id": self.request_id, "operation_id": self.operation_id,
                         "status": self.response_code, "duration_seconds": elapsed})

        def dispatch(self):
            if self.command == "GET" and self.path == "/healthz":
                return self.send_json(200, {"status": "ok", "version": __version__, "service": "recommendations"})
            auth = self.headers.get_all("Authorization", [])
            if len(auth) != 1 or not hmac.compare_digest(auth[0].encode("utf-8"), expected_auth):
                return self.send_json(401, {"error": "unauthorized"})
            if self.command == "GET" and self.path == "/readyz":
                store.ready()
                return self.send_json(200, {"status": "ready", "version": __version__})
            if self.command == "GET" and self.path == "/v1/operator":
                return self.send_json(200, {"service": "recommendations", "version": __version__,
                                           "contract_version": VERSION, "rubric_sha256": digest(rubric),
                                           "limits": {"pool": 1000, "request_bytes": MAX_REQUEST_BYTES,
                                                      "concurrent_requests": MAX_CONCURRENT_REQUESTS},
                                           "metrics": metrics.snapshot(), "storage": store.metadata()})
            if self.command == "POST" and self.path == "/v1/recommendations":
                lengths = self.headers.get_all("Content-Length", [])
                if (len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0])
                        or self.headers.get("Transfer-Encoding") is not None):
                    return self.send_json(422, {"error": "invalid_content_length"})
                size = int(lengths[0])
                if size > MAX_REQUEST_BYTES:
                    return self.send_json(413, {"error": "request_too_large"})
                if size == 0:
                    return self.send_json(422, {"error": "invalid_input"})
                types = self.headers.get_all("Content-Type", [])
                if len(types) != 1 or self.headers.get_content_type() != "application/json":
                    return self.send_json(422, {"error": "invalid_content_type"})
                raw = self.rfile.read(size)
                if len(raw) != size:
                    return self.send_json(422, {"error": "incomplete_body"})
                value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
                result = store.submit(value)
                self.operation_id = result["recommendation_id"]
                return self.send_json(200, result)
            match = RESULT_ROUTE.fullmatch(self.path)
            if self.command == "GET" and match:
                self.operation_id = match[1]
                return self.send_json(200, store.get(match[1]))
            return self.send_json(404, {"error": "not_found"})

        do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_OPTIONS = do_HEAD = handle_request

    return Handler


def database_path():
    return os.environ.get("RECOMMENDATIONS_DB") or str(Path.home() / ".local/share/hr-vision-recommendations/results.sqlite3")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    resources = ExitStack()
    try:
        if argv:
            if len(argv) == 2 and argv[0] == "backup":
                result = ResultStore(database_path()).backup(argv[1])
            elif len(argv) == 3 and argv[0] == "restore":
                result = ResultStore.restore(argv[1], argv[2])
            else:
                raise ValueError("invalid command")
            print(canonical_json(result), flush=True)
            return 0
        token = os.environ.get("RECOMMENDATIONS_SERVICE_TOKEN", "")
        port = os.environ.get("RECOMMENDATIONS_PORT", "8099")
        if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
            raise ValueError("invalid port")
        if os.environ.get("RECOMMENDATIONS_HOST", "127.0.0.1") != "127.0.0.1":
            raise ValueError("loopback bind required")
        handler(None, token)
        store = ResultStore(database_path())
        resources.enter_context(store.runtime_lock())
        server = BoundedHTTPServer(("127.0.0.1", int(port)), handler(
            store, token, log=lambda value: print(canonical_json(value), flush=True)))
        resources.callback(server.server_close)
        store.ready()
    except (ValueError, OSError, sqlite3.Error, StoreUnavailable):
        resources.close()
        print('{"error":"service_configuration_error"}', flush=True)
        return 2
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        resources.close()
    return 0
