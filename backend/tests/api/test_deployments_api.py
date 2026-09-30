from tests.conftest import approve, deploy, hdr


def test_unapproved_version_cannot_go_to_production(client, seeded_model):
    r = deploy(client, seeded_model, "2.0.0", "production")
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_NOT_APPROVED"
    assert client.get("/deployments").json()["total"] == 0


def test_production_requires_staging_first(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    r = deploy(client, seeded_model, "1.0.0", "production")
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROMOTION_REQUIRES_STAGING"


def test_async_deploy_returns_202_then_succeeds(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    r = deploy(client, seeded_model, "1.0.0", "staging")
    assert r.status_code == 202 and r.json()["status"] == "REQUESTED"  # not blocked on the runtime
    dep_id = r.json()["id"]
    run_worker()
    d = client.get(f"/deployments/{dep_id}").json()
    assert d["status"] == "SUCCEEDED" and d["external_ref"]
    assert client.get(f"/models/{seeded_model}/versions/1.0.0").json()["stage"] == "STAGING"
    events = [e["event"] for e in client.get(f"/deployments/{dep_id}/events").json()]
    assert events == ["deployment_requested", "validation_started", "runtime_call_started", "deployment_completed"]


def test_idempotency_key_replay_and_reuse(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    a = deploy(client, seeded_model, "1.0.0", "staging", key="k1")
    b = deploy(client, seeded_model, "1.0.0", "staging", key="k1")
    assert a.status_code == 202 and b.status_code == 200
    assert a.json()["id"] == b.json()["id"] and b.headers["Idempotent-Replay"] == "true"
    c = deploy(client, seeded_model, "2.0.0", "staging", key="k1")
    assert c.status_code == 409 and c.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert client.get("/deployments").json()["total"] == 1


def test_duplicate_without_key_is_deduplicated(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    ids = {deploy(client, seeded_model, "1.0.0", "staging").json()["id"] for _ in range(5)}
    assert len(ids) == 1


def test_conflicting_active_deployment(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    approve(client, seeded_model, "2.0.0")
    assert deploy(client, seeded_model, "1.0.0", "staging").status_code == 202
    r = deploy(client, seeded_model, "2.0.0", "staging")
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEPLOYMENT_IN_PROGRESS"


def test_transient_failure_then_retry_succeeds(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging", run_worker, simulate_failure="transient").json()
    d = client.get(f"/deployments/{dep['id']}").json()
    assert d["status"] == "FAILED" and d["failure_reason"] == "runtime_timeout"
    assert d["failure_class"] == "TRANSIENT" and d["retryable"] is True
    r = client.post(f"/deployments/{dep['id']}/retry")
    assert r.status_code == 202 and r.json()["attempt"] == 2
    run_worker()
    assert client.get(f"/deployments/{dep['id']}").json()["status"] == "SUCCEEDED"


def test_permanent_failure_not_retryable(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging", run_worker, simulate_failure="permanent").json()
    d = client.get(f"/deployments/{dep['id']}").json()
    assert d["failure_class"] == "PERMANENT" and d["retryable"] is False
    r = client.post(f"/deployments/{dep['id']}/retry")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_RETRYABLE"


def test_retry_of_succeeded_rejected_and_unknown_404(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging", run_worker).json()
    assert client.post(f"/deployments/{dep['id']}/retry").status_code == 409
    assert client.post("/deployments/dep-nope/retry").status_code == 404


def test_revoked_approval_fails_validation_in_worker(client, seeded_model, run_worker):
    """Approval is re-checked by the worker: a version rejected after the request must not deploy."""
    approve(client, seeded_model, "1.0.0")
    dep = deploy(client, seeded_model, "1.0.0", "staging").json()
    client.post(f"/models/{seeded_model}/versions/1.0.0/transitions", json={"action": "reject"})
    run_worker()
    d = client.get(f"/deployments/{dep['id']}").json()
    assert d["status"] == "FAILED" and d["failure_reason"] == "approval_validation_failed"
    assert d["failure_class"] == "PERMANENT"


def test_production_deploy_needs_approver_role(client, seeded_model, run_worker):
    approve(client, seeded_model, "1.0.0")
    deploy(client, seeded_model, "1.0.0", "staging", run_worker)
    r = client.post("/deployments", json={"model_id": seeded_model, "version": "1.0.0", "environment": "production"},
                    headers=hdr("engineer"))
    assert r.status_code == 403


def test_unknown_version_and_bad_environment(client, seeded_model):
    assert deploy(client, seeded_model, "9.9.9", "staging").status_code == 404
    assert deploy(client, seeded_model, "1.0.0", "moon").status_code == 422
