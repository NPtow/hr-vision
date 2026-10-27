"""Read-only interview integration with a fresh live manager membership fence."""
from contextlib import nullcontext
import http.client
import json
import re
from urllib.parse import urlsplit

from adapter_core import CoreClient, ServiceHTTP, SourceMedia
from adapter_reader import Reader
from adapter_errors import Problem, require


class ReadTransport(ServiceHTTP):
    """Fixed configured service origin; no mutations except remote access resolution."""
    def __init__(self, base_url, key, *, source=False, timeout=10, max_body=8 * 1024 * 1024):
        super().__init__(base_url, key, timeout=timeout)
        self.source, self.max_body = source, max_body

    def call(self, method, path, body=None):
        allowed = ((self.source and method == 'POST' and path == '/v1/materials/access') or
                   (not self.source and method == 'GET' and body is None and
                    re.fullmatch(r'/v1/(?:candidates/[^/?#]+/dossier|owners/interview/[^/?#]+/materials|assets/[^/?#]+/access)', path)))
        require(allowed, 'materials_operation_not_allowed', 403)
        return super().call(method, path, body)

    def _send(self, method, path, raw, forwarded=None):
        # Avoid proxies and redirects, and bound all server-to-server JSON reads.
        target = urlsplit(self.base_url)
        cls = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
        connection = cls(target.hostname, target.port, timeout=self.timeout)
        try:
            connection.request(method, path, body=raw, headers={
                'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json',
                'Content-Type': 'application/json'})
            response = connection.getresponse()
            require(not 300 <= response.status < 400, 'materials_redirect_rejected', 502)
            if response.status >= 400:
                status = response.status if response.status in (400, 401, 403, 404, 409, 410, 413, 422, 429) else 503
                raise Problem('materials_service_rejected', status)
            require(response.getheader('Content-Encoding', 'identity') == 'identity', 'materials_invalid_response', 502)
            media = response.getheader('Content-Type', '').split(';')[0].lower().strip()
            require(media == 'application/json' or media.endswith('+json'), 'materials_invalid_response', 502)
            content = response.read(self.max_body + 1)
            require(len(content) <= self.max_body, 'materials_response_too_large', 502)
            result = json.loads(content)
            require(isinstance(result, dict), 'materials_invalid_response', 502)
            return result
        except (OSError, http.client.HTTPException, ValueError, UnicodeError):
            raise Problem('materials_service_unavailable', 503) from None
        finally:
            connection.close()


def authorize_candidate(cid, directory_get):
    """Read fresh directory pages using the original caller, every callback."""
    offset, count, seen = 0, None, set()
    while True:
        page = directory_get('/api/hr/selection/candidates?offset=' + str(offset) + '&limit=500')
        require(isinstance(page, dict), 'directory_invalid_response', 502)
        rows, total, nxt = page.get('candidates'), page.get('count'), page.get('nextOffset')
        require(isinstance(rows, list) and type(total) is int and 0 <= total <= 1000000
                and type(page.get('offset')) is int and page['offset'] == offset
                and type(page.get('limit')) is int and page['limit'] == 500
                and (count is None or total == count) and offset <= total,
                'directory_invalid_response', 502)
        count = total
        require(len(rows) == min(500, total - offset), 'directory_incomplete', 502)
        found = False
        for row in rows:
            require(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id']
                    and row['id'] not in seen, 'directory_invalid_response', 502)
            seen.add(row['id'])
            found = found or row['id'] == cid
        end = offset + len(rows)
        require((end < total and type(nxt) is int and nxt == end and end > offset)
                or (end == total and nxt is None), 'directory_incomplete', 502)
        if found:
            return True
        if nxt is None:
            raise Problem('candidate_access_denied', 403)
        offset = nxt


def read_interviews(config, cid, iid, directory_get):
    require(config.core_url and config.core_key, 'interview_materials_configuration_unavailable', 503)
    try:
        core = CoreClient(ReadTransport(config.core_url, config.core_key, timeout=config.timeout, max_body=config.max_body))
        source = None
        if config.source_url and config.source_key:
            source = SourceMedia(ReadTransport(config.source_url, config.source_key, source=True,
                                               timeout=config.timeout, max_body=config.max_body))
        reader = Reader(core, lambda: nullcontext(None), lambda candidate: authorize_candidate(candidate, directory_get), source=source)
        return reader.materials(cid, iid) if iid is not None else reader.list(cid)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise Problem('interview_materials_invalid_response', 502) from None


