"""Stateless, read-only candidate preview BFF. Python standard library only."""
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode, unquote
import http.client
import json
import mimetypes
import os
import re
import threading

from adapter import read_interviews, read_completed_directory
from adapter_errors import Problem

PREFIX = '/candidate-preview'
ID = r'[A-Za-z0-9_-]{1,160}'
DETAIL = re.compile(rf'/candidates/({ID})/(readiness|interviews|required-fields)$')
MATERIAL = re.compile(rf'/candidates/({ID})/interviews/({ID})/materials$')
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src https: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


def resolve_route(path):
    """Validate preview API target and return fixed upstream path; never a URL."""
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or '%' in parsed.path:
        raise ValueError('invalid route')
    route = parsed.path
    if route.startswith(PREFIX + '/api'):
        route = route[len(PREFIX + '/api'):]
    if route == '/candidates':
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) - {'offset', 'limit'} or any(len(v) != 1 for v in query.values()):
            raise ValueError('invalid pagination')
        values = {}
        for key, default, maximum in [('offset', '0', 10000000), ('limit', '500', 500)]:
            raw = query.get(key, [default])[0]
            if not re.fullmatch(r'[0-9]{1,8}', raw):
                raise ValueError('invalid pagination')
            value = int(raw)
            if value > maximum or (key == 'limit' and value < 1):
                raise ValueError('invalid pagination')
            values[key] = value
        return '/api/hr/selection/candidates?' + urlencode(values)
    if parsed.query or not (DETAIL.fullmatch(route) or MATERIAL.fullmatch(route)):
        raise ValueError('invalid route')
    return '/api/hr/selection' + route


def validate_origin(origin, host, allowed_origins=()):
    if origin is None:
        return True
    if origin in allowed_origins:
        return True
    parsed = urlsplit(origin)
    return (parsed.scheme in ('http', 'https') and parsed.netloc == host
            and not parsed.path and not parsed.query and not parsed.fragment
            and parsed.username is None)


@dataclass
class Config:
    host: str = '127.0.0.1'
    port: int = 8397
    upstream: str = 'http://127.0.0.1:8300'
    dist: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / 'web' / 'dist')
    timeout: float = 10.0
    max_body: int = 8 * 1024 * 1024
    max_concurrency: int = 16
    allowed_origins: tuple = ()
    catalog_max_candidates: int = 2000
    catalog_concurrency: int = 4
    catalog_timeout: float = 25.0
    core_url: str = ''
    core_key: str = field(default='', repr=False)
    source_url: str = ''
    source_key: str = field(default='', repr=False)

    def __post_init__(self):
        p = urlsplit(self.upstream)
        if (p.scheme != 'http' or p.hostname not in ('127.0.0.1', 'localhost', '::1')
                or p.username or p.password or p.path not in ('', '/') or p.query or p.fragment):
            raise ValueError('upstream must be a fixed local HTTP origin')
        _ = p.port
        if self.host not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('binding must be localhost')
        if not (1 <= self.catalog_concurrency <= 8 and 1 <= self.catalog_max_candidates <= 10000 and 0 < self.catalog_timeout <= 60):
            raise ValueError('invalid catalogue limits')
        self.dist = Path(self.dist).resolve()
        if self.timeout <= 0 or self.max_body < 1 or self.max_concurrency < 1:
            raise ValueError('invalid resource limits')


