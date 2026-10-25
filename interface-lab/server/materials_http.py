"""Bounded HTTPS material ingest. Checked IPs are pinned through TLS/redirects.

The worker resolves fresh provider URLs before calling download. No Range/resume:
each attempt starts a new private staging file, published only after verification.
"""
from dataclasses import dataclass
import hashlib
import ipaddress
import math
import os
from pathlib import Path
import queue
import re
import socket
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import HTTPError as UrllibHTTPError


class DownloadError(ValueError):
    def __init__(self, message, *, code='download_failed', retryable=False, remote_status=None):
        super().__init__(message)
        self.code, self.retryable, self.remote_status = code, retryable, remote_status


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size: int
    sha256: str
    content_type: str


_DNS_SLOTS = threading.BoundedSemaphore(4)
_REDIRECTS = frozenset((301, 302, 303, 307, 308))


def _public(address):
    # NAT64 addresses can embed private IPv4 destinations while is_global=True.
    if address.version == 6 and any(address in network for network in
            (ipaddress.ip_network('64:ff9b::/96'), ipaddress.ip_network('64:ff9b:1::/48'))):
        return False
    return bool(address.is_global and not (address.is_private or address.is_loopback or address.is_link_local or
                address.is_multicast or address.is_reserved or address.is_unspecified or
                getattr(address, 'is_site_local', False) or getattr(address, 'ipv4_mapped', None) or
                getattr(address, 'sixtofour', None) or getattr(address, 'teredo', None)))


def validate_url(value):
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 33 or ord(c) == 127 for c in value) or '\\' in value:
        raise DownloadError('Invalid material URL', code='unsafe_url')
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() != 'https' or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
            raise ValueError()
        host = (parsed.hostname or '').rstrip('.').encode('idna').decode('ascii').lower()
        if not host or '%' in host or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.invalid', '.test', '.onion')):
            raise ValueError()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
            if len(host) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.')):
                raise ValueError()
        if literal is not None and not _public(literal):
            raise ValueError()
        netloc = '[' + host + ']' if ':' in host else host
        return urlunsplit(('https', netloc, parsed.path or '/', parsed.query, ''))
    except (ValueError, UnicodeError):
        # Never include presigned URLs/headers in an exception or log.
        raise DownloadError('Only public HTTPS material URLs on port 443 are allowed', code='unsafe_url') from None


def resolve_public(host, timeout):
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _public(literal):
            raise DownloadError('Non-public material address', code='unsafe_url')
        return (socket.AF_INET6, (str(literal), 443, 0, 0)) if literal.version == 6 else (socket.AF_INET, (str(literal), 443))
    if not _DNS_SLOTS.acquire(blocking=False):
        raise DownloadError('Material DNS capacity exceeded', code='dns_busy', retryable=True)
    answer = queue.Queue(maxsize=1)

    def resolve():
        try:
            answer.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            answer.put(None)
        finally:
            _DNS_SLOTS.release()

    threading.Thread(target=resolve, daemon=True, name='material-dns').start()
    try:
        entries = answer.get(timeout=max(0.001, timeout))
    except queue.Empty:
        raise DownloadError('Material DNS timed out', code='timeout', retryable=True) from None
    if not entries:
        raise DownloadError('Material DNS failed', code='dns_failed', retryable=True)
    for family, _, _, _, endpoint in entries:
        if family not in (socket.AF_INET, socket.AF_INET6) or not _public(ipaddress.ip_address(endpoint[0])):
            raise DownloadError('Non-public material DNS answer', code='unsafe_url')
    return entries[0][0], entries[0][4]


class _PinnedAdapter(HTTPAdapter):
    """One checked origin/endpoint per adapter; no second DNS resolution.

    Preserve the original host for certificate verification, SNI and Host. An
    active deadline shuts down the socket even if headers/body trickle forever.
    """
    def __init__(self, host, endpoint, deadline):
        self.host, self.endpoint, self.deadline = host, endpoint, deadline
        self._sockets, self._lock, self._expired = [], threading.Lock(), False
        adapter = self

        class PinnedConnection(HTTPSConnection):
            def _new_conn(self):
                family, address = adapter.endpoint
                raw = socket.socket(family, socket.SOCK_STREAM)
                try:
                    adapter._track(raw)
                    raw.settimeout(max(0.001, min(10, adapter.deadline - time.monotonic())))
                    raw.connect(address)
                    return raw
                except BaseException:
                    raw.close()
                    raise

            def connect(self):
                super().connect()
                adapter._track(self.sock)

        self.pool = HTTPSConnectionPool(host, port=443, maxsize=1, block=True)
        self.pool.ConnectionCls = PinnedConnection
        super().__init__(max_retries=0)

    def _track(self, sock):
        with self._lock:
            if self._expired:
                sock.close()
                raise DownloadError('Material request timed out', code='timeout', retryable=True)
            self._sockets.append(sock)

    def expire(self):
        with self._lock:
            self._expired = True
            for sock in self._sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        if urlsplit(request.url).hostname != self.host or not verify or proxies:
            raise DownloadError('Unsafe material transport configuration', code='unsafe_url')
        return self.pool

    def close(self):
        self.expire()
        self.pool.close()
        super().close()


