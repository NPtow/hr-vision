"""Service-to-service API. Input bytes only: no provider SDK, URL fetch or HR DB."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from .contracts import validate_normalized
from .normalization import MAX_BYTES, TranscriptError, normalize_transcript
from .repository import RepositoryConflict
from .settings import Settings
from .worker import Runtime

MEDIA_EXTENSIONS = {'.mp4', '.m4a', '.mp3', '.wav', '.webm', '.ogg', '.flac', '.mpeg', '.mpga'}


def create_app(settings=None, runtime=None):
    settings = settings or Settings.from_env()
    if not settings.api_keys:
        raise ValueError('TRANSCRIPTION_API_KEYS must configure at least one caller')
    runtime = runtime or Runtime(settings)
    app = FastAPI(title='Transcription service', version='1.0', docs_url=None, redoc_url=None)
    app.state.runtime = runtime
    bearer = HTTPBearer(auto_error=False)

    def caller(auth: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if auth and auth.scheme.lower() == 'bearer':
            supplied = auth.credentials.encode('utf-8')
            for client, token in settings.api_keys.items():
                if secrets.compare_digest(supplied, token.encode('utf-8')):
                    return client
        raise HTTPException(401, 'unauthorized', headers={'WWW-Authenticate': 'Bearer'})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        # FastAPI's default error body echoes submitted fields; keep audio/text
        # and service credentials out of diagnostic responses.
        return JSONResponse({'detail': 'invalid_request'}, status_code=422)

    @app.get('/health/live')
    def live():
        return {'status': 'ok'}

    @app.get('/health/ready')
    def ready(_client: str = Depends(caller)):
        # DB readiness is independent of selecting an inference provider.
        runtime.repository.get('health-check')
        return {'status': 'ready', 'normalization': True,
                'transcription': settings.asr_configured,
                'delivery': 'broker' if settings.broker_url else 'manual_worker'}

    async def chunks(request, maximum):
        length = request.headers.get('content-length')
        if length is not None:
            try:
                if not 0 <= int(length) <= maximum:
                    raise HTTPException(413, 'input_too_large')
            except ValueError:
                raise HTTPException(400, 'invalid_content_length') from None
        count = 0
        async for block in request.stream():
            count += len(block)
            if count > maximum:
                raise HTTPException(413, 'input_too_large')
            yield block
        if count == 0:
            raise HTTPException(422, 'empty_input')

    @app.post('/v1/normalize')
    async def normalize(request: Request, format: str, _client: str = Depends(caller)):
        if format not in ('vtt', 'srt', 'json', 'canonical_json'):
            raise HTTPException(422, 'unsupported_format')
        raw = bytearray()
        async for block in chunks(request, MAX_BYTES):
            raw.extend(block)
        try:
            normalized = await run_in_threadpool(lambda: validate_normalized(normalize_transcript(bytes(raw), format)))
        except (TranscriptError, ValueError, TypeError):
            raise HTTPException(422, 'invalid_transcript') from None
        if len(json.dumps(normalized, ensure_ascii=False).encode('utf-8')) > settings.max_result_bytes:
            raise HTTPException(413, 'normalized_result_too_large')
        return normalized

    def public(job):
        return {key: job[key] for key in ('id', 'status', 'attempts', 'created_at', 'updated_at',
                                         'input_sha256', 'input_size', 'language', 'error_code')}

    @app.post('/v1/jobs', status_code=202)
    async def submit(request: Request, language: str | None = None, client: str = Depends(caller)):
        if not settings.asr_configured:
            raise HTTPException(503, 'asr_not_configured')
        key = request.headers.get('idempotency-key', '')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key):
            raise HTTPException(422, 'idempotency_key_required')
        if language is not None and not re.fullmatch(r'[a-z]{2,3}', language):
            raise HTTPException(422, 'invalid_language')
        # Keep user-provided names out of persistence and backend diagnostics.
        extension = Path(request.headers.get('x-filename', '')).suffix.lower()
        if extension not in MEDIA_EXTENSIONS:
            raise HTTPException(422, 'supported_x_filename_required')
        staging = settings.data_dir / 'staging'
        staging.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='upload-', dir=staging)
        size, digest = 0, hashlib.sha256()
        try:
            with os.fdopen(fd, 'wb') as stream:
                async for block in chunks(request, settings.max_input_bytes):
                    size += len(block)
                    digest.update(block)
                    await run_in_threadpool(stream.write, block)
                await run_in_threadpool(stream.flush)
                await run_in_threadpool(os.fsync, stream.fileno())
            sha = digest.hexdigest()

            def persist():
                try:
                    job, created = runtime.repository.submit(client, key, sha, sha, size,
                        'input' + extension, language, settings.backend_identity,
                        initial_status='uploading', backend_model=settings.asr_model)
                except RepositoryConflict:
                    raise HTTPException(409, 'idempotency_conflict') from None
                if job['status'] == 'uploading':
                    try:
                        runtime.storage.put_file(name, sha)
                    except OSError:
                        raise HTTPException(503, 'input_storage_unavailable') from None
                    runtime.repository.activate(job['id'], client)
                    job = runtime.repository.get(job['id'], client)
                if job['status'] == 'queued':
                    runtime.enqueue(job['id'])
                return {**public(job), 'created': created}
            return await run_in_threadpool(persist)
        finally:
            Path(name).unlink(missing_ok=True)

    def owned(job_id, client):
        job = runtime.repository.get(job_id, client)
        if job is None:
            raise HTTPException(404, 'job_not_found')
        return job

    @app.get('/v1/jobs/{job_id}')
    def status(job_id: str, client: str = Depends(caller)):
        return public(owned(job_id, client))

    @app.get('/v1/jobs/{job_id}/result')
    def result(job_id: str, client: str = Depends(caller)):
        job = owned(job_id, client)
        if job['status'] != 'succeeded':
            raise HTTPException(409, 'result_not_ready')
        return {'job_id': job['id'], 'input_sha256': job['input_sha256'],
                'processing_status': 'completed', 'coverage': 'not_independently_verified',
                'language': job['language'], 'model': job['backend_model'],
                'backend_identity': job['backend_identity'],
                'transcript': job['result']}

    @app.post('/v1/jobs/{job_id}/retry', status_code=202)
    def retry(job_id: str, client: str = Depends(caller)):
        job = owned(job_id, client)
        if not settings.asr_configured or job['backend_identity'] != settings.backend_identity:
            raise HTTPException(409, 'backend_configuration_changed')
        if not runtime.repository.retry(job_id, client):
            raise HTTPException(409, 'job_not_retryable')
        runtime.enqueue(job_id)
        return public(owned(job_id, client))

    return app
