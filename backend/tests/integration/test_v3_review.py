"""Review v3 regressions: authentication, production guard, completion-time governance, late completions,
database-level invariants, HTTP runtime adapter, packaging."""
import secrets
import time
from pathlib import Path

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, validate_production
from app.db.models import ModelVersion
from app.main import create_app
from app.workers.factory import build_runtime
from app.workers.http_runtime import HttpRuntime
from app.workers.runtime import RuntimeFailure, SimulatedRuntime
from tests.conftest import approve, make_db_url
from tests.integration.test_governance_and_state import SlowRuntime, make_client

ROOT = Path(__file__).resolve().parents[3]
SECRET = secrets.token_hex(24)  # generated per run; never a real key


def token(sub="alice", role="admin", **kw):
    claims = {"sub": sub, "role": role, "exp": int(time.time()) + 600, "iss": "idp", "aud": "mlops"} | kw
    return {"Authorization": "Bearer " + jwt.encode(claims, SECRET, algorithm="HS256")}


def jwt_app(tmp_path, **kw):
    return make_client(tmp_path, auth_mode="jwt", jwt_secret=SECRET, jwt_issuer="idp", jwt_audience="mlops",
                       default_role="viewer", **kw)


# ---- F1 authentication
def test_jwt_mode_rejects_anonymous_and_forged_headers(tmp_path):
    with TestClient(jwt_app(tmp_path)) as c:
        assert c.get("/models").status_code == 401
        assert c.get("/health").status_code == 200 and c.get("/ready").status_code == 200
        forged = {"X-Role": "admin", "X-User": "root"}
        r = c.post("/models", json={"name": "Forge", "owner": "team", "framework": "sk"}, headers=forged)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
        assert c.get("/metrics").status_code == 401


def test_jwt_validates_signature_expiry_issuer_audience(tmp_path):
    with TestClient(jwt_app(tmp_path)) as c:
        ok = token()
        assert c.get("/models", headers=ok).status_code == 200
        bad = {
            "expired": token(exp=int(time.time()) - 5),
            "issuer": token(iss="evil"),
            "audience": token(aud="other"),
            "signature": {"Authorization": "Bearer " + jwt.encode({"sub": "a", "exp": int(time.time()) + 60},
                                                                   secrets.token_hex(24), algorithm="HS256")},
            "garbage": {"Authorization": "Bearer not-a-jwt"},
        }
        for name, h in bad.items():
            assert c.get("/models", headers=h).status_code == 401, name


def test_jwt_role_comes_from_claim_and_four_eyes_uses_subject(tmp_path):
    with TestClient(jwt_app(tmp_path, require_four_eyes=True)) as c:
        alice, bob = token("alice", "admin"), token("bob", "approver")
        assert c.post("/models", json={"name": "Jwt Model", "owner": "team", "framework": "sk"},
                      headers=token("viewer1", "viewer")).status_code == 403
        c.post("/models", json={"name": "Jwt Model", "owner": "team", "framework": "sk"}, headers=alice)
        c.post("/models/jwt-model/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"}, headers=alice)
        c.post("/models/jwt-model/versions/1.0.0/transitions", json={"action": "validate"}, headers=alice)
        spoof = alice | {"X-User": "bob"}  # changing a header does nothing: identity is the token subject
        r = c.post("/models/jwt-model/versions/1.0.0/transitions", json={"action": "approve"}, headers=spoof)
        assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPROVAL"
        r = c.post("/models/jwt-model/versions/1.0.0/transitions", json={"action": "approve"}, headers=bob)
        assert r.status_code == 200 and r.json()["approved_by"] == "bob"


def test_metrics_scrape_token(tmp_path):
    with TestClient(make_client(tmp_path, metrics_token="scrape-me")) as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer scrape-me"}).status_code == 200


# ---- F22 production guard
def test_production_guard_rejects_unsafe_settings():
    with pytest.raises(RuntimeError) as e:
        validate_production(Settings(environment="production"))
    msg = str(e.value)
    for needle in ("AUTH_MODE", "RUNTIME_ADAPTER", "METRICS_TOKEN", "DATABASE_URL", "AUTO_CREATE_SCHEMA"):
        assert needle in msg
    with pytest.raises(RuntimeError):
        create_app(Settings(environment="production"))


