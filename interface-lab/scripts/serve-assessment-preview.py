#!/usr/bin/env python3
"""Read-only, loopback-only access to private assessment preview material.

No candidate payloads belong in the Storybook bundle. Keep assessment.json,
candidates.json and resumes in a private directory and point this server there.
The remote video is streamed without caching or downloading a complete file.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEFAULT_DATA_DIR = Path.home() / "Library/Application Support/HRVision/assessment-preview-dsa-20261025"
ALLOWED_ORIGINS = frozenset(("http://127.0.0.1:6032", "http://localhost:6032"))
VIDEO_HOST = "kabinet.158-160-179-53.sslip.io"
CHUNK_SIZE = 64 * 1024
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)\Z")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def approved_video_url(candidate_id: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme == "https"
        and parsed.hostname == VIDEO_HOST
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and parsed.path == f"/api/video/{candidate_id}"
        and not parsed.query
        and not parsed.fragment
    ):
        return value
    return None


def parse_range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    match = RANGE_PATTERN.fullmatch(value)
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError("Invalid range")
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError("Invalid suffix")
        return max(0, size - suffix), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable range")
    return start, end


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, data_dir: Path, port: int = 6041, opener=None):
        self.data_dir = data_dir.expanduser().resolve(strict=True)
        payload = json.loads((self.data_dir / "candidates.json").read_text())
        self.candidates = {}
        for row in payload["candidates"]:
            candidate_id = str(row.get("id", ""))
            if not ID_PATTERN.fullmatch(candidate_id):
                continue
            self.candidates[candidate_id] = row
        self.opener = opener or build_opener(NoRedirect())
        # Binding is intentionally not configurable. External interfaces are forbidden.
        super().__init__(("127.0.0.1", port), PreviewHandler)


class PreviewHandler(BaseHTTPRequestHandler):
    server: PreviewServer
    protocol_version = "HTTP/1.1"
    server_version = "PrivateAssessmentPreview"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(45)

    def log_message(self, format, *args):
        # No URLs, candidate identities, bodies or remote credentials in logs.
        pass

    def _authorized(self) -> bool:
        port = self.server.server_port
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if len(hosts) != 1 or hosts[0] not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._error(HTTPStatus.FORBIDDEN)
            return False
        if len(origins) > 1 or (origins and origins[0] not in ALLOWED_ORIGINS):
            self._error(HTTPStatus.FORBIDDEN)
            return False
        # Blocks cross-site no-cors embeds/navigation which omit an Origin header.
        if not origins and self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._error(HTTPStatus.FORBIDDEN)
            return False
        return True

    def _headers(self, status: int, content_type: str, length: int | None, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Vary", "Origin")
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges")
        if length is not None:
            self.send_header("Content-Length", str(length))
        else:
            self.send_header("Connection", "close")
            self.close_connection = True
        for name, value in (extra or {}).items():
            self.send_header(name, str(value))
        self.end_headers()

    def _error(self, status: int, extra=None):
        body = (HTTPStatus(status).phrase + "\n").encode()
        self._headers(status, "text/plain; charset=utf-8", len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._authorized():
            return
        requested = self.headers.get("Access-Control-Request-Method", "GET")
        requested_headers = {part.strip().lower() for part in self.headers.get("Access-Control-Request-Headers", "").split(",") if part.strip()}
        if requested not in ("GET", "HEAD") or not requested_headers.issubset({"range", "if-range"}):
            self._error(HTTPStatus.FORBIDDEN)
            return
        if not self._route():
            self._error(HTTPStatus.NOT_FOUND)
            return
        self._headers(HTTPStatus.NO_CONTENT, "text/plain", 0, {
            "Access-Control-Allow-Methods": "GET, HEAD",
            "Access-Control-Allow-Headers": "Range, If-Range",
        })

    def do_POST(self):
        self.close_connection = True
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, {"Allow": "GET, HEAD, OPTIONS"})

    do_PUT = do_DELETE = do_PATCH = do_POST

    def do_HEAD(self):
        self.do_GET()

    def _route(self):
        if self.path == "/assessment":
            return "assessment", None
        for kind, pattern in (("resume", r"/resume/([A-Za-z0-9_-]{1,64})\.pdf"), ("video", r"/video/([A-Za-z0-9_-]{1,64})")):
            match = re.fullmatch(pattern, self.path)
            if match and match.group(1) in self.server.candidates:
                return kind, match.group(1)
        return None

    def do_GET(self):
        if not self._authorized():
            return
        route = self._route()
        if route is None:
            self._error(HTTPStatus.NOT_FOUND)
            return
        kind, candidate_id = route
        try:
            if kind == "assessment":
                self._file(self.server.data_dir / "assessment.json", "application/json; charset=utf-8")
            elif kind == "resume":
                # Never use arbitrary paths from data or request; symlinks must stay in this directory.
                allowed_dir = (self.server.data_dir / "resumes").resolve(strict=True)
                file_path = (allowed_dir / f"{candidate_id}.pdf").resolve(strict=True)
                if file_path.parent != allowed_dir:
                    self._error(HTTPStatus.FORBIDDEN)
                    return
                self._file(file_path, "application/pdf")
            else:
                self._video(candidate_id)
        except FileNotFoundError:
            self._error(HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True

    def _file(self, file_path: Path, content_type: str):
        # Open first, then fstat: atomic replacement of assessment.json is supported.
        with file_path.open("rb") as stream:
            import os
            size = os.fstat(stream.fileno()).st_size
            try:
                requested_range = parse_range(self.headers.get("Range"), size)
            except ValueError:
                self._error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, {"Content-Range": f"bytes */{size}"})
                return
            extra = {"Accept-Ranges": "bytes"}
            status = HTTPStatus.OK
            start, end = 0, size - 1
            if requested_range is not None:
                start, end = requested_range
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
                status = HTTPStatus.PARTIAL_CONTENT
            length = end - start + 1
            self._headers(status, content_type, length, extra)
            if self.command == "HEAD":
                return
            stream.seek(start)
            self._copy(stream, length)

    def _copy(self, stream, remaining: int | None):
        while remaining is None or remaining > 0:
            chunk = stream.read(CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining))
            if not chunk:
                # A truncated upstream must not turn into a reusable ambiguous connection.
                if remaining:
                    self.close_connection = True
                break
            self.wfile.write(chunk)
            if remaining is not None:
                remaining -= len(chunk)

    def _video(self, candidate_id: str):
        video_url = approved_video_url(candidate_id, self.server.candidates[candidate_id].get("videoUrl"))
        if video_url is None:
            self._error(HTTPStatus.NOT_FOUND)
            return
        headers = {"Accept-Encoding": "identity", "User-Agent": "HRVision-Private-Preview/1.0"}
        requested_range = self.headers.get("Range")
        if requested_range is not None:
            match = RANGE_PATTERN.fullmatch(requested_range)
            if not match or not any(match.groups()) or len(requested_range) > 80:
                self._error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            headers["Range"] = requested_range
        if_range = self.headers.get("If-Range")
        if if_range and len(if_range) <= 256:
            headers["If-Range"] = if_range
        request = Request(video_url, headers=headers, method=self.command)
        try:
            upstream = self.server.opener.open(request, timeout=30)
        except HTTPError as error:
            with error:
                if error.code == 416:
                    content_range = error.headers.get("Content-Range")
                    extra = {"Content-Range": content_range} if content_range else None
                    self._error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, extra)
                else:
                    self._error(HTTPStatus.BAD_GATEWAY)
            return
        except (URLError, TimeoutError, OSError):
            self._error(HTTPStatus.BAD_GATEWAY)
            return
        with upstream:
            if upstream.status not in (200, 206):
                self._error(HTTPStatus.BAD_GATEWAY)
                return
            raw_length = upstream.headers.get("Content-Length")
            if raw_length is not None and not raw_length.isdigit():
                self._error(HTTPStatus.BAD_GATEWAY)
                return
            length = int(raw_length) if raw_length else None
            extra = {key: upstream.headers[key] for key in ("Content-Range", "Accept-Ranges", "ETag", "Last-Modified") if key in upstream.headers}
            if upstream.status == 206 and "Content-Range" not in extra:
                self._error(HTTPStatus.BAD_GATEWAY)
                return
            self._headers(upstream.status, upstream.headers.get("Content-Type", "video/mp4"), length, extra)
            if self.command != "HEAD":
                self._copy(upstream, length)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--port", type=int, default=6041)
    args = parser.parse_args()
    server = PreviewServer(args.data_dir, args.port)
    print(f"Private assessment preview listening on http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
