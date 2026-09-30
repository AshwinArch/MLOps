"""The 10 acceptance scenarios from the brief, end to end through HTTP + worker + DB."""
import threading
from datetime import timedelta

from app.db.base import utcnow
from app.db.models import Deployment
from tests.conftest import approve, deploy, make_db_url


def promote(client, run_worker, model, version, env="production"):
    r = deploy(client, model, version, env, run_worker)
    assert r.status_code in (200, 202), r.text
    d = client.get(f"/deployments/{r.json()['id']}").json()
    assert d["status"] == "SUCCEEDED", d
    return d


def test_register_approve_deploy_metrics_rollback(client, seeded_model, run_worker):
    # 1-2 register model + two versions (fixture), approve both
    approve(client, seeded_model, "1.0.0")
    approve(client, seeded_model, "2.0.0")
    # 4 deploy 1.0.0 staging -> production
    promote(client, run_worker, seeded_model, "1.0.0", "staging")
    v1 = promote(client, run_worker, seeded_model, "1.0.0", "production")
    assert v1["previous_version"] is None
    # cannot roll back the first-ever production deployment: nothing safe to go back to
    r = client.post(f"/deployments/{v1['id']}/rollback")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ROLLBACK_TARGET_MISSING"
    # release 2.0.0
    promote(client, run_worker, seeded_model, "2.0.0", "staging")
    v2 = promote(client, run_worker, seeded_model, "2.0.0", "production")
    assert v2["previous_version"] == "1.0.0"
    stages = {v["version"]: v["stage"] for v in client.get(f"/models/{seeded_model}/versions").json()}
    assert stages == {"1.0.0": "ARCHIVED", "2.0.0": "PRODUCTION"}
    # stale rollback of v1 (no longer live) must be refused
    r = client.post(f"/deployments/{v1['id']}/rollback")
    assert r.status_code == 409 and r.json()["error"]["code"] in ("ROLLBACK_NOT_ALLOWED", "STALE_ROLLBACK")
    # 7 roll back v2 -> 1.0.0 (idempotent request)
    rb = client.post(f"/deployments/{v2['id']}/rollback")
    assert rb.status_code == 202 and rb.json()["rollback_of"] == v2["id"] and rb.json()["version"] == "1.0.0"
    again = client.post(f"/deployments/{v2['id']}/rollback")
    assert again.status_code == 200 and again.json()["id"] == rb.json()["id"]
    run_worker()
    assert client.get(f"/deployments/{rb.json()['id']}").json()["status"] == "SUCCEEDED"
    assert client.get(f"/deployments/{v2['id']}").json()["status"] == "ROLLED_BACK"
    stages = {v["version"]: v["stage"] for v in client.get(f"/models/{seeded_model}/versions").json()}
    assert stages == {"1.0.0": "PRODUCTION", "2.0.0": "VALIDATED"}  # faulty version must be re-validated
    bad = client.get(f"/models/{seeded_model}/versions/2.0.0").json()
    assert bad["approval_status"] == "PENDING" and bad["approved_by"] is None
    # ... and cannot be redeployed to production without a fresh approval
    r = deploy(client, seeded_model, "2.0.0", "production")
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_NOT_APPROVED"
    # a rollback cannot be rolled back
    r = client.post(f"/deployments/{rb.json()['id']}/rollback")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ROLLBACK_OF_ROLLBACK"
    # timeline contains the whole story
    events = [e["event"] for e in client.get("/events", params={"model_id": seeded_model}).json()["items"]]
    assert "rollback_completed" in events and "deployment_rolled_back" in events


