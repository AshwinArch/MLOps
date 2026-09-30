import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from app.api.routes import require_access, router
from app.core import metrics
from app.core.config import Settings, get_settings, validate_production
from app.core.context import correlation_id_var
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.db import seed as seeder
from app.db.session import init_db, make_engine, make_session_factory
from app.workers.factory import build_runtime
from app.workers.runtime import ModelRuntime
from app.workers.worker import DeploymentWorker

log = logging.getLogger("app")
_CID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _err(status: int, code: str, message: str, details=None) -> JSONResponse:
    cid = correlation_id_var.get()
    return JSONResponse({"error": {"code": code, "message": message, "details": details,
                                   "correlation_id": cid}}, status_code=status, headers={"X-Correlation-ID": cid})


def create_app(settings: Settings | None = None, runtime: ModelRuntime | None = None) -> FastAPI:
    settings = settings or get_settings()
    validate_production(settings)
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        if settings.auto_create_schema:
            init_db(engine)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        if settings.seed_on_startup:
            with app.state.session_factory() as s:
                seeder.seed(s, settings.seed_dir)
        app.state.runtime = runtime or build_runtime(settings)
        app.state.worker = DeploymentWorker(app.state.session_factory, app.state.runtime, settings)
        if settings.worker_enabled:
            app.state.worker.start()
        yield
        app.state.worker.stop()
        engine.dispose()

    app = FastAPI(title="MLOps Platform API", version="1.0.0", lifespan=lifespan,
                  description="Register, version, approve, deploy, monitor and roll back ML models.")
    app.state.settings = settings
    app.state.worker = None
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["X-Correlation-ID", "Idempotent-Replay"])

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        inbound = request.headers.get("X-Correlation-ID", "")
        cid = inbound if _CID.match(inbound) else uuid.uuid4().hex[:16]  # never trust/store unbounded input
        token = correlation_id_var.set(cid)
        start = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                log.exception("unhandled error", extra={"path": request.url.path})
                metrics.inc("http_requests_total", method=request.method, status="500")
                return _err(500, "INTERNAL_ERROR", "Unexpected server error")  # built BEFORE the id is reset
            metrics.observe("http_request_duration_seconds", time.perf_counter() - start, method=request.method)
            response.headers["X-Correlation-ID"] = cid
            metrics.inc("http_requests_total", method=request.method, status=str(response.status_code))
            log.info("request", extra={"method": request.method, "path": request.url.path,
                                       "status": response.status_code,
                                       "duration_ms": round((time.perf_counter() - start) * 1000, 1)})
            return response
        finally:
            correlation_id_var.reset(token)

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return _err(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return _err(422, "VALIDATION_ERROR", "Request validation failed", details)

    @app.exception_handler(StaleDataError)
    async def stale(_: Request, exc: StaleDataError):
        return _err(409, "CONCURRENT_UPDATE", "Resource was modified concurrently; reload and retry")

    app.include_router(router, dependencies=[Depends(require_access)])
    return app




def get_app() -> FastAPI:
    return create_app()
