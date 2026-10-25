"""Configured HTTP ASR transport; no provider selection or billable retries."""
import asyncio
import json
import math
from pathlib import Path
from typing import Protocol
import aiohttp
from .contracts import validate_normalized
from .normalization import normalize_transcript, MAX_TIME_MS, MAX_SEGMENTS

class BackendError(Exception):
    def __init__(self, code: str, retryable: bool = False, uncertain: bool = False):
        self.code, self.retryable, self.uncertain = code, retryable, uncertain
        super().__init__(code)

class AsrBackend(Protocol):
    def transcribe(self, path: Path, filename: str, language: str | None, request_id: str) -> dict: ...

def _provider_result(value):
    if not isinstance(value, dict) or not isinstance(value.get('text'), str):
        raise BackendError('asr_invalid_response')
    text = value['text'].strip()
    if not text:
        raise BackendError('asr_no_speech')
    segments = value.get('segments')
    if 'segments' not in value:
        canonical = [{'text': text, 'start_ms': None, 'end_ms': None, 'speaker': None}]
    else:
        if not isinstance(segments, list) or not 0 < len(segments) <= MAX_SEGMENTS:
            raise BackendError('asr_invalid_response')
        canonical = []
        for item in segments:
            if not isinstance(item, dict) or not isinstance(item.get('text'), str) or not item['text'].strip():
                raise BackendError('asr_invalid_response')
            times = [item.get('start'), item.get('end')]
            if times == [None, None]:
                converted = times
            elif any(type(t) not in (int, float) or not math.isfinite(t) or not 0 <= t <= MAX_TIME_MS / 1000 for t in times) or times[0] > times[1]:
                raise BackendError('asr_invalid_response')
            else:
                converted = [round(t * 1000) for t in times]
            canonical.append(dict(text=item['text'].strip(), start_ms=converted[0], end_ms=converted[1], speaker=item.get('speaker')))
        # Provider punctuation/whitespace may differ, but speech cannot disappear.
        if ''.join(text.split()) != ''.join(''.join(s['text'] for s in canonical).split()):
            raise BackendError('asr_invalid_response')
    try:
        result = normalize_transcript(json.dumps({'schema_version': 1, 'segments': canonical}, ensure_ascii=False).encode(), 'json')
        if 'segments' not in value:
            result['warnings'] = sorted(set(result['warnings'] + ['asr_text_only']))
        return validate_normalized(result)
    except (ValueError, TypeError):
        raise BackendError('asr_invalid_response') from None

class HttpAsrBackend:
    def __init__(self, settings):
        self.settings = settings
    def __repr__(self):
        return 'HttpAsrBackend(configured)'
    def transcribe(self, path: Path, filename: str, language: str | None, request_id: str) -> dict:
        return asyncio.run(self._transcribe(path, filename, language, request_id))

    async def _transcribe(self, path: Path, filename: str, language: str | None, request_id: str) -> dict:
        cfg = self.settings
        if not cfg.asr_url or not cfg.asr_model or cfg.asr_response_format not in ('json', 'verbose_json'):
            raise BackendError('asr_not_configured')
        headers = {'Idempotency-Key': request_id}
        if cfg.asr_api_key:
            token = cfg.asr_api_key.get_secret_value() if hasattr(cfg.asr_api_key, 'get_secret_value') else cfg.asr_api_key
            headers['Authorization'] = 'Bearer ' + token
        timeout = aiohttp.ClientTimeout(total=cfg.asr_timeout_seconds)
        try:
            async with aiohttp.ClientSession(trust_env=False, timeout=timeout) as session:
                with path.open('rb') as stream:
                    form = aiohttp.FormData()
                    form.add_field('model', cfg.asr_model)
                    form.add_field('response_format', cfg.asr_response_format)
                    if language:
                        form.add_field('language', language)
                    form.add_field('file', stream, filename=filename, content_type='application/octet-stream')
                    async with session.post(cfg.asr_url, data=form, headers=headers, allow_redirects=False) as response:
                        if response.status == 429:
                            raise BackendError('asr_rate_limited', retryable=True)
                        if response.status >= 500:
                            raise BackendError('asr_upstream_failure', uncertain=True)
                        if response.status != 200:
                            raise BackendError('asr_request_rejected')
                        chunks, size = [], 0
                        async for block in response.content.iter_chunked(65536):
                            size += len(block)
                            if size > cfg.max_result_bytes:
                                raise BackendError('asr_response_too_large')
                            chunks.append(block)
                        try:
                            value = json.loads(b''.join(chunks))
                        except (ValueError, RecursionError):
                            raise BackendError('asr_invalid_response') from None
                        return _provider_result(value)
        except (asyncio.TimeoutError, aiohttp.ClientConnectionError):
            raise BackendError('asr_transport_uncertain', uncertain=True) from None
        except aiohttp.ClientError:
            raise BackendError('asr_transport_failure', uncertain=True) from None
