from tests.conftest import approve, hdr


def test_health_ready_and_correlation_id(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["database"] == "ok"
    r = client.get("/health", headers={"X-Correlation-ID": "abc123"})
    assert r.headers["X-Correlation-ID"] == "abc123"


def test_create_and_get_model(client):
    r = client.post("/models", json={"name": "Valve Health", "owner": "Team", "framework": "xgboost", "tags": ["a"]})
    assert r.status_code == 201
    assert r.json()["id"] == "valve-health"
    assert client.get("/models/valve-health").json()["versions"] == []


def test_duplicate_model_conflict(client):
    body = {"name": "Dup Model", "owner": "Team", "framework": "xgboost"}
    assert client.post("/models", json=body).status_code == 201
    r = client.post("/models", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_EXISTS"


def test_validation_error_envelope(client):
    r = client.post("/models", json={"name": "x"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["correlation_id"] and err["details"]


def test_unknown_model_404(client):
    r = client.get("/models/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "MODEL_NOT_FOUND"


def test_version_validation_and_duplicates(client, seeded_model):
    bad = client.post(f"/models/{seeded_model}/versions", json={"version": "v1", "artifact_uri": "s3://x/y"})
    assert bad.status_code == 422
    bad = client.post(f"/models/{seeded_model}/versions", json={"version": "3.0.0", "artifact_uri": "ftp://x"})
    assert bad.status_code == 422
    dup = client.post(f"/models/{seeded_model}/versions", json={"version": "1.0.0", "artifact_uri": "s3://x/y"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "VERSION_EXISTS"


def test_lifecycle_and_approval_metadata(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    v = client.get(f"/models/{seeded_model}/versions/1.0.0").json()
    assert v["stage"] == "APPROVED" and v["approval_status"] == "APPROVED" and v["approved_by"]
    r = client.post(f"/models/{seeded_model}/versions/1.0.0/transitions", json={"action": "approve"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_TRANSITION"


def test_search_and_filters(client, seeded_model):
    client.post("/models", json={"name": "Other", "owner": "Zed", "framework": "pytorch"})
    assert client.get("/models", params={"q": "pump"}).json()["total"] == 1
    assert client.get("/models", params={"framework": "pytorch"}).json()["items"][0]["id"] == "other"
    assert client.get("/models", params={"stage": "DRAFT"}).json()["total"] == 1
    m = client.get("/models").json()["items"][0]
    assert "version_count" in m


def test_rbac(client, seeded_model):
    r = client.post(f"/models/{seeded_model}/versions/1.0.0/transitions", json={"action": "validate"},
                    headers=hdr("viewer"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    client.post(f"/models/{seeded_model}/versions/1.0.0/transitions", json={"action": "validate"})
    r = client.post(f"/models/{seeded_model}/versions/1.0.0/transitions", json={"action": "approve"},
                    headers=hdr("engineer"))
    assert r.status_code == 403  # engineers cannot approve (separation of duties)
    assert client.get("/audit", headers=hdr("engineer")).status_code == 403
    assert client.get("/audit", headers=hdr("bogus")).status_code == 422


def test_audit_trail_records_approval(client, seeded_model):
    approve(client, seeded_model, "1.0.0")
    actions = [a["action"] for a in client.get("/audit", headers=hdr("approver")).json()]
    assert "version.approve" in actions and "model.created" in actions
