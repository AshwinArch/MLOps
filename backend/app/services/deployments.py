"""Deployment orchestration (request side). The worker (app/workers) performs the long-running part."""
import hashlib
import json
import logging
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core import metrics
from app.core.config import Settings
from app.core.context import get_correlation_id
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailed
from app.core.security import Actor
from app.db.models import Deployment, DeploymentEvent, EnvironmentState
from app.domain import deployment_fsm as fsm
from app.domain import lifecycle
from app.domain.enums import ApprovalStatus, DeploymentStatus, Environment, Role, Stage
from app.services import registry
from app.services.audit import audit, deployment_event, record_denied

log = logging.getLogger(__name__)
ACTIVE = [s.value for s in fsm.ACTIVE]


def _hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def get_deployment(session: Session, deployment_id: str) -> Deployment:
    dep = session.get(Deployment, deployment_id)
    if not dep:
        raise NotFoundError(f"Deployment '{deployment_id}' not found", code="DEPLOYMENT_NOT_FOUND")
    return dep


def live_state(session: Session, model_id: str, env: str) -> EnvironmentState | None:
    """What is live in an environment, from the dedicated state table (not inferred from timestamps)."""
    return session.get(EnvironmentState, (model_id, env))


def newer_deployment_id(session: Session, dep: Deployment) -> str | None:
    return session.scalar(select(Deployment.id).where(
        Deployment.model_id == dep.model_id, Deployment.environment == dep.environment,
        Deployment.id != dep.id, Deployment.created_at > dep.created_at).limit(1))


def has_newer(session: Session, dep: Deployment) -> bool:
    return newer_deployment_id(session, dep) is not None


def _require_env_role(actor: Actor, env: Environment, what: str) -> None:
    if env == Environment.PRODUCTION:
        actor.require(Role.APPROVER, f"{what} in production")
    else:
        actor.require(Role.ENGINEER, what)


def request_deployment(session: Session, settings: Settings, actor: Actor, *, model_id: str, version: str,
                       environment: Environment, simulate_failure: str,
                       idempotency_key: str | None) -> tuple[Deployment, bool]:
    """Returns (deployment, created). created=False means an idempotent replay of an earlier request.
    Blocked attempts (403/409/422 gates) are persisted as `deployment.denied` audit rows."""
    try:
        return _request_deployment(session, settings, actor, model_id=model_id, version=version,
                                   environment=environment, simulate_failure=simulate_failure,
                                   idempotency_key=idempotency_key)
    except (ConflictError, ForbiddenError, ValidationFailed) as exc:
        record_denied(session, actor, "deployment", "deployment", f"{model_id}:{version}@{environment.value}", exc)
        raise


