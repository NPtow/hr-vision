"""Transitional provider-collector boundary to the standalone transcription service.

Workers preserve raw bytes in owned storage before this HTTP call. Catalogue and
read paths consume persisted results; there is deliberately no local parser fallback.
"""
import json
import os
from urllib.parse import urlsplit

import requests

MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_SEGMENTS = 100000
MAX_TIME_MS = 7 * 24 * 60 * 60 * 1000


class TranscriptError(ValueError):
    """The service rejected the supplied transcript content."""


class TranscriptionUnavailable(OSError):
    """Retryable configuration, transport, authentication or protocol failure."""


def _validate(value):
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('schema')
    if value.get('format') not in ('vtt', 'srt', 'json', 'canonical_json'):
        raise ValueError('format')
    if any(not isinstance(value.get(k), str) for k in ('full_text', 'text', 'vtt')):
        raise ValueError('text')
    if value['text'] != value['full_text']:
        raise ValueError('text projection')
    if not isinstance(value.get('warnings'), list) or any(not isinstance(w, str) for w in value['warnings']):
        raise ValueError('warnings')
    segments = value.get('segments')
    if not isinstance(segments, list) or not 0 < len(segments) <= MAX_SEGMENTS:
        raise ValueError('segments')
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get('text'), str) or not segment['text'].strip():
            raise ValueError('segment')
        if not all(k in segment for k in ('start_ms', 'end_ms', 'speaker')):
            raise ValueError('segment fields')
        start, end = segment['start_ms'], segment['end_ms']
        if not (start is None and end is None) and not (type(start) is int and type(end) is int and 0 <= start <= end <= MAX_TIME_MS):
            raise ValueError('timestamps')
        if segment['speaker'] is not None and (not isinstance(segment['speaker'], str) or not segment['speaker'].strip()):
            raise ValueError('speaker')
    if value['full_text'] != '\n'.join(s['text'] for s in segments):
        raise ValueError('full text projection')
    return value


def normalize_transcript(raw, format):
    """Normalize through authenticated HTTP, retaining the collector's old shape."""
    base = os.environ.get('HR_TRANSCRIPTION_URL', '').strip().rstrip('/')
    token = os.environ.get('HR_TRANSCRIPTION_TOKEN', '').strip()
    try:
        parsed = urlsplit(base)
    except ValueError:
        raise TranscriptionUnavailable('Transcription service is not configured') from None
    if not token or parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TranscriptionUnavailable('Transcription service is not configured')
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.post(base + '/v1/normalize', params={'format': str(format)},
                              data=raw, headers={'Authorization': 'Bearer ' + token,
                                                 'Content-Type': 'application/octet-stream'},
                              timeout=(5, 30), allow_redirects=False, stream=True) as response:
                if response.status_code in (400, 413, 415, 422):
                    raise TranscriptError('Transcription service rejected transcript content')
                if response.status_code != 200:
                    raise TranscriptionUnavailable('Transcription service unavailable')
                body = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise TranscriptionUnavailable('Transcription service response exceeds limit')
                return _validate(json.loads(body))
    except (TranscriptError, TranscriptionUnavailable):
        raise
    except (requests.RequestException, ValueError, TypeError, RecursionError):
        # Never expose response bodies, URLs, tokens or transport exception strings.
        raise TranscriptionUnavailable('Transcription service request or response failed') from None
