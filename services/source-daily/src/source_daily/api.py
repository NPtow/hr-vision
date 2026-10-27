from contextlib import asynccontextmanager
import hmac
import json
from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, field_validator, model_validator

from ._vendor.media_runtime import provider_budget
from ._vendor.support import Problem
from .errors import ServiceError
from .pipeline import safe_id

Identifier = Annotated[str,StringConstraints(min_length=1,max_length=200,pattern=r'^[A-Za-z0-9_.:@-]+$')]
Label = Annotated[str,StringConstraints(min_length=1,max_length=200)]


class DTO(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)


class Room(DTO):
    name: Identifier
    id: Identifier
    url: Annotated[str,StringConstraints(max_length=2048)] | None = None
    recordingInstanceId: Identifier | None = None
    captureInstanceId: Identifier | None = None
    captureNeverStarted: StrictBool | None = None
    meetingSessionId: Identifier | None = None
    session_id: Identifier | None = None
    sessionId: Identifier | None = None
    mtgSessionId: Identifier | None = None

    @field_validator('name')
    @classmethod
    def room_name(cls,value):
        if not value.startswith('hrv-'):
            raise ValueError('Invalid managed room')
        return value

    @field_validator('url')
    @classmethod
    def room_url(cls,value):
        if value is not None:
            parsed = urlsplit(value)
            if parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.daily.co') or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Invalid managed room URL')
        return value


class RoomRequest(DTO):
    meeting_id: Identifier
    recordable: StrictBool = True
    screenshare: StrictBool = False


class Permissions(DTO):
    canAdmin: StrictBool = False


class TokenProperties(DTO):
    room_name: Identifier | None = None
    user_name: Label
    user_id: Identifier
    is_owner: StrictBool = False
    permissions: Permissions | None = None
    enable_recording_ui: StrictBool | None = None
    enable_live_captions_ui: StrictBool | None = None
    enable_prejoin_ui: StrictBool | None = None
    exp: Annotated[StrictInt,Field(ge=1,le=4102444800)]
    eject_at_token_exp: StrictBool = True
    start_cloud_recording: StrictBool = False
    auto_start_transcription: StrictBool = False

    @model_validator(mode='after')
    def no_implicit_capture(self):
        if self.start_cloud_recording or self.auto_start_transcription:
            raise ValueError('Capture must use the explicit start endpoint')
        return self


class TokenRequest(DTO):
    room: Room
    properties: TokenProperties

    @model_validator(mode='after')
    def same_room(self):
        if self.properties.room_name is not None and self.properties.room_name != self.room.name:
            raise ValueError('Token room mismatch')
        return self


class StartRequest(DTO):
    room: Room
    recording_instance_id: Identifier | None = None
    start_recording: StrictBool = False
    start_transcription: StrictBool = False


class FinishRequest(DTO):
    room: Room


class KnownAsset(DTO):
    id: Identifier
    duration: Annotated[float,Field(ge=0,le=7*24*3600)] | None = None
    providerMetadata: dict = Field(default_factory=dict)

    @field_validator('providerMetadata')
    @classmethod
    def trusted_metadata_only(cls,value):
        if set(value)-{'meetingSessionId','startTs','created_at','updated_at'}:
            raise ValueError('Unsupported provider metadata')
        if len(json.dumps(value))>1024:
            raise ValueError('Metadata limit')
        if any(isinstance(v,str) and (len(v)>200 or '://' in v) for v in value.values()):
            raise ValueError('Invalid metadata')
        return value


class Known(DTO):
    recordings: list[KnownAsset] = Field(default_factory=list,max_length=20)
    transcripts: list[KnownAsset] = Field(default_factory=list,max_length=20)


class MaterialsRequest(DTO):
    room: Room
    known: Known | None = None
    include_metadata: StrictBool = False


class JobRequest(DTO):
    binding_id: Identifier
    generation: Annotated[StrictInt,Field(ge=1,le=2147483647)]


class AccessRequest(DTO):
    binding_id: Identifier
    external_id: Identifier


def public_job(job):
    cp = job['checkpoint']
    return {k:job[k] for k in ('id','binding_id','generation','status','attempts','error_code','created_at','updated_at')} | {
        'video_status':cp.get('video_status','processing'),
        'transcript_status':cp.get('transcript_status','processing'),
        'acquisition_complete':cp.get('acquisition_complete',False),
        'transcription_status':cp.get('transcription_status','not_requested')}


