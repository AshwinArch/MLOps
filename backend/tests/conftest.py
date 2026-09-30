import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import metrics
from app.core.config import Settings
from app.main import create_app
from app.workers.runtime import SimulatedRuntime


def make_db_url(tmp_path) -> str:
    """SQLite by default; set TEST_DATABASE_URL=postgresql://... to run the whole suite against PostgreSQL."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return f"sqlite:///{tmp_path / 'test.db'}"
    from app.db.base import Base
    from app.db.session import init_db, make_engine
    engine = make_engine(url)
    with engine.begin() as conn:  # clean slate per test
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    init_db(engine)
    assert Base.metadata.tables
    engine.dispose()
    return url


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=make_db_url(tmp_path), worker_enabled=False, seed_on_startup=False,
                    runtime_step_seconds=0, log_level="WARNING", default_role="admin",
                    enable_failure_simulation=True, require_four_eyes=False,
                    require_artifact_checksum=False)


@pytest.fixture
def client(settings):
    metrics.reset()
    app = create_app(settings, runtime=SimulatedRuntime(0))
    with TestClient(app) as c:
        c.app_state = app.state
        yield c


@pytest.fixture
def run_worker(client):
    """Deterministically drive the async worker (no sleeping / polling in tests)."""
    return lambda: client.app_state.worker.run_pending()


def hdr(role="admin", user="tester"):
    return {"X-Role": role, "X-User": user}


@pytest.fixture
def seeded_model(client):
    """Model with two versions; 1.0.0 approved."""
    body = {"name": "Pump Model", "owner": "Team A", "framework": "sklearn"}
    assert client.post("/models", json=body).status_code == 201
    for v in ("1.0.0", "2.0.0"):
        r = client.post("/models/pump-model/versions", json={"version": v, "artifact_uri": f"s3://models/pump/{v}"})
        assert r.status_code == 201, r.text
    return "pump-model"


def approve(client, model_id, version):
    for action in ("validate", "approve"):
        r = client.post(f"/models/{model_id}/versions/{version}/transitions", json={"action": action})
        assert r.status_code == 200, r.text


def deploy(client, model_id, version, env, run_worker=None, key=None, **extra):
    headers = {"Idempotency-Key": key} if key else {}
    r = client.post("/deployments", json={"model_id": model_id, "version": version, "environment": env, **extra},
                    headers=headers)
    if run_worker and r.status_code in (200, 202):
        run_worker()
    return r