def create_server(config=None):
    cfg = config or Config()
    slots = threading.BoundedSemaphore(cfg.max_concurrency)
    catalog_slots = threading.BoundedSemaphore(1)

    class BoundedServer(ThreadingHTTPServer):
        def __init__(self, *args):
            self.request_slots = threading.BoundedSemaphore(cfg.max_concurrency + 4)
            super().__init__(*args)

        def get_request(self):
            request, address = super().get_request()
            request.settimeout(cfg.timeout)
            return request, address

        def process_request(self, request, client_address):
            if not self.request_slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except Exception:
                self.request_slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.request_slots.release()

        def handle_error(self, request, client_address):
            pass

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'CandidatePreview'
        sys_version = ''

        def log_message(self, *args):
            pass  # Never log paths, tokens, response data, or exception details.

        def reply(self, status, body, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            for name, value in [('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'),
                                ('Content-Security-Policy', CSP), ('X-Content-Type-Options', 'nosniff'),
                                ('Connection', 'close')]:
                self.send_header(name, value)
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)

        def error(self, status, code):
            self.reply(status, json.dumps({'error': code}).encode())

        def do_GET(self):
            host = self.headers.get('Host', '')
            if (len(self.headers.get_all('Host', [])) != 1 or not host
                    or not validate_origin(self.headers.get('Origin'), host, cfg.allowed_origins)
                    or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                return self.error(403, 'origin_not_allowed')
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
                return self.error(400, 'request_body_not_allowed')
            if self.path in (PREFIX + '/api/health', PREFIX + '/health'):
                return self.reply(200, b'{"ok":true}')
            if self.path.startswith(PREFIX + '/api/'):
                return self.proxy()
            return self.static()

        def proxy(self):
            try:
                target = resolve_route(self.path)
            except ValueError:
                return self.error(400, 'invalid_route')
            tokens = self.headers.get_all('Authorization', [])
            if len(tokens) != 1 or not re.fullmatch(r'Bearer [A-Za-z0-9._~+/-]+=*', tokens[0]):
                return self.error(401, 'authorization_required')
            if not slots.acquire(blocking=False):
                return self.error(503, 'busy')
            try:
                def directory_get(path):
                    status, body = self.upstream_get(path, tokens[0])
                    if status != 200:
                        raise Problem('directory_authorization_failed', status if status in (401, 403) else 502)
                    return json.loads(body)
                if target.startswith('/api/hr/selection/candidates?'):
                    if not catalog_slots.acquire(blocking=False):
                        raise Problem('directory_verification_busy', 503)
                    try:
                        query = parse_qs(urlsplit(target).query)
                        result = read_completed_directory(cfg, int(query['offset'][0]), int(query['limit'][0]), directory_get)
                        body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
                        if len(body) > cfg.max_body:
                            raise Problem('upstream_response_too_large', 502)
                        return self.reply(200, body)
                    finally:
                        catalog_slots.release()
                route = target[len('/api/hr/selection'):]
                listing = DETAIL.fullmatch(route)
                materials = MATERIAL.fullmatch(route)
                if materials or (listing and listing.group(2) == 'interviews'):
                    cid = materials.group(1) if materials else listing.group(1)
                    iid = materials.group(2) if materials else None
                    result = read_interviews(cfg, cid, iid, directory_get)
                    body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
                    if len(body) > cfg.max_body:
                        raise Problem('upstream_response_too_large', 502)
                    return self.reply(200, body)
                status, body = self.upstream_get(target, tokens[0])
                return self.reply(status, body)
            except Problem as exc:
                return self.error(exc.status, exc.message)
            except (OSError, http.client.HTTPException, ValueError, UnicodeError):
                return self.error(502, 'upstream_unavailable')
            finally:
                slots.release()

        def upstream_get(self, target, token):
            connection = None
            try:
                upstream = urlsplit(cfg.upstream)
                connection = http.client.HTTPConnection(upstream.hostname, upstream.port or 80, timeout=cfg.timeout)
                connection.request('GET', target, headers={'Authorization': token, 'Accept': 'application/json'})
                response = connection.getresponse()
                if 300 <= response.status < 400:
                    raise Problem('upstream_redirect_rejected', 502)
                if response.getheader('Content-Encoding', 'identity') != 'identity':
                    raise Problem('invalid_upstream_response', 502)
                body = response.read(cfg.max_body + 1)
                if len(body) > cfg.max_body:
                    raise Problem('upstream_response_too_large', 502)
                media = response.getheader('Content-Type', '').split(';')[0].strip().lower()
                if media != 'application/json' and not media.endswith('+json'):
                    raise Problem('invalid_upstream_response', 502)
                json.loads(body)
                return response.status, body
            except (OSError, http.client.HTTPException, ValueError, UnicodeError):
                raise Problem('upstream_unavailable', 502) from None
            finally:
                if connection:
                    connection.close()

        def static(self):
            path = urlsplit(self.path)
            if path.query or not path.path.startswith(PREFIX + '/'):
                return self.error(404, 'not_found')
            relative = unquote(path.path[len(PREFIX) + 1:])
            if '\\' in relative or '\x00' in relative or any(p in ('.', '..') for p in relative.split('/')):
                return self.error(404, 'not_found')
            candidate = (cfg.dist / (relative or 'index.html')).resolve()
            if not candidate.is_relative_to(cfg.dist) or not candidate.is_file():
                return self.error(404, 'not_found')
            try:
                if candidate.stat().st_size > cfg.max_body:
                    return self.error(413, 'file_too_large')
                body = candidate.read_bytes()
            except OSError:
                return self.error(404, 'not_found')
            return self.reply(200, body, mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream')

        def reject_method(self):
            self.error(405, 'method_not_allowed')

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_CONNECT = do_TRACE = reject_method

    server = BoundedServer((cfg.host, cfg.port), Handler)
    server.daemon_threads = True
    return server


if __name__ == '__main__':
    config = Config(port=int(os.environ.get('CANDIDATE_PREVIEW_PORT', '8397')),
                    upstream=os.environ.get('CANDIDATE_PREVIEW_UPSTREAM', 'http://127.0.0.1:8300'),
                    core_url=os.environ.get('HR_MATERIALS_CORE_URL', ''),
                    core_key=os.environ.get('HR_MATERIALS_CORE_KEY', ''),
                    source_url=os.environ.get('HR_MATERIALS_SOURCE_URL', ''),
                    source_key=os.environ.get('HR_MATERIALS_SOURCE_KEY', ''))
    create_server(config).serve_forever()