def create_app(settings,pipeline,media,*,publish=None):
    app = FastAPI(title='Daily source service',version='1.0',docs_url=None,redoc_url=None)
    app.state.pipeline = pipeline

    @app.middleware('http')
    async def bounded_body(request,call_next):
        length = request.headers.get('content-length')
        if length is not None and (not length.isdigit() or int(length)>settings.max_body_bytes):
            return JSONResponse({'error':'request_body_limit'},status_code=413)
        if request.method in ('POST','PUT','PATCH'):
            # Bound chunked bodies before Pydantic decodes them.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>settings.max_body_bytes:
                    return JSONResponse({'error':'request_body_limit'},status_code=413)
            request._body = bytes(body)
        return await call_next(request)

    def principal(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):
            raise HTTPException(401,detail='unauthorized')
        token = authorization[7:]
        client = next((v for k,v in settings.api_keys.items() if hmac.compare_digest(token,k)),None)
        if client is None:
            raise HTTPException(401,detail='unauthorized')
        return client

    auth = [Depends(principal)]

    @app.exception_handler(RequestValidationError)
    async def invalid(request,error):
        # Pydantic errors include rejected input; never echo credentials/URLs.
        return JSONResponse({'error':'invalid_request'},status_code=422)

    @app.exception_handler(ServiceError)
    async def service_error(request,error):
        return JSONResponse({'error':error.code},status_code=error.status if error.status in (401,403,404,409,413,422,429,503) else 502)

    @app.exception_handler(Problem)
    async def provider_error(request,error):
        return JSONResponse({'error':'daily_operation_failed'},status_code=error.status if error.status in (401,403,404,409,503) else 502)

    @app.get('/health/live')
    def live():
        return {'status':'ok'}

    @app.get('/health/ready',dependencies=auth)
    def ready():
        with pipeline.store.db() as db:
            db.execute('SELECT 1').fetchone()
        configured = media.configured and pipeline.core.configured
        return JSONResponse({'status':'ready' if configured else 'unconfigured',
                'daily_configured':media.configured,'core_configured':pipeline.core.configured,
                's3_configured':pipeline.storage is not None,
                'transcription_configured':pipeline.transcription.configured},status_code=200 if configured else 503)

    @app.get('/v1/capabilities',dependencies=auth)
    def capabilities():
        return {'provider':'daily','connection_id':settings.connection_id,'configured':media.configured,
                'default_storage_policy':settings.default_storage_policy,
                'transcription_configured':bool(settings.daily_transcription_model),
                'asr_configured':settings.enable_asr and pipeline.transcription.configured}

    @app.post('/v1/calls/room',dependencies=auth)
    def room(body:RoomRequest):
        with provider_budget():
            return media.room(body.meeting_id,recordable=body.recordable,screenshare=body.screenshare)

    @app.post('/v1/calls/token',dependencies=auth)
    def token(body:TokenRequest):
        properties = body.properties.model_dump(exclude_none=True)
        properties['room_name'] = body.room.name
        with provider_budget():
            result = media.request('/meeting-tokens',{'properties':properties})
        if not isinstance(result.get('token'),str) or not result['token'] or len(result['token'])>16384:
            raise ServiceError('daily_token_invalid')
        return {'token':result['token']}

    @app.post('/v1/calls/start',dependencies=auth)
    def start(body:StartRequest):
        result = {'recording_requested':False,'transcription_requested':False}
        path = '/rooms/' + quote(body.room.name,safe='')
        with provider_budget():
            if body.start_recording:
                try:
                    options = {'type':'cloud'}
                    if body.recording_instance_id:
                        options['instanceId'] = body.recording_instance_id
                    media.request(path+'/recordings/start',options)
                    result['recording_requested'] = True
                except Problem:
                    result['recording_error'] = 'daily_recording_start_failed'
            if body.start_transcription:
                if not settings.daily_transcription_model:
                    result['transcription_error'] = 'daily_transcription_not_configured'
                else:
                    try:
                        media.request(path+'/transcription/start',{'language':'ru','model':settings.daily_transcription_model})
                        result['transcription_requested'] = True
                    except Problem:
                        result['transcription_error'] = 'daily_transcription_start_failed'
        return result

    @app.post('/v1/calls/finish',dependencies=auth)
    def finish(body:FinishRequest):
        with provider_budget():
            media.finish(body.room.model_dump(exclude_none=True))
        return {'acknowledged':True}

    @app.post('/v1/calls/materials',dependencies=auth)
    def materials(body:MaterialsRequest):
        if not settings.legacy_materials:
            raise ServiceError('legacy_materials_disabled',404)
        # Legacy lab only: production uses exact-session pipeline and access proofs.
        return media.materials(body.room.model_dump(exclude_none=True),
                body.known.model_dump(exclude_none=True) if body.known else None,
                include_metadata=body.include_metadata)

    @app.post('/v1/jobs',dependencies=auth,status_code=202)
    def enqueue(body:JobRequest):
        job = pipeline.enqueue(body.binding_id,body.generation)
        if publish:
            try:
                publish(job['id'])
            except Exception:
                # Durable row precedes publication; standalone scheduler recovers.
                pass
        return public_job(job)

    @app.get('/v1/jobs/{job_id}',dependencies=auth)
    def job(job_id:Identifier):
        row = pipeline.store.get(job_id)
        if not row:
            raise ServiceError('job_not_found',404)
        pipeline.binding(row['binding_id'],row['generation'])
        return public_job(row)

    @app.post('/v1/materials/access',dependencies=auth)
    def access(body:AccessRequest):
        return pipeline.access(body.binding_id,body.external_id)

    @app.post('/v1/jobs/{job_id}/resume',dependencies=auth,status_code=202)
    def resume(job_id:Identifier):
        row = pipeline.resume(job_id)
        if publish:
            try:
                publish(row['id'])
            except Exception:
                pass
        return public_job(row)

    @app.post('/v1/webhooks/daily',dependencies=auth)
    async def webhook(request:Request):
        raw = await request.body()
        hint = pipeline.capture.verify_webhook(raw,dict(request.headers))
        if not hint.get('test'):
            if not safe_id(hint.get('event_id')):
                raise ServiceError('invalid_webhook_identity',422)
            hint['payload'] = {k:v for k,v in hint['payload'].items() if safe_id(v)}
        duplicate,woken = pipeline.store.webhook(hint)
        if publish:
            for job_id in woken:
                try:
                    publish(job_id)
                except Exception:
                    break
        return {'accepted':True,'duplicate':duplicate}

    return app