def test_metrics_endpoint_with_points(client, seeded_model):
    from app.db.models import MetricPoint
    with client.app_state.session_factory() as s:
        for i in range(3):
            s.add(MetricPoint(model_id=seeded_model, version="1.0.0", environment="production",
                              timestamp=utcnow() - timedelta(days=i), latency_ms=90, throughput_rpm=1000,
                              error_rate=0.01, quality_score=0.9, drift_score=0.1, availability=99.9))
        s.add(MetricPoint(model_id=seeded_model, version="2.0.0", environment="production", timestamp=utcnow(),
                          latency_ms=300, throughput_rpm=900, error_rate=0.01, quality_score=0.9,
                          drift_score=0.1, availability=99.9))
        s.commit()
    m = client.get(f"/models/{seeded_model}/metrics").json()
    assert len(m["points"]) == 4 and set(m["by_version"]) == {"1.0.0", "2.0.0"}
    assert m["by_version"]["1.0.0"]["status"] == "HEALTHY"
    assert m["by_version"]["2.0.0"]["status"] == "CRITICAL"  # version comparison surfaces the regression
    only = client.get(f"/models/{seeded_model}/metrics", params={"version": "1.0.0"}).json()
    assert len(only["points"]) == 3
    assert client.get("/models/nope/metrics").status_code == 404


def test_concurrent_duplicate_requests_create_one_deployment(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    results = []

    def go():
        body = {"model_id": seeded_model, "version": "1.0.0", "environment": "staging"}
        results.append(client.post("/deployments", json=body, headers={"Idempotency-Key": "race"}))
    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert {r.status_code for r in results} <= {200, 202}
    assert len({r.json()["id"] for r in results}) == 1
    assert client.get("/deployments").json()["total"] == 1


def test_worker_claim_is_exclusive(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging").json()
    w = client.app_state.worker
    with client.app_state.session_factory() as s:
        assert w._claim(s, dep["id"]) is True
        assert w._claim(s, dep["id"]) is False  # second worker loses the race


def test_reconciler_recovers_external_success_with_db_failure(client, seeded_model):
    """Runtime deployed OK but the DB never recorded it (crash between the two): reconciler heals the row."""
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging").json()
    w = client.app_state.worker
    with client.app_state.session_factory() as s:
        assert w._claim(s, dep["id"])
        row = s.get(Deployment, dep["id"])
        row.status = "DEPLOYING"
        row.updated_at = utcnow() - timedelta(hours=1)
        s.commit()
    client.app_state.runtime.register_external(dep["id"], "rt-external-1")
    assert w.reconcile() == 1
    d = client.get(f"/deployments/{dep['id']}").json()
    assert d["status"] == "SUCCEEDED" and d["external_ref"] == "rt-external-1"


def test_reconciler_fails_unknown_external_state_as_retryable(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging").json()
    w = client.app_state.worker
    with client.app_state.session_factory() as s:
        w._claim(s, dep["id"])
        row = s.get(Deployment, dep["id"])
        row.status, row.updated_at = "DEPLOYING", utcnow() - timedelta(hours=1)
        s.commit()
    w.reconcile()
    d = client.get(f"/deployments/{dep['id']}").json()
    assert d["status"] == "FAILED" and d["failure_class"] == "RECONCILIATION" and d["retryable"] is True


def test_metrics_and_audit_visible(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    deploy(client, seeded_model, "1.0.0", "staging", run_worker)
    text = client.get("/metrics").text
    assert "deployments_succeeded_total" in text and "http_requests_total" in text


def test_seed_loader_is_idempotent(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app
    data = Path(__file__).resolve().parents[3] / "data"
    s = Settings(database_url=make_db_url(tmp_path), worker_enabled=False, seed_on_startup=True,
                 seed_dir=str(data), log_level="WARNING", default_role="admin")
    for _ in range(2):  # restart twice
        with TestClient(create_app(s)) as c:
            assert c.get("/models").json()["total"] == 3
    with TestClient(create_app(s)) as c:
        deps = {d["id"]: d for d in c.get("/deployments").json()["items"]}
        assert deps["dep-1002"]["failure_class"] == "PERMANENT" and deps["dep-1003"]["failure_class"] == "TRANSIENT"
        m = c.get("/models/pump-failure-predictor/metrics").json()
        assert len(m["points"]) == 30 and set(m["by_version"]) == {"1.0.0", "2.0.0"}
        assert c.get("/models/pump-failure-predictor").json()["production_version"] == "2.0.0"
