from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api import schemas as s
from app.api.deps import get_actor, get_session, get_settings_dep
from app.core import metrics
from app.core.auth import actor_from_bearer
from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.security import Actor
from app.db.models import AuditLog, Deployment, Model
from app.domain.enums import DeploymentStatus
from app.services import deployments as dsvc
from app.services import monitoring, registry

router = APIRouter()
OPEN_PATHS = {"/health", "/ready"}


def require_access(request: Request, authorization: str | None = Header(None)) -> None:
    """Applied to every route: authenticate (jwt mode) and protect /metrics with an optional scrape token."""
    settings: Settings = request.app.state.settings
    path = request.url.path
    if path in OPEN_PATHS:
        return
    if path == "/metrics" and settings.metrics_token:
        if authorization != f"Bearer {settings.metrics_token}":
            raise UnauthorizedError("Metrics scrape token required")
        return
    if settings.auth_mode == "jwt":
        actor_from_bearer(settings, authorization)  # 401 on anything but a valid token, including reads

ERR = {400: {"model": s.ErrorResponse}, 404: {"model": s.ErrorResponse}, 409: {"model": s.ErrorResponse},
       422: {"model": s.ErrorResponse}, 403: {"model": s.ErrorResponse}}


def model_out(m: Model, detail: bool = False) -> dict:
    versions = list(m.versions)
    out = s.ModelOut.model_validate(m).model_dump()
    out["version_count"] = len(versions)
    out["latest_version"] = versions[-1].version if versions else None
    out["production_version"] = next((v.version for v in versions if v.stage == "PRODUCTION"), None)
    if detail:
        out["versions"] = [s.VersionOut.model_validate(v) for v in versions]
    return out


def dep_out(d: Deployment, settings: Settings, session: Session | None = None) -> s.DeploymentOut:
    out = s.DeploymentOut.model_validate(d)
    out.retryable = (d.status == DeploymentStatus.FAILED.value
                     and d.failure_class in ("TRANSIENT", "RECONCILIATION")
                     and d.attempt < settings.max_deployment_attempts)
    if out.retryable and session is not None and dsvc.has_newer(session, d):
        out.retryable = False  # superseded: a retry would be refused (STALE_RETRY)
    out.rollbackable = (d.status == DeploymentStatus.SUCCEEDED.value and bool(d.previous_version)
                        and not d.rollback_of)
    return out


# ------------------------------------------------------------------ operations
@router.get("/health", tags=["ops"], summary="Liveness")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", tags=["ops"], summary="Readiness (database + worker)")
def ready(request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    try:
        session.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        db = "down"
    worker = request.app.state.worker
    worker_ok = (not request.app.state.settings.worker_enabled) or bool(worker and worker.is_alive())
    ok = db == "ok" and worker_ok
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "not_ready", "database": db, "worker": "ok" if worker_ok else "down"}


@router.get("/metrics", tags=["ops"], response_class=PlainTextResponse, summary="Prometheus metrics")
def prometheus() -> str:
    return metrics.render()


# ------------------------------------------------------------------ models
@router.post("/models", response_model=s.ModelOut, status_code=201, tags=["registry"], responses=ERR)
def create_model(body: s.ModelCreate, session: Session = Depends(get_session), actor: Actor = Depends(get_actor)):
    m = registry.create_model(session, actor, name=body.name, owner=body.owner, framework=body.framework,
                              description=body.description, tags=body.tags, model_id=body.model_id)
    return model_out(m)


@router.get("/models", response_model=s.Page[s.ModelOut], tags=["registry"])
def list_models(q: str | None = None, framework: str | None = None, stage: str | None = None,
                owner: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                session: Session = Depends(get_session)):
    items, total = registry.list_models(session, q=q, framework=framework, stage=stage, owner=owner,
                                        limit=limit, offset=offset)
    return {"items": [model_out(m) for m in items], "total": total, "limit": limit, "offset": offset}


@router.get("/models/{model_id}", response_model=s.ModelDetailOut, tags=["registry"], responses=ERR)
def get_model(model_id: str, session: Session = Depends(get_session)):
    return model_out(registry.get_model(session, model_id), detail=True)


@router.post("/models/{model_id}/versions", response_model=s.VersionOut, status_code=201, tags=["registry"],
             responses=ERR)
def create_version(model_id: str, body: s.VersionCreate, session: Session = Depends(get_session),
                   actor: Actor = Depends(get_actor), settings: Settings = Depends(get_settings_dep)):
    return registry.create_version(session, actor, settings, model_id, **body.model_dump())


@router.get("/models/{model_id}/versions", response_model=list[s.VersionOut], tags=["registry"], responses=ERR)
def list_versions(model_id: str, session: Session = Depends(get_session)):
    return registry.get_model(session, model_id).versions