def _request_deployment(session: Session, settings: Settings, actor: Actor, *, model_id: str, version: str,
                        environment: Environment, simulate_failure: str,
                        idempotency_key: str | None) -> tuple[Deployment, bool]:
    _require_env_role(actor, environment, "deploy")
    if simulate_failure != "none" and not settings.enable_failure_simulation:
        raise ValidationFailed("Failure simulation is disabled (set ENABLE_FAILURE_SIMULATION=true for demos)",
                               code="SIMULATION_DISABLED")
    body = {"model_id": model_id, "version": version, "environment": environment.value,
            "simulate_failure": simulate_failure}
    req_hash = _hash(body)

    if idempotency_key:
        existing = session.scalar(select(Deployment).where(Deployment.idempotency_key == idempotency_key,
                                                            Deployment.requested_by == actor.user))
        if existing:
            if existing.request_hash != req_hash:
                raise ConflictError("Idempotency-Key was already used with a different request body",
                                    code="IDEMPOTENCY_KEY_REUSED")
            metrics.inc("deployments_idempotent_replays_total")
            return existing, False

    registry.get_model(session, model_id)
    mv = registry.get_version(session, model_id, version)
    lifecycle.assert_deployable(environment, Stage(mv.stage), ApprovalStatus(mv.approval_status),
                                model_id=model_id, version=version)

    if environment == Environment.PRODUCTION and settings.require_staging_before_production:
        staged = session.scalar(select(func.count()).select_from(Deployment).where(
            Deployment.model_id == model_id, Deployment.version == version,
            Deployment.environment == Environment.STAGING.value,
            Deployment.status.in_([DeploymentStatus.SUCCEEDED.value, DeploymentStatus.ROLLED_BACK.value])))
        if not staged and mv.stage != Stage.PRODUCTION.value:
            raise ConflictError(f"Version {version} must be successfully deployed to staging before production",
                                code="PROMOTION_REQUIRES_STAGING")

    active = session.scalar(select(Deployment).where(
        Deployment.model_id == model_id, Deployment.environment == environment.value,
        Deployment.status.in_(ACTIVE)))
    if active:
        if active.version == version and active.request_hash == req_hash:
            metrics.inc("deployments_idempotent_replays_total")
            return active, False  # natural-key dedupe (duplicate click / retry storm)
        raise ConflictError(
            f"Deployment {active.id} ({active.version}) is already in progress for {model_id} in {environment.value}",
            code="DEPLOYMENT_IN_PROGRESS", details={"active_deployment_id": active.id})

    live = live_state(session, model_id, environment.value)
    if live and live.version == version:
        raise ConflictError(f"Version {version} is already live in {environment.value}", code="ALREADY_DEPLOYED",
                            details={"deployment_id": live.live_deployment_id})

    dep = Deployment(id=f"dep-{uuid.uuid4().hex[:12]}", model_id=model_id, version=version,
                     environment=environment.value, status=DeploymentStatus.REQUESTED.value,
                     idempotency_key=idempotency_key, request_hash=req_hash, requested_by=actor.user,
                     simulate_failure=simulate_failure, request_correlation_id=get_correlation_id())
    session.add(dep)
    deployment_event(session, dep, "deployment_requested", requested_by=actor.user)
    audit(session, actor, "deployment.requested", "deployment", dep.id, model_id=model_id, version=version,
          environment=environment.value)
    try:
        session.commit()
    except IntegrityError:
        # Lost a race with a concurrent identical/conflicting request: the DB constraints decide.
        session.rollback()
        winner = None
        if idempotency_key:
            winner = session.scalar(select(Deployment).where(Deployment.idempotency_key == idempotency_key,
                                                            Deployment.requested_by == actor.user))
        if winner and winner.request_hash == req_hash:
            return winner, False
        raise ConflictError("A deployment is already in progress for this model and environment",
                            code="DEPLOYMENT_IN_PROGRESS") from None
    metrics.inc("deployments_requested_total", environment=environment.value)
    log.info("deployment requested", extra={"deployment_id": dep.id, "model_id": model_id, "version": version})
    return dep, True


def retry_deployment(session: Session, settings: Settings, actor: Actor, deployment_id: str) -> Deployment:
    dep = get_deployment(session, deployment_id)
    _require_env_role(actor, Environment(dep.environment), "retry deployments")
    fsm.assert_retryable(DeploymentStatus(dep.status), dep.failure_class, dep.attempt,
                         settings.max_deployment_attempts)
    fsm.assert_transition(DeploymentStatus.FAILED, DeploymentStatus.REQUESTED)
    newer = newer_deployment_id(session, dep)
    if newer:  # ordering-agnostic: retrying would overwrite a newer intent (e.g. downgrade production)
        raise ConflictError(f"Deployment {newer} was created after this one for the same model/environment; "
                            "request a new deployment instead of retrying", code="STALE_RETRY",
                            details={"newer_deployment_id": newer})
    dep.status = DeploymentStatus.REQUESTED.value
    dep.attempt += 1
    dep.failure_class = None
    dep.failure_reason = None
    deployment_event(session, dep, "deployment_retry_requested", attempt=dep.attempt, by=actor.user)
    audit(session, actor, "deployment.retried", "deployment", dep.id, attempt=dep.attempt)
    try:
        session.commit()
    except (IntegrityError, StaleDataError):
        session.rollback()
        raise ConflictError("Another deployment is active or the record changed; reload and retry",
                            code="DEPLOYMENT_IN_PROGRESS") from None
    metrics.inc("deployments_retried_total")
    return dep


