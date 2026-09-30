"""Regression tests for defects found in the independent technical review (F2, F3, F5, F6, F12, correlation id)."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import Deployment
from app.main import create_app
from app.workers.runtime import SimulatedRuntime
from tests.conftest import approve, deploy, hdr, make_db_url


def _age(client, dep_id, status):
    with client.app_state.session_factory() as s:
        row = s.get(Deployment, dep_id)
        row.status, row.updated_at = status, utcnow() - timedelta(hours=1)
        s.commit()


# ---- F2: stuck VALIDATING must not block the environment forever
def test_stuck_validating_is_requeued_and_completes(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging").json()
    _age(client, dep["id"], "VALIDATING")  # worker died right after claiming
    blocked = deploy(client, seeded_model, "1.0.0", "staging", simulate_failure="transient")
    assert blocked.status_code == 409  # environment is (correctly) held while the row is active
    assert client.app_state.worker.reconcile() == 1
    assert client.get(f"/deployments/{dep['id']}").json()["status"] == "REQUESTED"
    run_worker()
    assert client.get(f"/deployments/{dep['id']}").json()["status"] == "SUCCEEDED"
    events = [e["event"] for e in client.get(f"/deployments/{dep['id']}/events").json()]
    assert "validation_requeued" in events


def test_poison_validating_row_is_capped(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging").json()
    w = client.app_state.worker
    for _ in range(w.MAX_REQUEUES):
        _age(client, dep["id"], "VALIDATING")
        w.reconcile()
    _age(client, dep["id"], "VALIDATING")
    w.reconcile()
    d = client.get(f"/deployments/{dep['id']}").json()
    assert d["status"] == "FAILED" and d["failure_reason"] == "validation_requeue_limit"
    assert d["failure_class"] == "RECONCILIATION"


# ---- F3: a stale retry must never overwrite a newer release
def test_stale_retry_is_refused(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    approve(client, seeded_model, "2.0.0")
    old = deploy(client, seeded_model, "1.0.0", "staging", run_worker, simulate_failure="transient").json()
    assert client.get(f"/deployments/{old['id']}").json()["status"] == "FAILED"
    new = deploy(client, seeded_model, "2.0.0", "staging", run_worker).json()
    assert client.get(f"/deployments/{new['id']}").json()["status"] == "SUCCEEDED"
    r = client.post(f"/deployments/{old['id']}/retry")
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_RETRY"
    assert r.json()["error"]["details"]["newer_deployment_id"] == new["id"]


# ---- F5: bounded inputs (Postgres would 500 on these)
@pytest.mark.parametrize("field,value", [("artifact_uri", "s3://" + "x" * 600), ("framework", "f" * 80),
                                         ("algorithm", "a" * 101), ("training_data_ref", "t" * 501)])
def test_overlong_version_fields_rejected(client, seeded_model, field, value):
    body = {"version": "9.0.0", "artifact_uri": "s3://m/x", field: value}
    r = client.post(f"/models/{seeded_model}/versions", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_metadata_and_tags_bounded(client, seeded_model):
    big = {"version": "9.0.0", "artifact_uri": "s3://m/x", "metadata": {"k": "v" * 20_000}}
    assert client.post(f"/models/{seeded_model}/versions", json=big).status_code == 422
    many = {"version": "9.0.0", "artifact_uri": "s3://m/x", "tags": ["t"] * 21}
    assert client.post(f"/models/{seeded_model}/versions", json=many).status_code == 422


def test_hostile_correlation_id_is_replaced(client):
    r = client.get("/health", headers={"X-Correlation-ID": "x" * 100})
    assert r.headers["X-Correlation-ID"] != "x" * 100 and len(r.headers["X-Correlation-ID"]) <= 64
    r = client.get("/health", headers={"X-Correlation-ID": "bad id with spaces"})
    assert " " not in r.headers["X-Correlation-ID"]


# ---- 500 responses must still carry the correlation id
def test_500_keeps_correlation_id(settings):
    app = create_app(settings, runtime=SimulatedRuntime(0))

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom", headers={"X-Correlation-ID": "trace-me-1"})
    assert r.status_code == 500
    assert r.json()["error"]["correlation_id"] == "trace-me-1"
    assert r.headers["X-Correlation-ID"] == "trace-me-1"


# ---- F6: fail-closed defaults
def test_secure_defaults():
    s = Settings()
    assert s.default_role == "viewer" and s.seed_on_startup is False and s.enable_failure_simulation is False


def test_no_headers_means_read_only(tmp_path):
    s = Settings(database_url=make_db_url(tmp_path), worker_enabled=False, log_level="WARNING")
    with TestClient(create_app(s)) as c:
        assert c.get("/models").status_code == 200
        r = c.post("/models", json={"name": "Nope", "owner": "team", "framework": "sk"})
        assert r.status_code == 403
        assert c.get("/audit").status_code == 403


def test_failure_simulation_disabled_by_default(tmp_path):
    s = Settings(database_url=make_db_url(tmp_path), worker_enabled=False, log_level="WARNING",
                 default_role="admin", require_four_eyes=False)
    with TestClient(create_app(s)) as c:
        c.post("/models", json={"name": "Sim Model", "owner": "team", "framework": "sk"})
        c.post("/models/sim-model/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"})
        for a in ("validate", "approve"):
            c.post("/models/sim-model/versions/1.0.0/transitions", json={"action": a})
        r = c.post("/deployments", json={"model_id": "sim-model", "version": "1.0.0", "environment": "staging",
                                         "simulate_failure": "permanent"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "SIMULATION_DISABLED"


# ---- F12: inventory must not issue one query per model
def test_inventory_query_count_is_constant(client):
    for i in range(12):
        client.post("/models", json={"name": f"Model {i:02d}", "owner": "team", "framework": "sk"})
        client.post(f"/models/model-{i:02d}/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"})
    counter = {"n": 0}
    engine = client.app_state.engine

    def count(*_a, **_k):
        counter["n"] += 1
    event.listen(engine, "before_cursor_execute", count)
    assert client.get("/models").json()["total"] == 12
    event.remove(engine, "before_cursor_execute", count)
    assert counter["n"] <= 4, counter["n"]


# ---- stage failures are reported honestly, not as approval failures
def test_stage_failure_reason(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging").json()
    client.post(f"/models/{seeded_model}/versions/1.0.0/transitions", json={"action": "archive"})
    run_worker()
    d = client.get(f"/deployments/{dep['id']}").json()
    assert d["failure_reason"] == "stage_validation_failed" and d["failure_class"] == "PERMANENT"


def test_runtime_port_lookup(client):
    rt = client.app_state.runtime
    assert rt.lookup("nope") is None
    rt.register_external("tok", "rt-1")
    assert rt.lookup("tok") == "rt-1" and rt.status("rt-1") == "running"
    _ = (hdr, text)


def test_worker_metrics_endpoint():
    import socket
    import urllib.request

    from app.core import metrics
    from app.workers.run import _serve_metrics

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    metrics.reset()
    metrics.inc("deployments_succeeded_total", environment="staging")
    _serve_metrics(port)
    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=3).read().decode()
    assert "deployments_succeeded_total" in body
