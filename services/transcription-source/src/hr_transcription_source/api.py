import re
import secrets
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .repository import Conflict
from .settings import Settings
from .worker import Runtime, Rejected, Retryable


class Recording(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    owner_kind: Literal['interview','meeting']
    owner_id: Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')]
    binding_id: Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')]
    generation: Annotated[int,Field(gt=0)]
    asset_id: Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')]


class BodyLimit:
    def __init__(self,app):
        self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method']!='POST':
            return await self.app(scope,receive,send)
        body=bytearray()
        while True:
            message=await receive()
            if message['type']=='http.disconnect':
                return
            body.extend(message.get('body',b''))
            if len(body)>4096:
                return await JSONResponse({'detail':'payload_too_large'},status_code=413)(scope,receive,send)
            if not message.get('more_body'):
                break
        delivered=False
        async def bounded():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered=True
            return {'type':'http.request','body':bytes(body),'more_body':False}
        await self.app(scope,bounded,send)


def create_app(settings=None,runtime=None):
    settings=settings or Settings.from_env()
    runtime=runtime or Runtime(settings)
    app=FastAPI(title='Transcription source coordinator',docs_url=None,redoc_url=None)
    app.add_middleware(BodyLimit)
    app.state.runtime=runtime
    bearer=HTTPBearer(auto_error=False)
    def caller(auth=Depends(bearer)):
        if auth and auth.scheme.lower()=='bearer':
            for name,c in settings.callers.items():
                if secrets.compare_digest(auth.credentials.encode(),c.token.encode()):
                    return name
        raise HTTPException(401,'unauthorized')
    @app.exception_handler(RequestValidationError)
    async def invalid(request,exc):
        return JSONResponse({'detail':'invalid_request'},status_code=422)
    @app.exception_handler(Rejected)
    async def rejected(request,exc):
        return JSONResponse({'detail':exc.code},status_code=exc.status)
    @app.exception_handler(Retryable)
    async def unavailable(request,exc):
        return JSONResponse({'detail':'dependency_unavailable'},status_code=503)
    @app.get('/health/live')
    def live():
        return {'status':'ok'}
    @app.get('/health/ready')
    def ready(_caller=Depends(caller)):
        seen=runtime.repository.worker_seen()
        return {'api':True,'configuration':bool(settings.source_tokens and settings.download_hosts),
                'worker':seen is not None and runtime.repository.clock()-seen<120,'worker_seen':seen,
                'delivery':'broker' if settings.broker_url else 'manual_worker'}
    @app.post('/v1/recordings',status_code=202)
    def recording(body:Recording,request:Request,name=Depends(caller)):
        key=request.headers.get('idempotency-key','')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',key):
            raise HTTPException(422,'idempotency_key_required')
        try:
            job,created=runtime.submit(name,key,body.model_dump())
        except Conflict:
            raise HTTPException(409,'idempotency_conflict') from None
        return {**runtime.public(job),'created':created}
    @app.get('/v1/jobs/{identity}')
    def status(identity:str,name=Depends(caller)):
        job=runtime.repository.get(identity,name)
        if job is None:
            raise HTTPException(404,'job_not_found')
        # A revoked portal credential cannot expose historical transcript IDs.
        runtime.authorize(name,job['event'])
        return runtime.public(job)
    return app