def test_production_guard_accepts_hardened_settings():
    validate_production(Settings(
        environment="production", auth_mode="jwt", jwt_secret=SECRET, jwt_issuer="idp", jwt_audience="mlops",
        default_role="viewer", seed_on_startup=False, enable_failure_simulation=False, auto_create_schema=False,
        require_four_eyes=True, require_artifact_checksum=True, runtime_adapter="http",
        runtime_http_url="https://rt.internal", metrics_token="t", database_url="postgresql://u:p@db/x",
        runtime_timeout_seconds=60, stuck_deployment_seconds=300))


# ---- F3 governance re-checked when the deployment finalises
class RaceRuntime(SimulatedRuntime):
    """Runs a callback in the middle of the runtime call, like a concurrent approver/archiver would."""

    def __init__(self, hook):
        super().__init__(0)
        self.hook = hook

    def deploy(self, **kw):
        if kw["environment"] == "production":
            self.hook()
        return super().deploy(**kw)


def _to_staging(c, app, name):
    c.post("/models", json={"name": name, "owner": "team", "framework": "sk"})
    c.post(f"/models/{name}/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"})
    approve(c, name, "1.0.0")
    d = c.post("/deployments", json={"model_id": name, "version": "1.0.0", "environment": "staging"}).json()
    app.state.worker.run_pending()
    assert c.get(f"/deployments/{d['id']}").json()["status"] == "SUCCEEDED"


@pytest.mark.parametrize("stage,approval,case",
                         [("DRAFT", "REJECTED", "revoked"), ("ARCHIVED", "APPROVED", "archived")])
def test_revoked_or_archived_mid_deploy_never_reaches_production(tmp_path, stage, approval, case):
    holder = {}

    def hook():
        with holder["app"].state.session_factory() as s:
            s.execute(text("UPDATE model_versions SET stage=:st, approval_status=:ap"), {"st": stage, "ap": approval})
            s.commit()

    rt = RaceRuntime(hook)
    app = make_client(tmp_path, runtime=rt)
    holder["app"] = app
    with TestClient(app) as c:
        _to_staging(c, app, "race-model")
        d = c.post("/deployments", json={"model_id": "race-model", "version": "1.0.0",
                                         "environment": "production"}).json()
        app.state.worker.run_pending()
        got = c.get(f"/deployments/{d['id']}").json()
        assert got["status"] == "FAILED" and got["failure_reason"] == "approval_revoked_during_deploy", case
        assert got["failure_class"] == "PERMANENT" and got["retryable"] is False
        assert rt.lookup(d["id"]) is None  # compensating undeploy removed it from the runtime
        with app.state.session_factory() as s:
            mv = s.query(ModelVersion).one()
            assert mv.stage == stage and mv.stage != "PRODUCTION"
            live = s.execute(text("SELECT version FROM environment_state WHERE environment='production'")).first()
            assert live is None  # the live pointer never moved


def test_api_refuses_reject_or_archive_while_deploying(tmp_path):
    seen = {}
    holder = {}

    def hook():
        c = holder["c"]
        for action in ("reject", "archive", "withdraw"):
            r = c.post("/models/busy-model/versions/1.0.0/transitions", json={"action": action})
            seen[action] = (r.status_code, r.json()["error"]["code"])

    app = make_client(tmp_path, runtime=RaceRuntime(hook))
    with TestClient(app) as c:
        holder["c"] = c
        _to_staging(c, app, "busy-model")
        c.post("/deployments", json={"model_id": "busy-model", "version": "1.0.0", "environment": "production"})
        app.state.worker.run_pending()
    assert seen == {a: (409, "VERSION_IN_USE") for a in ("reject", "archive", "withdraw")}


# ---- F4 late completion after a timeout
def test_late_completion_superseded_is_undeployed(tmp_path):
    rt = SlowRuntime({"late-model": 0.5})
    app = make_client(tmp_path, runtime_timeout_seconds=0.1, runtime=rt)
    with TestClient(app) as c:
        c.post("/models", json={"name": "Late Model", "owner": "team", "framework": "sk"})
        for v in ("1.0.0", "2.0.0"):
            c.post("/models/late-model/versions", json={"version": v, "artifact_uri": "s3://m/x"})
            approve(c, "late-model", v)
        d1 = c.post("/deployments", json={"model_id": "late-model", "version": "1.0.0",
                                          "environment": "staging"}).json()
        app.state.worker.run_pending()
        assert c.get(f"/deployments/{d1['id']}").json()["failure_reason"] == "runtime_timeout"
        rt.delays.clear()
        d2 = c.post("/deployments", json={"model_id": "late-model", "version": "2.0.0",
                                          "environment": "staging"}).json()
        app.state.worker.run_pending()
        assert c.get(f"/deployments/{d2['id']}").json()["status"] == "SUCCEEDED"
        time.sleep(0.6)  # the stalled v1 call completes remotely after v2 went live
        assert rt.lookup(d1["id"])
        assert app.state.worker.sweep_late_completions() == 1
        assert rt.lookup(d1["id"]) is None
        events = [e["event"] for e in c.get(f"/deployments/{d1['id']}/events").json()]
        assert "late_completion_undeployed" in events
        assert app.state.worker.sweep_late_completions() == 0  # once only


