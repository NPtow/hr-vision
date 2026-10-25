"""FastAPI reads and an optional command to the existing durable collector."""
from contextlib import asynccontextmanager
import sqlite3

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from materials_repository import RepositoryError

from .auth import Tokens
from .repository import CoreRepository
from .schemas import BindingInput, CollectInput, OwnerKind, Receipt
from .service import CoreService


MAX_BODY_BYTES = 24 * 1024 * 1024


class BodyLimit:
    """Bound raw bytes before FastAPI decodes JSON, including chunked bodies."""
    def __init__(self, app, limit=MAX_BODY_BYTES):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > self.limit:
                return await JSONResponse({"detail": "payload_too_large"}, status_code=413)(scope, receive, send)
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        await self.app(scope, bounded_receive, send)


def create_app(settings=None, *, service=None, tokens=None):
    if service is None:
        from .settings import Settings
        settings = settings or Settings.from_env()
        repository = CoreRepository(settings.connect, default_connections=settings.default_connections)
        service = CoreService(repository, settings.storage_registry())
        if settings.collector_enabled:
            import os
            from materials_runtime import build_runtime
            from materials_service import MaterialsService
            runtime = build_runtime(connect=settings.connect,
                environ={**os.environ, "HR_MATERIALS_ENABLED": "1"}, publish=lambda _: None)
            service.collector = MaterialsService(runtime).register_owner
            if not service.storage_registry and runtime.storage.backend == "s3":
                service.storage_registry = {pair: runtime.storage for pair in settings.default_connections.items()}
    tokens = tokens or Tokens(settings.principals)

    @asynccontextmanager
    async def lifespan(app):
        service.repository.init()
        yield

    app = FastAPI(title="HR Materials Core", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(BodyLimit)
    app.state.service = service
    bearer = HTTPBearer(auto_error=False)

    def principal(credentials=Depends(bearer)):
        return tokens.authenticate(credentials)

    @app.exception_handler(RepositoryError)
    async def repository_error(request: Request, exc: RepositoryError):
        return JSONResponse({"detail": exc.code}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Never echo raw input (credentials/URLs/transcript content) in errors.
        return JSONResponse({"detail": [{"loc": list(x["loc"]), "type": x["type"]} for x in exc.errors()]}, status_code=422)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            with service.repository._db() as db:
                db.execute("SELECT 1 FROM core_material_receipts LIMIT 1")
        except sqlite3.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.post("/v1/bindings")
    def binding(body: BindingInput, caller=Depends(principal)):
        return service.repository.create_binding(caller, body)

    @app.get("/v1/bindings/{binding_id}")
    def get_binding(binding_id: str, caller=Depends(principal)):
        return service.repository.read_binding(caller, binding_id)

    @app.post("/v1/bindings/{binding_id}/receipts")
    def receipt(binding_id: str, body: Receipt, caller=Depends(principal)):
        return service.receive(caller, binding_id, body)

    @app.get("/v1/owners/{kind}/{identity}/materials")
    def materials(kind: OwnerKind, identity: str, caller=Depends(principal)):
        return service.repository.read_materials(caller, kind, identity)

    @app.post("/v1/owners/{kind}/{identity}/collect", status_code=202)
    def collect(kind: OwnerKind, identity: str, body: CollectInput | None = None, caller=Depends(principal)):
        return service.collect(caller, kind, identity, body.storage_policy if body else None)

    @app.get("/v1/candidates/{candidate_id}/dossier")
    def dossier(candidate_id: str, caller=Depends(principal)):
        return service.repository.dossier(caller, candidate_id)

    @app.get("/v1/assets/{asset_id}/access")
    def access(asset_id: str, caller=Depends(principal)):
        return service.access(caller, asset_id)

    return app