def load_directory(directory_get, max_candidates=2000):
    """Fully verified manager directory, never a partial candidate pool."""
    rows, seen, offset, count = [], set(), 0, None
    while True:
        page = directory_get('/api/hr/selection/candidates?offset=' + str(offset) + '&limit=500')
        require(isinstance(page, dict), 'directory_invalid_response', 502)
        values, total, nxt = page.get('candidates'), page.get('count'), page.get('nextOffset')
        require(type(total) is int and 0 <= total <= max_candidates,
                'directory_verification_limit', 503)
        require(isinstance(values, list) and type(page.get('offset')) is int and page['offset'] == offset
                and type(page.get('limit')) is int and page['limit'] == 500
                and (count is None or total == count) and offset <= total,
                'directory_invalid_response', 502)
        count = total
        require(len(values) == min(500, total - offset), 'directory_incomplete', 502)
        for value in values:
            require(isinstance(value, dict) and isinstance(value.get('id'), str)
                    and re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value['id']) and value['id'] not in seen,
                    'directory_invalid_response', 502)
            seen.add(value['id'])
            rows.append(value)
        end = offset + len(values)
        require((end < total and type(nxt) is int and nxt == end and end > offset)
                or (end == total and nxt is None), 'directory_incomplete', 502)
        if nxt is None:
            return rows
        offset = nxt


def has_completed_recorded_interview(dossier, cid):
    """Only canonical completed HR interviews with an active available recording."""
    require(isinstance(dossier, dict) and dossier.get('candidate_id') == cid,
            'interview_candidate_identity_mismatch', 502)
    owners = dossier.get('interviews')
    require(isinstance(owners, list), 'interview_materials_invalid_inventory', 502)
    ids, found = set(), False
    for owner in owners:
        require(isinstance(owner, dict) and isinstance(owner.get('id'), str) and owner['id']
                and owner['id'] not in ids, 'interview_materials_invalid_inventory', 502)
        ids.add(owner['id'])
        if owner.get('status') != 'completed':
            continue
        data = owner.get('materials')
        require(isinstance(data, dict) and data.get('owner_kind') == 'interview'
                and data.get('owner_id') == owner['id'], 'interview_materials_owner_mismatch', 502)
        sources, assets = data.get('sources'), data.get('assets')
        require(isinstance(sources, list) and isinstance(assets, list), 'interview_materials_invalid_inventory', 502)
        by_source = {}
        for source in sources:
            require(isinstance(source, dict) and isinstance(source.get('id'), str) and source['id']
                    and source['id'] not in by_source, 'interview_materials_invalid_inventory', 502)
            by_source[source['id']] = source
        asset_ids = set()
        for asset in assets:
            require(isinstance(asset, dict) and isinstance(asset.get('id'), str) and asset['id']
                    and asset['id'] not in asset_ids and asset.get('source_id') in by_source,
                    'interview_materials_source_mismatch', 502)
            asset_ids.add(asset['id'])
            if (asset.get('kind') in ('audio', 'video') and asset.get('state') in ('ready', 'available')
                    and not by_source[asset['source_id']].get('revoked')):
                found = True
    return found


def read_completed_directory(config, offset, limit, directory_get):
    """Bounded canonical scan; errors invalidate the complete filtered response."""
    import time
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    from urllib.parse import quote
    require(config.core_url and config.core_key, 'interview_materials_configuration_unavailable', 503)
    started = time.monotonic()
    rows = load_directory(directory_get, config.catalog_max_candidates)
    deadline = started + config.catalog_timeout
    def check(candidate):
        require(time.monotonic() < deadline, 'directory_verification_timeout', 503)
        transport = ReadTransport(config.core_url, config.core_key,
                                  timeout=min(config.timeout, max(.01, deadline-time.monotonic())),
                                  max_body=config.max_body)
        dossier = transport.call('GET', '/v1/candidates/' + quote(candidate['id'], safe='') + '/dossier')
        return candidate['id'], has_completed_recorded_interview(dossier, candidate['id'])
    included = set()
    # Keep at most catalog_concurrency submissions live, including queued work.
    iterator = iter(rows)
    executor = ThreadPoolExecutor(max_workers=config.catalog_concurrency)
    pending = set()
    try:
        for _ in range(config.catalog_concurrency):
            candidate = next(iterator, None)
            if candidate is not None:
                pending.add(executor.submit(check, candidate))
        while pending:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'directory_verification_timeout', 503)
            completed, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            require(completed, 'directory_verification_timeout', 503)
            for future in completed:
                cid, matches = future.result()
                if matches:
                    included.add(cid)
                candidate = next(iterator, None)
                if candidate is not None:
                    pending.add(executor.submit(check, candidate))
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True)
    require(time.monotonic() < deadline, 'directory_verification_timeout', 503)
    fresh = load_directory(directory_get, config.catalog_max_candidates)
    require([r['id'] for r in fresh] == [r['id'] for r in rows], 'directory_access_changed', 409)
    filtered = [row for row in fresh if row['id'] in included]
    values = filtered[offset:offset + limit]
    end = offset + len(values)
    return {'candidates': values, 'count': len(filtered), 'offset': offset, 'limit': limit,
            'nextOffset': end if end < len(filtered) else None}