def test_late_completion_without_successor_is_flagged_not_undone(tmp_path):
    rt = SlowRuntime({"flag-model": 0.4})
    app = make_client(tmp_path, runtime_timeout_seconds=0.1, runtime=rt)
    with TestClient(app) as c:
        c.post("/models", json={"name": "Flag Model", "owner": "team", "framework": "sk"})
        c.post("/models/flag-model/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"})
        approve(c, "flag-model", "1.0.0")
        d = c.post("/deployments", json={"model_id": "flag-model", "version": "1.0.0",
                                         "environment": "staging"}).json()
        app.state.worker.run_pending()
        time.sleep(0.5)
        assert app.state.worker.sweep_late_completions() == 1 and rt.lookup(d["id"])
        events = [e["event"] for e in c.get(f"/deployments/{d['id']}/events").json()]
        assert "late_completion_detected" in events


def test_drift_check_flags_runtime_missing_live_deployment(tmp_path):
    rt = SimulatedRuntime(0)
    app = make_client(tmp_path, runtime=rt)
    with TestClient(app) as c:
        c.post("/models", json={"name": "Drift Model", "owner": "team", "framework": "sk"})
        c.post("/models/drift-model/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"})
        approve(c, "drift-model", "1.0.0")
        d = c.post("/deployments", json={"model_id": "drift-model", "version": "1.0.0",
                                         "environment": "staging"}).json()
        app.state.worker.run_pending()
        app.state.worker._last_drift = 0.0
        assert app.state.worker.check_drift() == 0
        rt.undeploy(d["id"])  # runtime lost it (restart / manual delete)
        app.state.worker._last_drift = 0.0
        assert app.state.worker.check_drift() == 1


# ---- F5 database enforces invariants
def _exec(app, sql, **params):
    with app.state.session_factory() as s:
        s.execute(text(sql), params)
        s.commit()


def test_database_rejects_illegal_governance_rows(tmp_path):
    app = make_client(tmp_path)
    with TestClient(app) as c:
        c.post("/models", json={"name": "Db Model", "owner": "team", "framework": "sk"})
        for v in ("1.0.0", "2.0.0"):
            c.post("/models/db-model/versions", json={"version": v, "artifact_uri": "s3://m/x"})
        with pytest.raises(IntegrityError):  # live without approval
            _exec(app, "UPDATE model_versions SET stage='PRODUCTION' WHERE version='1.0.0'")
        with pytest.raises(IntegrityError):  # stage outside the closed set
            _exec(app, "UPDATE model_versions SET stage='BOGUS' WHERE version='1.0.0'")
        with pytest.raises(IntegrityError):
            _exec(app, "UPDATE model_versions SET approval_status='MAYBE' WHERE version='1.0.0'")
        _exec(app, "UPDATE model_versions SET stage='PRODUCTION', approval_status='APPROVED' WHERE version='1.0.0'")
        with pytest.raises(IntegrityError):  # second PRODUCTION version for the same model
            _exec(app, "UPDATE model_versions SET stage='PRODUCTION', approval_status='APPROVED' "
                       "WHERE version='2.0.0'")
        with pytest.raises(IntegrityError):  # deployment status outside the closed set
            _exec(app, "INSERT INTO deployments (id, model_id, version, environment, status, attempt, requested_by,"
                       " simulate_failure, created_at, updated_at, row_version) VALUES ('x1','db-model','1.0.0',"
                       "'production','WEIRD',1,'u','none','2026-01-01','2026-01-01',1)")


@pytest.mark.skipif(not make_db_url.__module__ or "postgresql" not in __import__("os").environ.get(
    "TEST_DATABASE_URL", ""), reason="foreign keys are only enforced by default on PostgreSQL")
