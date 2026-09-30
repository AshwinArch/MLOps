"""Application settings (12-factor: everything comes from environment variables)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./mlops.db"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:4200"
    default_role: str = "viewer"  # fail closed: no X-Role header => read-only. Dev sets DEFAULT_ROLE=admin
    require_staging_before_production: bool = True
    max_deployment_attempts: int = 3
    require_four_eyes: bool = True  # the creator of a version may not approve it
    runtime_timeout_seconds: float = 60.0  # per runtime.deploy call
    worker_concurrency: int = 4  # deployments processed in parallel per worker process
    stale_metrics_hours: int = 48  # no datapoint for this long => monitoring status STALE
    artifact_uri_prefixes: str = ""  # comma list (e.g. "s3://acme-models/,gs://acme-ml/"); empty = any valid scheme
    validation_required_fields: str = ""  # comma list required before `validate`, e.g. "training_data_ref,algorithm"
    require_artifact_checksum: bool = True   # PRODUCTION deploys need a checksum the runtime can verify
    worker_enabled: bool = True
    environment: str = "development"  # "production" turns on the startup guard (validate_production)
    # Authentication. "header" = dev/demo stand-in (forgeable); "jwt" = validate a bearer token on every route.
    auth_mode: str = "header"
    jwt_secret: str = ""  # HS256 shared secret (or use jwt_jwks_url for RS256/ES256)
    jwt_jwks_url: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = ""
    jwt_role_claim: str = "role"
    metrics_token: str = ""  # when set, /metrics requires "Authorization: Bearer <token>"
    # Runtime adapter: "simulated" (tests/demo) or "http" (generic REST contract, see workers/http_runtime.py)
    runtime_adapter: str = "simulated"
    runtime_http_url: str = ""
    runtime_http_token: str = ""
    enable_failure_simulation: bool = False  # demo/test only: allows simulate_failure != none
    auto_create_schema: bool = True  # dev/tests; set false where Alembic owns the schema
    worker_metrics_port: int = 9100  # standalone worker exposes /metrics here (0 disables)
    worker_poll_seconds: float = 0.5
    runtime_step_seconds: float = 0.4  # simulated runtime latency per phase
    stuck_deployment_seconds: int = 300  # reconciler threshold for DEPLOYING rows
    seed_on_startup: bool = False  # dev/demo only (SEED_ON_STARTUP=true)
    seed_dir: str = str(Path(__file__).resolve().parents[3] / "data")


def validate_production(s: Settings) -> None:
    """Refuse to start in production with demo-grade settings (review F22)."""
    if s.environment.lower() != "production":
        return
    problems = []
    if s.auth_mode != "jwt" or not (s.jwt_secret or s.jwt_jwks_url):
        problems.append("AUTH_MODE=jwt with JWT_SECRET or JWT_JWKS_URL is required")
    if not (s.jwt_issuer and s.jwt_audience):
        problems.append("JWT_ISSUER and JWT_AUDIENCE are required")
    if s.default_role != "viewer":
        problems.append("DEFAULT_ROLE must be viewer")
    if s.seed_on_startup or s.enable_failure_simulation or s.auto_create_schema:
        problems.append("SEED_ON_STARTUP, ENABLE_FAILURE_SIMULATION and AUTO_CREATE_SCHEMA must be false")
    if not (s.require_four_eyes and s.require_artifact_checksum):
        problems.append("REQUIRE_FOUR_EYES and REQUIRE_ARTIFACT_CHECKSUM must be true")
    if s.runtime_adapter == "simulated":
        problems.append("RUNTIME_ADAPTER must be a real adapter, not 'simulated'")
    if not s.metrics_token:
        problems.append("METRICS_TOKEN is required")
    if s.database_url.startswith("sqlite"):
        problems.append("DATABASE_URL must be PostgreSQL")
    if s.stuck_deployment_seconds <= 2 * s.runtime_timeout_seconds:
        problems.append("STUCK_DEPLOYMENT_SECONDS must exceed twice RUNTIME_TIMEOUT_SECONDS")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
