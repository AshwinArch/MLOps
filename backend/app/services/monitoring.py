"""Monitoring read-model: metric series, per-version summaries, health classification."""
from datetime import datetime
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import MetricPoint

SEVERITY = {"HEALTHY": 0, "DEGRADED": 1, "CRITICAL": 2}

# (degraded, critical) thresholds. "higher is worse" unless inverted.
THRESHOLDS = {
    "latency_ms": (150.0, 250.0, False),
    "error_rate": (0.02, 0.05, False),
    "drift_score": (0.25, 0.40, False),
    "quality_score": (0.85, 0.75, True),
    "availability": (99.5, 99.0, True),
}


def classify_point(p: MetricPoint) -> str:
    """HEALTHY | DEGRADED | CRITICAL for one observation."""
    worst = "HEALTHY"
    for field, (warn, crit, inverted) in THRESHOLDS.items():
        v = getattr(p, field)
        if (v < crit) if inverted else (v > crit):
            return "CRITICAL"
        if (v < warn) if inverted else (v > warn):
            worst = "DEGRADED"
    return worst


def _avg(points: list[MetricPoint], field: str) -> float:
    return round(mean(getattr(p, field) for p in points), 4)


def summarize(points: list[MetricPoint], *, now: datetime | None = None, stale_hours: int = 48) -> dict:
    """Status = worst of the last 3 observations (one noisy point cannot flip it), or STALE when the newest
    datapoint is older than `stale_hours` (a silent model must never look healthy)."""
    if not points:
        return {"status": "NO_DATA", "points": 0}
    now = now or utcnow()
    ordered = sorted(points, key=lambda p: p.timestamp)
    latest = ordered[-1]
    recent = [classify_point(p) for p in ordered[-3:]]
    status = max(recent, key=lambda c: SEVERITY[c])
    age_hours = (now - latest.timestamp).total_seconds() / 3600
    if age_hours > stale_hours:
        status = "STALE"
    return {
        "status": status,
        "points": len(points),
        "data_age_hours": round(age_hours, 1),
        "latest": {f: getattr(latest, f) for f in THRESHOLDS} | {"throughput_rpm": latest.throughput_rpm},
        "averages": {f: _avg(points, f) for f in [*THRESHOLDS, "throughput_rpm"]},
        "last_successful_inference": max(
            (p.timestamp for p in points if p.throughput_rpm > 0 and p.availability > 0), default=None),
    }


def model_metrics(session: Session, model_id: str, *, version: str | None, environment: str | None,
                  since: datetime | None, limit: int, stale_hours: int = 48) -> dict:
    stmt = select(MetricPoint).where(MetricPoint.model_id == model_id)
    if version:
        stmt = stmt.where(MetricPoint.version == version)
    if environment:
        stmt = stmt.where(MetricPoint.environment == environment)
    if since:
        stmt = stmt.where(MetricPoint.timestamp >= since)
    points = list(session.scalars(stmt.order_by(MetricPoint.timestamp.desc()).limit(limit)).all())
    points.reverse()  # chronological
    by_version: dict[str, list[MetricPoint]] = {}
    for p in points:
        by_version.setdefault(p.version, []).append(p)
    return {
        "model_id": model_id,
        "summary": summarize(points, stale_hours=stale_hours),
        "by_version": {v: summarize(ps, stale_hours=stale_hours) for v, ps in sorted(by_version.items())},
        "points": points,
    }
