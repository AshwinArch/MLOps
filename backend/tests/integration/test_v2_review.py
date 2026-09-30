"""Review v2 regressions: identity normalisation, timeout adoption, checksum enforcement, readiness."""
from fastapi.testclient import TestClient

from app.workers.runtime import SimulatedRuntime
from tests.conftest import approve, hdr
from tests.integration.test_governance_and_state import SlowRuntime, make_client

SHA = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


def _version(c, name, checksum=None, uri="s3://m/x"):
    c.post("/models", json={"name": name, "owner": "team", "framework": "sk"})
    body = {"version": "1.0.0", "artifact_uri": uri}
    if checksum:
        body["artifact_checksum"] = checksum
    assert c.post(f"/models/{name}/versions", json=body).status_code == 201


def test_four_eyes_cannot_be_bypassed_by_header_case(tmp_path):
    with TestClient(make_client(tmp_path, require_four_eyes=True)) as c:
        c.post("/models", json={"name": "Case Model", "owner": "team", "framework": "sk"},
               headers=hdr("admin", "alice"))
        c.post("/models/case-model/versions", json={"version": "1.0.0", "artifact_uri": "s3://m/x"},
               headers=hdr("admin", "alice"))
        c.post("/models/case-model/versions/1.0.0/transitions", json={"action": "validate"},
               headers=hdr("admin", "alice"))
        for user in ("ALICE", "  Alice ", "aLiCe"):
            r = c.post("/models/case-model/versions/1.0.0/transitions", json={"action": "approve"},
                       headers=hdr("approver", user))
            assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPROVAL"


def test_timeout_then_remote_completion_is_adopted_not_redeployed(tmp_path):
    rt = SlowRuntime({"adopt-model": 0.5})
    app = make_client(tmp_path, runtime_timeout_seconds=0.1, runtime=rt)
    with TestClient(app) as c:
        _version(c, "adopt-model")
        approve(c, "adopt-model", "1.0.0")
        d = c.post("/deployments", json={"model_id": "adopt-model", "version": "1.0.0",
                                         "environment": "staging"}).json()
        app.state.worker.run_pending()
        assert c.get(f"/deployments/{d['id']}").json()["failure_reason"] == "runtime_timeout"
        import time
        time.sleep(0.6)  # the abandoned remote call completes and the runtime now holds the token
        ref = rt.lookup(d["id"])
        assert ref
        c.post(f"/deployments/{d['id']}/retry")
        app.state.worker.run_pending()
        got = c.get(f"/deployments/{d['id']}").json()
        assert got["status"] == "SUCCEEDED" and got["external_ref"] == ref


def test_production_requires_checksum(tmp_path):
    app = make_client(tmp_path, require_artifact_checksum=True)
    with TestClient(app) as c:
        _version(c, "nosum-model")
        approve(c, "nosum-model", "1.0.0")
        for env in ("staging", "production"):
            d = c.post("/deployments", json={"model_id": "nosum-model", "version": "1.0.0", "environment": env})
            app.state.worker.run_pending()
            got = c.get(f"/deployments/{d.json()['id']}").json()
            if env == "staging":
                assert got["status"] == "SUCCEEDED"
        assert got["failure_reason"] == "artifact_checksum_missing" and got["failure_class"] == "PERMANENT"


def test_checksum_mismatch_is_rejected_by_runtime_verification(tmp_path):
    rt = SimulatedRuntime(0)
    rt.register_artifact("s3://m/real", SHA)
    app = make_client(tmp_path, runtime=rt)
    with TestClient(app) as c:
        _version(c, "bad-sum", checksum=OTHER, uri="s3://m/real")
        approve(c, "bad-sum", "1.0.0")
        d = c.post("/deployments", json={"model_id": "bad-sum", "version": "1.0.0", "environment": "staging"}).json()
        app.state.worker.run_pending()
        got = c.get(f"/deployments/{d['id']}").json()
        assert got["status"] == "FAILED" and got["failure_reason"] == "artifact_checksum_mismatch"
        assert got["retryable"] is False


def test_matching_checksum_deploys(tmp_path):
    rt = SimulatedRuntime(0)
    rt.register_artifact("s3://m/real", SHA)
    app = make_client(tmp_path, runtime=rt)
    with TestClient(app) as c:
        _version(c, "good-sum", checksum=SHA, uri="s3://m/real")
        approve(c, "good-sum", "1.0.0")
        d = c.post("/deployments", json={"model_id": "good-sum", "version": "1.0.0", "environment": "staging"}).json()
        app.state.worker.run_pending()
        assert c.get(f"/deployments/{d['id']}").json()["status"] == "SUCCEEDED"


def test_ready_uses_public_worker_liveness(tmp_path):
    app = make_client(tmp_path, worker_enabled=True)
    with TestClient(app) as c:
        assert app.state.worker.is_alive() and c.get("/ready").json()["worker"] == "ok"
