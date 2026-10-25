"""Private stateless Zoom API. HR authorization and durable jobs stay in core."""
import hmac
import os
import re
from pathlib import Path
import tempfile
import threading

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from domain import Problem
from media_runtime import provider_budget
from materials_http import download
from materials_capture import HttpSource
from zoom_media import ZoomMedia, ZoomMediaError


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Room(Input):
    room: dict


class Create(Input):
    meeting_id: str = Field(min_length=1, max_length=200)
    host_id: str = Field(min_length=1, max_length=320)
    request_id: str = Field(min_length=1, max_length=200)


class Access(Room):
    user_name: str = Field(min_length=1, max_length=200)
    owner: StrictBool


class Content(Room):
    file_id: str = Field(min_length=1, max_length=200)
    timeout_seconds: int = Field(default=240, ge=1, le=1440)
    max_bytes: int = Field(default=2 * 1024**3, ge=1, le=2 * 1024**3)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > 65536:
                return await JSONResponse({'error': 'request_too_large'}, status_code=413)(scope, receive, send)
            if not message.get('more_body', False):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
        await self.app(scope, bounded_receive, send)


class TemporaryFileResponse(FileResponse):
    """Release the private spool even when the client disconnects mid-response."""
    def __init__(self, *args, cleanup, **kwargs):
        self.cleanup = cleanup
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.cleanup()


def create_app(media=None, *, token=None, downloader=download, temp_root=None):
    media = media or ZoomMedia.from_env()
    token = token or os.environ.get('ZOOM_API_TOKEN', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise ValueError('ZOOM_API_TOKEN must be a 32-256 character URL-safe token')
    temp_root = temp_root or os.environ.get('ZOOM_API_TEMP_ROOT', '/tmp')
    app = FastAPI(title='Zoom API', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)
    download_slot = threading.BoundedSemaphore(1)

    def authorized(authorization: str = Header(default='')):
        if not hmac.compare_digest(authorization.encode(), ('Bearer ' + token).encode()):
            raise HTTPException(401, 'unauthorized')

    @app.middleware('http')
    async def limit_body(request: Request, next_handler):
        # All clients send small JSON; no uploads, chunked bodies or public CORS.
        if request.method == 'POST':
            length = request.headers.get('content-length', '')
            if not length.isdecimal() or int(length) > 65536:
                return JSONResponse({'error': 'request_too_large'}, status_code=413)
        response = await next_handler(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(Problem)
    @app.exception_handler(ZoomMediaError)
    async def provider_error(request, exc):
        return JSONResponse({'error': 'zoom_operation_failed'}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({'error': 'invalid_request'}, status_code=422)

    @app.get('/health/live')
    def live():
        return {'status': 'ok'}

    @app.get('/v1/config', dependencies=[Depends(authorized)])
    def configuration():
        return {'configured': media.configured, 'capture_configured': media.capture_configured,
                'selection_enabled': media.selection_enabled, 'connection_id': media.connection_id,
                'hosts': media._hosts, 'default_host': media._default_host}

    @app.post('/v1/rooms', dependencies=[Depends(authorized)])
    def room(body: Create):
        # Never retry POST. Portal reserved cp_call_provider_choices before this.
        with provider_budget():
            return media.room(body.meeting_id, body.host_id, body.request_id)

    @app.post('/v1/access', dependencies=[Depends(authorized)])
    def access(body: Access):
        with provider_budget():
            return media.credentials(body.room, body.user_name, body.owner)

    @app.post('/v1/state', dependencies=[Depends(authorized)])
    def state(body: Room):
        with provider_budget():
            return media.get_state(body.room)

    @app.post('/v1/finish', dependencies=[Depends(authorized)])
    def finish(body: Room):
        with provider_budget():
            return media.finish(body.room)

    @app.post('/v1/recordings', dependencies=[Depends(authorized)])
    def recordings(body: Room):
        with provider_budget():
            result = media.recordings(body.room)
        fields = {'id', 'meeting_id', 'file_type', 'file_extension', 'recording_type',
                  'status', 'file_size', 'recording_start', 'recording_end'}
        return {**{key: result[key] for key in ('id', 'uuid', 'status', 'recording_count') if key in result},
                'listing_complete': not bool(result.get('next_page_token')),
                'recording_files': [{k: v for k, v in row.items() if k in fields} for row in result['recording_files']]}

    @app.post('/v1/recordings/content', dependencies=[Depends(authorized)])
    def content(body: Content):
        if not download_slot.acquire(blocking=False):
            raise HTTPException(503, 'download_busy')
        directory = None
        def cleanup():
            try:
                if directory:
                    directory.cleanup()
            finally:
                download_slot.release()
        try:
            with provider_budget():
                files = [row for row in media.recordings(body.room)['recording_files'] if row.get('id') == body.file_id]
                if len(files) != 1:
                    raise HTTPException(404, 'recording_not_found')
                transport = media.resolve_download(files[0])
            directory = tempfile.TemporaryDirectory(prefix='zoom-content-', dir=temp_root)
            result = downloader(HttpSource(**transport), Path(directory.name) / 'content',
                max_bytes=body.max_bytes, expected_size=files[0].get('file_size'), timeout_seconds=body.timeout_seconds)
            return TemporaryFileResponse(result.path, media_type=result.content_type, cleanup=cleanup,
                headers={'X-Content-SHA256': result.sha256})
        except (HTTPException, Problem):
            cleanup()
            raise
        except Exception:
            cleanup()
            # No provider URL, token, payload or candidate data in error responses.
            raise HTTPException(502, 'recording_download_failed') from None

    return app
