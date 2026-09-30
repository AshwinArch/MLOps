"""Governance, live-state, isolation and resilience tests (review findings F4, F7, F8, F9, F13, F14, F18)."""
import time
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import metrics
from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import AuditLog, EnvironmentState, MetricPoint
from app.main import create_app
from app.workers.runtime import SimulatedRuntime
from tests.conftest import approve, deploy, hdr, make_db_url

DATA = Path(__file__).resolve().parents[3] / "data"


def make_client(tmp_path, **kw):
    base = dict(database_url=make_db_url(tmp_path), worker_enabled=False, seed_on_startup=False,
                runtime_step_seconds=0, log_level="WARNING", default_role="admin", enable_failure_simulation=True,
                require_four_eyes=False, require_artifact_checksum=False)
    return create_app(Settings(**(base | kw)), runtime=kw.pop("runtime", None) or SimulatedRuntime(0))


def audit_actions(client):
    return [a["action"] for a in client.get("/audit", headers=hdr("approver")).json()]


# ---- F4 governance holes
def test_four_eyes_blocks_self_approval(tmp_path):
    with TestClient(make_client(tmp_path, require_four_eyes=True)) as c:
        alice, bob = hdr("admin", "alice"), hdr("approver", "bob")
        c.post("/models", json={"name": "Four Eyes", "owner": "team", "framework": "sk"}, headers=alice)
        c.post("/models/four-eyes/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"}, headers=alice)
        c.post("/models/four-eyes/versions/1.0.0/transitions", json={"action": "validate"}, headers=alice)
        r = c.post("/models/four-eyes/versions/1.0.0/transitions", json={"action": "approve"}, headers=alice)
        assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPROVAL"
        r = c.post("/models/four-eyes/versions/1.0.0/transitions", json={"action": "approve"}, headers=bob)
        assert r.status_code == 200 and r.json()["approved_by"] == "bob"


