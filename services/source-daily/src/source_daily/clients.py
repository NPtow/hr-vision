"""Bounded HTTP clients to trusted configured services; never accept URLs from DTOs."""
import json
from pathlib import Path
from urllib.parse import quote
import requests
from .config import endpoint
from .errors import ServiceError


class JsonHTTP:
    def __init__(self, base_url, token, *, session=None, max_response_bytes=24*1024*1024):
        self.base_url, self.token = endpoint(base_url), token
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.max_response_bytes = max_response_bytes

    @property
    def configured(self):
        return bool(self.base_url and self.token)

    def request(self, method, path, *, body=None, data=None, headers=None):
        if not self.configured:
            raise ServiceError('service_not_configured', 503, retryable=True)
        request_headers = {'Authorization': 'Bearer ' + self.token, **(headers or {})}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()
            request_headers['Content-Type'] = 'application/json'
        try:
            with self.session.request(method, self.base_url + path, data=data, headers=request_headers,
                    timeout=(5, 120), stream=True, allow_redirects=False) as response:
                if response.status_code >= 300:
                    # No exception text, remote body or arbitrary URL is retained.
                    status = response.status_code
                    # Only this fixed, documented code changes orchestration.
                    # Infrastructure 503 must remain retryable, not look unconfigured.
                    if status == 503:
                        error_raw = response.raw.read(1025)
                        try:
                            error_body = json.loads(error_raw) if len(error_raw)<=1024 else {}
                        except (ValueError,UnicodeError):
                            error_body = {}
                        if isinstance(error_body,dict) and error_body.get('detail',error_body.get('error'))=='asr_not_configured':
                            raise ServiceError('asr_not_configured',503,retryable=True)
                    raise ServiceError('upstream_' + str(status), status,
                                       retryable=status in (408, 425, 429) or status >= 500)
                raw = bytearray()
                for chunk in response.iter_content(64*1024):
                    raw.extend(chunk)
                    if len(raw) > self.max_response_bytes:
                        raise ServiceError('upstream_response_limit')
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except ServiceError:
            raise
        except (requests.RequestException, OSError):
            raise ServiceError('upstream_transport', retryable=True) from None
        except (ValueError, UnicodeError):
            raise ServiceError('upstream_invalid_response') from None


class CoreClient(JsonHTTP):
    def binding(self, binding_id):
        return self.request('GET', '/v1/bindings/' + quote(binding_id, safe=''))

    def receipt(self, binding_id, body):
        return self.request('POST', '/v1/bindings/' + quote(binding_id, safe='') + '/receipts', body=body)


class TranscriptionClient(JsonHTTP):
    def normalize(self, raw, format='vtt'):
        return self.request('POST', '/v1/normalize?format=' + format, data=raw,
                            headers={'Content-Type': 'application/octet-stream'})

    def submit(self, path, key):
        suffix = Path(path).suffix.lower()
        with open(path, 'rb') as raw:
            return self.request('POST', '/v1/jobs', data=raw, headers={
                'Content-Type': 'application/octet-stream', 'Idempotency-Key': key,
                'X-Filename': 'recording' + suffix})

    def poll(self, job_id):
        return self.request('GET', '/v1/jobs/' + quote(job_id, safe=''))

    def result(self, job_id):
        return self.request('GET', '/v1/jobs/' + quote(job_id, safe='') + '/result')