@router.get("/models/{model_id}/versions/{version}", response_model=s.VersionOut, tags=["registry"], responses=ERR)
def get_version(model_id: str, version: str, session: Session = Depends(get_session)):
    return registry.get_version(session, model_id, version)


@router.post("/models/{model_id}/versions/{version}/transitions", response_model=s.VersionOut, tags=["registry"],
              responses=ERR, summary="Lifecycle action: validate | approve | reject | archive | withdraw")
def transition(model_id: str, version: str, body: s.TransitionIn, session: Session = Depends(get_session),
               actor: Actor = Depends(get_actor), settings: Settings = Depends(get_settings_dep)):
    return registry.transition_version(session, actor, settings, model_id, version, body.action, body.comment)


@router.get("/models/{model_id}/metrics", response_model=s.MetricsOut, tags=["monitoring"], responses=ERR)
def model_metrics(model_id: str, version: str | None = None, environment: str | None = None,
                  since: datetime | None = None, limit: int = Query(500, ge=1, le=5000),
                  session: Session = Depends(get_session), settings: Settings = Depends(get_settings_dep)):
    registry.get_model(session, model_id)
    return monitoring.model_metrics(session, model_id, version=version, environment=environment, since=since,
                                    limit=limit, stale_hours=settings.stale_metrics_hours)


# ------------------------------------------------------------------ deployments
@router.post("/deployments", response_model=s.DeploymentOut, status_code=202, tags=["deployments"], responses=ERR,
             summary="Request a deployment (asynchronous; 202). Send Idempotency-Key to make it replay-safe.")
def create_deployment(body: s.DeploymentCreate, response: Response, session: Session = Depends(get_session),
                      actor: Actor = Depends(get_actor), settings: Settings = Depends(get_settings_dep),
                      idempotency_key: str | None = Header(None, max_length=100)):
    dep, created = dsvc.request_deployment(session, settings, actor, model_id=body.model_id,
                                           version=body.version, environment=body.environment,
                                           simulate_failure=body.simulate_failure.value,
                                           idempotency_key=idempotency_key)
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return dep_out(dep, settings)


@router.get("/deployments", response_model=s.Page[s.DeploymentOut], tags=["deployments"])
def list_deployments(model_id: str | None = None, environment: str | None = None, status: str | None = None,
                     q: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                     session: Session = Depends(get_session), settings: Settings = Depends(get_settings_dep)):
    items, total = dsvc.list_deployments(session, model_id=model_id, environment=environment, status=status,
                                         q=q, limit=limit, offset=offset)
    return {"items": [dep_out(d, settings, session) for d in items], "total": total, "limit": limit, "offset": offset}


@router.get("/deployments/{deployment_id}", response_model=s.DeploymentOut, tags=["deployments"], responses=ERR)
def get_deployment(deployment_id: str, session: Session = Depends(get_session),
                   settings: Settings = Depends(get_settings_dep)):
    return dep_out(dsvc.get_deployment(session, deployment_id), settings, session)


@router.post("/deployments/{deployment_id}/retry", response_model=s.DeploymentOut, status_code=202,
             tags=["deployments"], responses=ERR)
def retry(deployment_id: str, session: Session = Depends(get_session), actor: Actor = Depends(get_actor),
          settings: Settings = Depends(get_settings_dep)):
    return dep_out(dsvc.retry_deployment(session, settings, actor, deployment_id), settings, session)


@router.post("/deployments/{deployment_id}/rollback", response_model=s.DeploymentOut, status_code=202,
             tags=["deployments"], responses=ERR,
             summary="Roll back to the previous version (creates a rollback deployment; asynchronous)")
def rollback(deployment_id: str, response: Response, session: Session = Depends(get_session),
             actor: Actor = Depends(get_actor), settings: Settings = Depends(get_settings_dep)):
    rb, created = dsvc.rollback_deployment(session, actor, deployment_id)
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return dep_out(rb, settings)


@router.get("/deployments/{deployment_id}/events", response_model=list[s.EventOut], tags=["deployments"],
            responses=ERR)
def deployment_events(deployment_id: str, session: Session = Depends(get_session)):
    dsvc.get_deployment(session, deployment_id)
    return dsvc.list_events(session, model_id=None, deployment_id=deployment_id, limit=200, offset=0)[0][::-1]


@router.get("/events", response_model=s.Page[s.EventOut], tags=["deployments"], summary="Event timeline")
def events(model_id: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
           session: Session = Depends(get_session)):
    items, total = dsvc.list_events(session, model_id=model_id, deployment_id=None, limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/audit", response_model=list[s.AuditOut], tags=["ops"], summary="Audit trail (approver+)")
def audit_log(entity_id: str | None = None, limit: int = Query(100, ge=1, le=500),
              session: Session = Depends(get_session), actor: Actor = Depends(get_actor)):
    from app.domain.enums import Role
    actor.require(Role.APPROVER, "read the audit log")
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return session.scalars(stmt).all()