def test_approval_can_be_revoked_after_staging(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    deploy(client, seeded_model, "1.0.0", "staging", run_worker)
    assert client.get(f"/models/{seeded_model}/versions/1.0.0").json()["stage"] == "STAGING"
    r = client.post(f"/models/{seeded_model}/versions/1.0.0/transitions", json={"action": "reject"})
    assert r.status_code == 200 and r.json()["approval_status"] == "REJECTED"
    r = deploy(client, seeded_model, "1.0.0", "production")
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_NOT_APPROVED"


def test_withdrawn_version_is_never_a_rollback_target(client, seeded_model, run_worker):
    for v in ("1.0.0", "2.0.0"):
        approve(client, seeded_model, v)
        for env in ("staging", "production"):
            assert deploy(client, seeded_model, v, env, run_worker).status_code == 202
    v2 = [d for d in client.get("/deployments", params={"environment": "production"}).json()["items"]
          if d["version"] == "2.0.0"][0]
    # 1.0.0 was superseded (ARCHIVED). Found faulty -> withdraw it.
    r = client.post(f"/models/{seeded_model}/versions/1.0.0/transitions", json={"action": "withdraw"})
    assert r.status_code == 200 and r.json()["stage"] == "WITHDRAWN"
    rb = client.post(f"/deployments/{v2['id']}/rollback")
    assert rb.status_code == 409 and rb.json()["error"]["code"] == "ROLLBACK_TARGET_UNSAFE"
    # the live version cannot be withdrawn
    r = client.post(f"/models/{seeded_model}/versions/2.0.0/transitions", json={"action": "withdraw"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_TRANSITION"
    assert deploy(client, seeded_model, "1.0.0", "staging").status_code == 409  # WITHDRAWN is not deployable


# ---- audit of blocked attempts
def test_denied_attempts_are_audited(client, seeded_model):
    r = deploy(client, seeded_model, "2.0.0", "production")
    assert r.status_code == 409
    client.post(f"/models/{seeded_model}/versions/2.0.0/transitions", json={"action": "approve"})  # DRAFT->approve
    acts = audit_actions(client)
    assert "deployment.denied" in acts and "version.approve.denied" in acts
    with client.app_state.session_factory() as s:
        row = s.query(AuditLog).filter(AuditLog.action == "deployment.denied").first()
        assert row.detail["code"] == "VERSION_NOT_APPROVED" and row.correlation_id


# ---- F8 live state is explicit; seed models the real situation
def test_seed_state_enables_rollback_of_first_deployment_after_seed(tmp_path):
    s = Settings(database_url=make_db_url(tmp_path), worker_enabled=False, seed_on_startup=True,
                 seed_dir=str(DATA), log_level="WARNING", default_role="admin", require_four_eyes=False,
                 require_artifact_checksum=False)
    app = create_app(s, runtime=SimulatedRuntime(0))
    with TestClient(app) as c:
        with app.state.session_factory() as db:
            st = db.get(EnvironmentState, ("compressor-anomaly-detector", "production"))
            assert st.version == "1.0.0" and st.live_deployment_id is None  # live without a deployment row
        m = "compressor-anomaly-detector"
        for a in ("validate", "approve"):
            c.post(f"/models/{m}/versions/1.1.0/transitions", json={"action": a})
        for env in ("staging", "production"):
            d = c.post("/deployments", json={"model_id": m, "version": "1.1.0", "environment": env}).json()
            app.state.worker.run_pending()
            assert c.get(f"/deployments/{d['id']}").json()["status"] == "SUCCEEDED"
        stages = {v["version"]: v["stage"] for v in c.get(f"/models/{m}/versions").json()}
        assert stages["1.0.0"] == "ARCHIVED" and stages["1.1.0"] == "PRODUCTION"  # exactly one PRODUCTION
        assert d["previous_version"] is None  # computed at success time, read back:
        assert c.get(f"/deployments/{d['id']}").json()["previous_version"] == "1.0.0"
        rb = c.post(f"/deployments/{d['id']}/rollback")
        assert rb.status_code == 202 and rb.json()["version"] == "1.0.0"


def test_live_pointer_moves_with_each_release_and_rollback(client, seeded_model, run_worker):
    for v in ("1.0.0", "2.0.0"):
        approve(client, seeded_model, v)
        for env in ("staging", "production"):
            deploy(client, seeded_model, v, env, run_worker)
    with client.app_state.session_factory() as s:
        assert s.get(EnvironmentState, (seeded_model, "production")).version == "2.0.0"
    prod = [d for d in client.get("/deployments").json()["items"] if d["environment"] == "production"
            and d["version"] == "2.0.0"][0]
    client.post(f"/deployments/{prod['id']}/rollback")
    run_worker()
    with client.app_state.session_factory() as s:
        assert s.get(EnvironmentState, (seeded_model, "production")).version == "1.0.0"


# ---- idempotency is scoped to the caller
def test_idempotency_key_is_scoped_per_user(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    body = {"model_id": seeded_model, "version": "1.0.0", "environment": "staging"}
    a = client.post("/deployments", json=body, headers=hdr("admin", "alice") | {"Idempotency-Key": "k"})
    assert a.status_code == 202
    b = client.post("/deployments", json=body, headers=hdr("admin", "bob") | {"Idempotency-Key": "k"})
    # bob's key 'k' is NOT matched against alice's record. The identical request is still de-duplicated by the
    # natural key (same model/version/environment already active), and no second deployment is created.
    assert b.status_code == 200 and b.json()["id"] == a.json()["id"]
    assert client.get("/deployments").json()["total"] == 1
    # a different body under bob's own key is his own request, never an "IDEMPOTENCY_KEY_REUSED" against alice
    bob = hdr("admin", "bob") | {"Idempotency-Key": "k"}
    c = client.post("/deployments", json=body | {"environment": "dev"}, headers=bob)
    assert c.status_code == 202 and c.json()["requested_by"] == "bob"
    again = client.post("/deployments", json=body, headers=hdr("admin", "alice") | {"Idempotency-Key": "k"})
    assert again.status_code == 200 and again.json()["id"] == a.json()["id"]


# ---- F13 runtime timeout + concurrency
class SlowRuntime(SimulatedRuntime):
    def __init__(self, delays):
        super().__init__(0)
        self.delays = delays

    def deploy(self, **kw):
        time.sleep(self.delays.get(kw["model_id"], 0))
        return super().deploy(**kw)


def test_runtime_timeout_fails_transient_and_retry_succeeds(tmp_path):
    rt = SlowRuntime({"pump-model": 0.6})
    app = make_client(tmp_path, runtime_timeout_seconds=0.15, runtime=rt)
    with TestClient(app) as c:
        c.post("/models", json={"name": "Pump Model", "owner": "team", "framework": "sk"})
        c.post("/models/pump-model/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"})
        approve(c, "pump-model", "1.0.0")
        d = c.post("/deployments", json={"model_id": "pump-model", "version": "1.0.0", "environment": "staging"}).json()
        app.state.worker.run_pending()
        got = c.get(f"/deployments/{d['id']}").json()
        assert got["status"] == "FAILED" and got["failure_reason"] == "runtime_timeout"
        assert got["failure_class"] == "TRANSIENT" and got["retryable"] is True
        rt.delays.clear()
        assert c.post(f"/deployments/{d['id']}/retry").status_code == 202
        app.state.worker.run_pending()
        assert c.get(f"/deployments/{d['id']}").json()["status"] == "SUCCEEDED"


def test_slow_runtime_does_not_block_other_deployments(tmp_path):
    rt = SlowRuntime({"slow-model": 0.8})
    app = make_client(tmp_path, runtime=rt, worker_concurrency=4)
    with TestClient(app) as c:
        for name in ("slow-model", "fast-model"):
            c.post("/models", json={"name": name, "owner": "team", "framework": "sk"})
            c.post(f"/models/{name}/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"})
            approve(c, name, "1.0.0")
        ids = {}
        for n in ("slow-model", "fast-model"):
            body = {"model_id": n, "version": "1.0.0", "environment": "staging"}
            ids[n] = c.post("/deployments", json=body).json()["id"]
        start = time.perf_counter()
        app.state.worker.run_pending()
        assert time.perf_counter() - start < 1.4  # the fast deployment finishes without waiting for the slow one
        assert {c.get(f"/deployments/{i}").json()["status"] for i in ids.values()} == {"SUCCEEDED"}


# ---- F14 artifact integrity / F18 validation gate
def test_artifact_checksum_and_store_allowlist(tmp_path):
    with TestClient(make_client(tmp_path, artifact_uri_prefixes="s3://acme-models/")) as c:
        c.post("/models", json={"name": "Safe Model", "owner": "team", "framework": "sk"})
        url = "/models/safe-model/versions"
        ok = c.post(url, json={"version": "1.0.0", "artifact_uri": "s3://acme-models/a", "artifact_checksum":
                               "sha256:" + "a" * 64})
        assert ok.status_code == 201 and ok.json()["artifact_checksum"] == "sha256:" + "a" * 64
        bad = c.post(url, json={"version": "1.0.1", "artifact_uri": "s3://evil/a"})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "ARTIFACT_STORE_NOT_ALLOWED"
        bad = c.post(url, json={"version": "1.0.2", "artifact_uri": "s3://acme-models/a", "artifact_checksum": "md5:1"})
        assert bad.status_code == 422
        assert c.post(url, json={"version": "1.0.3", "artifact_uri": "file:///etc/passwd"}).status_code == 422


def test_validation_gate_requires_configured_fields(tmp_path):
    with TestClient(make_client(tmp_path, validation_required_fields="training_data_ref,algorithm")) as c:
        c.post("/models", json={"name": "Gate Model", "owner": "team", "framework": "sk"})
        c.post("/models/gate-model/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"})
        r = c.post("/models/gate-model/versions/1.0.0/transitions", json={"action": "validate"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "VALIDATION_INCOMPLETE"
        assert set(r.json()["error"]["details"]["missing"]) == {"training_data_ref", "algorithm"}
        c.post("/models/gate-model/versions", json={"version": "1.1.0", "artifact_uri": "s3://m/x",
                                                     "training_data_ref": "s3://d/1", "algorithm": "rf"})
        assert c.post("/models/gate-model/versions/1.1.0/transitions", json={"action": "validate"}).status_code == 200


# ---- F9/F11 observability + monitoring freshness
def test_histogram_and_request_counters_exposed(client):
    metrics.reset()
    client.get("/health")
    body = client.get("/metrics").text
    assert 'http_request_duration_seconds_bucket{method="GET",le="+Inf"}' in body
    assert "http_request_duration_seconds_count" in body


def test_stale_data_is_never_healthy(client, seeded_model):
    with client.app_state.session_factory() as s:
        s.add(MetricPoint(model_id=seeded_model, version="1.0.0", environment="production",
                          timestamp=utcnow() - timedelta(days=60), latency_ms=90, throughput_rpm=1000,
                          error_rate=0.01, quality_score=0.9, drift_score=0.1, availability=99.9))
        s.commit()
    m = client.get(f"/models/{seeded_model}/metrics").json()
    assert m["summary"]["status"] == "STALE" and m["summary"]["data_age_hours"] > 48


def test_status_uses_recent_window_not_single_point(client, seeded_model):
    base = dict(model_id=seeded_model, version="1.0.0", environment="production", latency_ms=90, throughput_rpm=1000,
                error_rate=0.01, quality_score=0.9, drift_score=0.1, availability=99.9)
    with client.app_state.session_factory() as s:
        s.add(MetricPoint(timestamp=utcnow() - timedelta(hours=3), **base))
        s.add(MetricPoint(timestamp=utcnow() - timedelta(hours=2), **{**base, "error_rate": 0.09}))  # spike
        s.add(MetricPoint(timestamp=utcnow() - timedelta(hours=1), **base))
        s.commit()
    assert client.get(f"/models/{seeded_model}/metrics").json()["summary"]["status"] == "CRITICAL"


# ---- retry hint reflects STALE_RETRY
def test_retryable_hint_false_when_superseded(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    approve(client, seeded_model, "2.0.0")
    old = deploy(client, seeded_model, "1.0.0", "staging", run_worker, simulate_failure="transient").json()
    assert client.get(f"/deployments/{old['id']}").json()["retryable"] is True
    deploy(client, seeded_model, "2.0.0", "staging", run_worker)
    assert client.get(f"/deployments/{old['id']}").json()["retryable"] is False
