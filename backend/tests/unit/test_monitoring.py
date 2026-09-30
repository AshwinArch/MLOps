from datetime import UTC, datetime

from app.db.models import MetricPoint
from app.services import monitoring


def point(**kw):
    base = dict(model_id="m", version="1", environment="production", timestamp=datetime(2026, 7, 1, tzinfo=UTC),
                latency_ms=90, throughput_rpm=1000, error_rate=0.01, quality_score=0.9, drift_score=0.1,
                availability=99.9)
    return MetricPoint(**(base | kw))


def test_healthy():
    assert monitoring.classify_point(point()) == "HEALTHY"


def test_degraded_on_drift():
    assert monitoring.classify_point(point(drift_score=0.3)) == "DEGRADED"


def test_critical_on_low_quality_or_errors():
    assert monitoring.classify_point(point(quality_score=0.7)) == "CRITICAL"
    assert monitoring.classify_point(point(error_rate=0.06)) == "CRITICAL"


def test_summary_empty_and_last_inference():
    assert monitoring.summarize([])["status"] == "NO_DATA"
    a = point(timestamp=datetime(2026, 7, 1, tzinfo=UTC))
    b = point(timestamp=datetime(2026, 7, 2, tzinfo=UTC), throughput_rpm=0)
    s = monitoring.summarize([a, b])
    assert s["last_successful_inference"] == a.timestamp
    assert s["points"] == 2


def test_summary_stale_and_window():
    from datetime import timedelta
    now = datetime(2026, 10, 1, tzinfo=UTC)
    fresh = [point(timestamp=now - timedelta(hours=h)) for h in (3, 2, 1)]
    assert monitoring.summarize(fresh, now=now)["status"] == "HEALTHY"
    old = [point(timestamp=now - timedelta(days=10))]
    assert monitoring.summarize(old, now=now)["status"] == "STALE"
    spike = [point(timestamp=now - timedelta(hours=3)), point(timestamp=now - timedelta(hours=2), error_rate=0.09),
             point(timestamp=now - timedelta(hours=1))]
    assert monitoring.summarize(spike, now=now)["status"] == "CRITICAL"
