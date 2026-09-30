from sqlalchemy.orm import Session

from app.core.context import get_correlation_id
from app.core.security import Actor
from app.db.models import AuditLog, DeploymentEvent


def audit(session: Session, actor: Actor, action: str, entity_type: str, entity_id: str, **detail) -> None:
    session.add(
        AuditLog(actor=actor.user, role=actor.role.value, action=action, entity_type=entity_type,
                 entity_id=entity_id, detail=detail, correlation_id=get_correlation_id())
    )


def deployment_event(session: Session, dep, event: str, **detail) -> None:
    session.add(
        DeploymentEvent(deployment_id=dep.id, model_id=dep.model_id, version=dep.version,
                        environment=dep.environment, status=dep.status, event=event, detail=detail,
                        correlation_id=get_correlation_id())
    )


def record_denied(session: Session, actor: Actor, action: str, entity_type: str, entity_id: str, exc) -> None:
    """Blocked attempts are security-relevant: persist them (own transaction) before the error is returned."""
    session.rollback()
    audit(session, actor, f"{action}.denied", entity_type, entity_id, code=getattr(exc, "code", "ERROR"),
          reason=str(exc)[:300])
    session.commit()