def rollback_deployment(session: Session, actor: Actor, deployment_id: str) -> tuple[Deployment, bool]:
    """Creates a rollback deployment back to the previously live version. Returns (rollback, created)."""
    try:
        return _rollback(session, actor, deployment_id)
    except (ConflictError, ForbiddenError) as exc:
        record_denied(session, actor, "deployment.rollback", "deployment", deployment_id, exc)
        raise


def _rollback(session: Session, actor: Actor, deployment_id: str) -> tuple[Deployment, bool]:
    dep = get_deployment(session, deployment_id)
    _require_env_role(actor, Environment(dep.environment), "roll back")

    existing = session.scalar(select(Deployment).where(Deployment.rollback_of == dep.id,
                                                       Deployment.status.in_(ACTIVE)))
    if existing:
        return existing, False  # idempotent
    if dep.rollback_of:
        raise ConflictError("A rollback deployment cannot itself be rolled back; deploy a version explicitly",
                            code="ROLLBACK_OF_ROLLBACK")
    if dep.status != DeploymentStatus.SUCCEEDED.value:
        raise ConflictError(f"Only SUCCEEDED deployments can be rolled back (status: {dep.status})",
                            code="ROLLBACK_NOT_ALLOWED")
    live = live_state(session, dep.model_id, dep.environment)
    if not live or live.live_deployment_id != dep.id:
        raise ConflictError("Deployment is no longer the live one in this environment; rolling it back would "
                            "overwrite a newer release", code="STALE_ROLLBACK",
                            details={"live_deployment_id": live.live_deployment_id if live else None})
    if not dep.previous_version:
        raise ConflictError("No previous version to roll back to (first deployment in this environment)",
                            code="ROLLBACK_TARGET_MISSING")
    target = registry.get_version(session, dep.model_id, dep.previous_version)
    if target.approval_status != ApprovalStatus.APPROVED.value or target.stage == "WITHDRAWN":
        raise ConflictError(f"Rollback target {target.version} is not approved", code="ROLLBACK_TARGET_UNSAFE")

    rb = Deployment(id=f"dep-{uuid.uuid4().hex[:12]}", model_id=dep.model_id, version=target.version,
                    environment=dep.environment, status=DeploymentStatus.REQUESTED.value,
                    requested_by=actor.user, rollback_of=dep.id, request_correlation_id=get_correlation_id(),
                    request_hash=_hash(
                        {"rollback_of": dep.id}))
    session.add(rb)
    deployment_event(session, rb, "rollback_requested", rollback_of=dep.id, target=target.version)
    audit(session, actor, "deployment.rollback_requested", "deployment", dep.id, rollback_id=rb.id,
          target_version=target.version)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("A deployment is already in progress for this model and environment",
                            code="DEPLOYMENT_IN_PROGRESS") from None
    metrics.inc("rollbacks_requested_total", environment=dep.environment)
    return rb, True


def list_deployments(session: Session, *, model_id: str | None, environment: str | None, status: str | None,
                     q: str | None, limit: int, offset: int) -> tuple[list[Deployment], int]:
    stmt = select(Deployment)
    if model_id:
        stmt = stmt.where(Deployment.model_id == model_id)
    if environment:
        stmt = stmt.where(Deployment.environment == environment)
    if status:
        stmt = stmt.where(Deployment.status == status)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Deployment.id).like(like), func.lower(Deployment.model_id).like(like),
                              Deployment.version.like(like)))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = session.scalars(stmt.order_by(Deployment.created_at.desc(), Deployment.id).limit(limit)
                            .offset(offset)).all()
    return list(items), total


def list_events(session: Session, *, model_id: str | None, deployment_id: str | None, limit: int,
                offset: int) -> tuple[list[DeploymentEvent], int]:
    stmt = select(DeploymentEvent)
    if model_id:
        stmt = stmt.where(DeploymentEvent.model_id == model_id)
    if deployment_id:
        stmt = stmt.where(DeploymentEvent.deployment_id == deployment_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = session.scalars(stmt.order_by(DeploymentEvent.timestamp.desc(), DeploymentEvent.id.desc())
                            .limit(limit).offset(offset)).all()
    return list(items), total