def download(source, destination, *, max_bytes, timeout_seconds=120, expected_size=None, expected_sha256=None, headers=None):
    if type(max_bytes) is not int or max_bytes <= 0 or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise DownloadError('Invalid material download limits', code='invalid_limits')
    if expected_size is not None and (type(expected_size) is not int or not 0 <= expected_size <= max_bytes):
        raise DownloadError('Invalid expected material size', code='invalid_limits')
    if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected_sha256)):
        raise DownloadError('Invalid expected SHA-256', code='invalid_limits')
    url = source if isinstance(source, str) else source.url
    current = validate_url(url)
    expires = None if isinstance(source, str) else getattr(source, 'expires_at', None)
    if expires is not None and (type(expires) not in (int, float) or not math.isfinite(expires) or expires <= time.time()):
        raise DownloadError('Material access link expired; resolve a fresh link', code='url_expired', retryable=True)
    supplied = dict(headers if headers is not None else ({} if isinstance(source, str) else source.headers))
    forbidden = {'host', 'range', 'connection', 'content-length', 'transfer-encoding', 'proxy-authorization', 'accept-encoding'}
    if any(not isinstance(k, str) or not isinstance(v, str) or '\r' in k + v or '\n' in k + v or k.lower() in forbidden for k, v in supplied.items()):
        raise DownloadError('Unsafe material request headers', code='unsafe_headers')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, stage_name = tempfile.mkstemp(prefix='.material-', suffix='.part', dir=destination.parent)
    stage = Path(stage_name)
    deadline = time.monotonic() + timeout_seconds
    try:
        with os.fdopen(fd, 'wb') as output:
            for redirect in range(5):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DownloadError('Material download timed out', code='timeout', retryable=True)
                parsed = urlsplit(current)
                endpoint = resolve_public(parsed.hostname, remaining)
                adapter = _PinnedAdapter(parsed.hostname, endpoint, deadline)
                session = requests.Session()
                session.trust_env = False  # No proxies, .netrc or ambient credentials.
                session.headers.clear()
                session.mount('https://', adapter)
                timer = threading.Timer(max(0.001, deadline - time.monotonic()), adapter.expire)
                timer.daemon = True
                timer.start()
                response = None
                try:
                    request_headers = {**supplied, 'Accept-Encoding': 'identity', 'User-Agent': 'MashaDashaMaterials/1.0'}
                    remaining = max(0.001, deadline - time.monotonic())
                    response = session.get(current, headers=request_headers, stream=True, allow_redirects=False,
                                           timeout=(min(10, remaining), min(30, remaining)), verify=True, proxies={})
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get('Location')
                        if redirect == 4 or not location:
                            raise DownloadError('Invalid or excessive material redirect', code='redirect_failed')
                        target = validate_url(urljoin(current, location))
                        if urlsplit(target).netloc != parsed.netloc:
                            supplied = {}  # All custom source headers may contain credentials.
                        current = target
                        continue
                    if response.status_code != 200:
                        raise DownloadError('Material source returned an unsuccessful status', code='remote_status',
                                            remote_status=response.status_code,
                                            retryable=response.status_code in (401, 403, 408, 429) or response.status_code >= 500)
                    if response.headers.get('Content-Encoding', 'identity').strip().lower() not in ('', 'identity'):
                        raise DownloadError('Compressed HTTP transfer is not supported', code='invalid_response')
                    length = response.headers.get('Content-Length')
                    if length is not None:
                        if not re.fullmatch(r'[0-9]{1,20}', length) or int(length) > max_bytes:
                            raise DownloadError('Material exceeds download limit or has invalid length', code='size_limit')
                        length = int(length)
                        if expected_size is not None and length != expected_size:
                            raise DownloadError('Material Content-Length mismatch', code='size_mismatch')
                    size, digest = 0, hashlib.sha256()
                    while True:
                        if time.monotonic() >= deadline:
                            raise DownloadError('Material download timed out', code='timeout', retryable=True)
                        chunk = response.raw.read1(min(64 * 1024, max_bytes + 1 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            raise DownloadError('Material exceeds download limit', code='size_limit')
                        output.write(chunk)
                        digest.update(chunk)
                    if length is not None and size != length or expected_size is not None and size != expected_size:
                        raise DownloadError('Material download is truncated', code='size_mismatch', retryable=True)
                    sha256 = digest.hexdigest()
                    if expected_sha256 is not None and sha256 != expected_sha256.lower():
                        raise DownloadError('Material SHA-256 mismatch', code='hash_mismatch')
                    output.flush()
                    os.fsync(output.fileno())
                    content_type = response.headers.get('Content-Type', 'application/octet-stream').split(';')[0].strip().lower()
                    break
                except DownloadError:
                    raise
                except (requests.RequestException, UrllibHTTPError, OSError) as error:
                    raise DownloadError('Material transport failed', code='transport_failed', retryable=True) from None
                finally:
                    timer.cancel()
                    if response is not None:
                        response.close()
                    adapter.close()
                    session.close()
            else:
                raise DownloadError('Too many material redirects', code='redirect_failed')
        os.replace(stage, destination)
        return DownloadResult(destination, size, sha256, content_type)
    finally:
        stage.unlink(missing_ok=True)