def test_deployment_must_reference_an_existing_version(tmp_path):
    app = make_client(tmp_path)
    with TestClient(app) as c:
        c.post("/models", json={"name": "Fk Model", "owner": "team", "framework": "sk"})
        with pytest.raises(IntegrityError):
            _exec(app, "INSERT INTO deployments (id, model_id, version, environment, status, attempt, requested_by,"
                       " simulate_failure, created_at, updated_at, row_version) VALUES ('x2','fk-model','9.9.9',"
                       "'staging','REQUESTED',1,'u','none',NOW(),NOW(),1)")


# ---- F2 HTTP runtime adapter (mock transport: contract only, not a real runtime)
def _http(handler):
    return HttpRuntime("http://rt", "tok", transport=httpx.MockTransport(handler))


def test_http_runtime_contract_and_failure_mapping():
    calls = []

    def ok(req):
        calls.append((req.method, req.url.path, req.headers.get("Idempotency-Key"), req.headers["Authorization"]))
        if req.method == "POST" and req.url.path == "/deployments":
            return httpx.Response(201, json={"ref": "r-1"})
        if req.method == "GET" and req.url.path == "/deployments/tok1":
            return httpx.Response(200, json={"ref": "r-1"})
        if req.method == "GET" and req.url.path == "/deployments/none":
            return httpx.Response(404)
        if req.url.path == "/refs/r-1":
            return httpx.Response(200, json={"state": "running"})
        if req.url.path == "/artifacts/verify":
            return httpx.Response(200, json={"match": False})
        return httpx.Response(204)

    rt = _http(ok)
    kw = dict(model_id="m", version="1", environment="staging", artifact_uri="s3://a", attempt=1,
              simulate_failure="none")
    assert rt.deploy(idempotency_token="tok1", **kw) == "r-1"
    assert calls[0] == ("POST", "/deployments", "tok1", "Bearer tok")
    assert rt.lookup("tok1") == "r-1" and rt.lookup("none") is None
    assert rt.status("r-1") == "running" and rt.verify_artifact("s3://a", "sha256:" + "0" * 64) is False
    rt.undeploy("tok1")
    for status, reason in ((500, "runtime_unavailable"), (400, "runtime_rejected")):
        with pytest.raises(RuntimeFailure) as e:
            _http(lambda r, s=status: httpx.Response(s)).deploy(idempotency_token="t", **kw)
        assert e.value.reason == reason

    def boom(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(RuntimeFailure) as e:
        _http(boom).deploy(idempotency_token="t", **kw)
    assert e.value.reason == "runtime_timeout"

    def down(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(RuntimeFailure) as e:
        _http(down).deploy(idempotency_token="t", **kw)
    assert e.value.reason == "runtime_unavailable"


def test_runtime_factory_selects_adapter_by_configuration(tmp_path):
    assert isinstance(build_runtime(Settings(runtime_adapter="simulated")), SimulatedRuntime)
    assert isinstance(build_runtime(Settings(runtime_adapter="http", runtime_http_url="http://rt")), HttpRuntime)
    with pytest.raises(RuntimeError):
        build_runtime(Settings(runtime_adapter="http"))
    with pytest.raises(RuntimeError):
        build_runtime(Settings(runtime_adapter="kserve"))


# ---- F6 packaging is not empty
def test_frontend_nginx_template_is_real():
    body = (ROOT / "frontend" / "nginx.conf.template").read_text()
    assert "listen 8080" in body and "${API_UPSTREAM}" in body and "try_files $uri $uri/ /index.html" in body
    assert "Content-Security-Policy" in body
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text()
    assert "nginx.conf.template" in dockerfile and "nginx-unprivileged" in dockerfile


# ---- F10 the request correlation id follows the deployment into the worker
def test_request_correlation_id_reaches_worker_events(tmp_path):
    app = make_client(tmp_path)
    with TestClient(app) as c:
        c.post("/models", json={"name": "Cid Model", "owner": "team", "framework": "sk"})
        c.post("/models/cid-model/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"})
        approve(c, "cid-model", "1.0.0")
        d = c.post("/deployments", json={"model_id": "cid-model", "version": "1.0.0", "environment": "staging"},
                   headers={"X-Correlation-ID": "req-abc-123"}).json()
        app.state.worker.run_pending()
        cids = {e["correlation_id"] for e in c.get(f"/deployments/{d['id']}/events").json()}
        assert "req-abc-123" in cids and "req-abc-123.w" in cids
