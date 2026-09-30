"""Idempotent seed loader for the assignment's sample data (registry, events, metrics)."""
import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Deployment,
    DeploymentEvent,
    EnvironmentState,
    MetricPoint,
    Model,
    ModelVersion,
)

log = logging.getLogger(__name__)


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def _ensure(session: Session, model_id: str, version: str | None = None, **model_kw) -> None:
    """Sample files reference models/versions missing from the registry file: register them as inferred."""
    if not session.get(Model, model_id):
        session.add(Model(id=model_id, name=model_kw.get("name") or model_id.replace("-", " ").title(),
                          owner=model_kw.get("owner", "Unassigned"), framework=model_kw.get("framework", "unknown"),
                          tags=["seed-inferred"] if "name" not in model_kw else []))
        session.flush()
    if version and not session.scalar(select(ModelVersion).where(ModelVersion.model_id == model_id,
                                                                 ModelVersion.version == version)):
        session.add(ModelVersion(model_id=model_id, version=version,
                                 artifact_uri=f"s3://models/{model_id}/{version}", stage="VALIDATED",
                                 approval_status="PENDING", tags=["seed-inferred"],
                                 extra={"note": "inferred from sample metrics/events; not in registry file"}))
        session.flush()


def seed(session: Session, data_dir: str) -> bool:
    d = Path(data_dir)
    if not d.exists() or session.scalar(select(func.count()).select_from(Model)):
        return False
    registry = json.loads((d / "sample_model_registry.json").read_text())
    for m in registry:
        _ensure(session, m["model_id"], name=m["name"], owner=m["owner"], framework=m["framework"])
        for v in m["versions"]:
            session.add(ModelVersion(
                model_id=m["model_id"], version=v["version"], framework=m["framework"],
                artifact_uri=v["artifact_uri"], stage=v["stage"],
                approval_status="APPROVED" if v["approved"] else "PENDING",
                approved_by="seed" if v["approved"] else None, tags=["seed"]))
    session.flush()

    for e in json.loads((d / "sample_deployment_events.json").read_text()):
        _ensure(session, e["model_id"], e["version"])
        reason = e["event"]
        fail_cls = {"approval_validation_failed": "PERMANENT", "runtime_timeout": "TRANSIENT"}.get(reason)
        ts = _ts(e["timestamp"])
        dep = Deployment(id=e["deployment_id"], model_id=e["model_id"], version=e["version"],
                         environment=e["environment"], status=e["status"], requested_by="seed", attempt=1,
                         failure_reason=None if e["status"] == "SUCCEEDED" else reason,
                         failure_class=fail_cls if e["status"] == "FAILED" else None,
                         created_at=ts, updated_at=ts)
        session.add(dep)
        session.add(DeploymentEvent(deployment_id=dep.id, model_id=dep.model_id, version=dep.version,
                                    environment=dep.environment, status=dep.status, event=reason,
                                    detail={"seed": True}, timestamp=ts))
    session.flush()

    # Whatever the registry says is in PRODUCTION is live there; link it to its deployment when one exists.
    for mv in session.scalars(select(ModelVersion).where(ModelVersion.stage == "PRODUCTION")).all():
        dep_id = session.scalar(select(Deployment.id).where(
            Deployment.model_id == mv.model_id, Deployment.version == mv.version,
            Deployment.environment == "production", Deployment.status == "SUCCEEDED"))
        session.add(EnvironmentState(model_id=mv.model_id, environment="production", version=mv.version,
                                     live_deployment_id=dep_id))
    session.flush()

    seen = set()
    for row in csv.DictReader((d / "sample_model_metrics.csv").open()):
        _ensure(session, row["model_id"], row["version"])
        key = (row["model_id"], row["version"], row["environment"], row["timestamp"])
        if key in seen:
            continue
        seen.add(key)
        session.add(MetricPoint(
            model_id=row["model_id"], version=row["version"], environment=row["environment"],
            timestamp=_ts(row["timestamp"]), latency_ms=float(row["latency_ms"]),
            throughput_rpm=float(row["throughput_rpm"]), error_rate=float(row["error_rate"]),
            quality_score=float(row["quality_score"]), drift_score=float(row["drift_score"]),
            availability=float(row["availability"])))
    session.commit()
    log.info("seed data loaded", extra={"metric_points": len(seen)})
    return True
