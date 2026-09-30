"""Persistence model. Ownership: registry tables, deployment tables, observability tables."""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime, utcnow

BigPK = BigInteger().with_variant(Integer(), "sqlite")  # 64-bit keys on high-volume tables (review F9)
STAGES_SQL = "stage IN ('DRAFT','VALIDATED','APPROVED','STAGING','PRODUCTION','ARCHIVED','WITHDRAWN')"
PRODUCTION_SQL = "stage = 'PRODUCTION'"
ACTIVE_SQL = "status IN ('REQUESTED','VALIDATING','DEPLOYING')"


class Model(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str] = mapped_column(String(200))
    framework: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="model", order_by="ModelVersion.created_at", cascade="all, delete-orphan"
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_id", "version"),
        # The database, not only application code, guards governance: a version can never be live (or in staging)
        # without approval, stages/approvals are closed sets, and a model has at most one PRODUCTION version.
        CheckConstraint(STAGES_SQL, name="ck_version_stage"),
        CheckConstraint("approval_status IN ('PENDING','APPROVED','REJECTED')", name="ck_version_approval"),
        CheckConstraint("stage NOT IN ('STAGING','PRODUCTION') OR approval_status = 'APPROVED'",
                        name="ck_version_live_requires_approval"),
        Index("uq_one_production_version", "model_id", unique=True,
              sqlite_where=text(PRODUCTION_SQL), postgresql_where=text(PRODUCTION_SQL)),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id"), index=True)
    version: Mapped[str] = mapped_column(String(50))
    framework: Mapped[str | None] = mapped_column(String(50), nullable=True)
    algorithm: Mapped[str | None] = mapped_column(String(100), nullable=True)
    artifact_uri: Mapped[str] = mapped_column(String(500))
    training_data_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_checksum: Mapped[str | None] = mapped_column(String(80), nullable=True)  # "sha256:<hex>"
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    stage: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    approval_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic locking

    model: Mapped[Model] = relationship(back_populates="versions")
    __mapper_args__ = {"version_id_col": row_version}


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        # Concurrency guard: at most ONE active deployment per (model, environment), enforced by the DB.
        Index("uq_active_deployment", "model_id", "environment", unique=True,
              sqlite_where=text(ACTIVE_SQL), postgresql_where=text(ACTIVE_SQL)),
        Index("ix_deployments_model_env", "model_id", "environment"),
        CheckConstraint("status IN ('REQUESTED','VALIDATING','DEPLOYING','SUCCEEDED','FAILED','ROLLED_BACK')",
                        name="ck_deployment_status"),
        CheckConstraint("environment IN ('dev','staging','production')", name="ck_deployment_environment"),
        ForeignKeyConstraint(["model_id", "version"], ["model_versions.model_id", "model_versions.version"],
                             name="fk_deployment_version", ondelete="RESTRICT"),
        # Idempotency keys are scoped to the caller, so one user can never replay (or read) another user's request.
        Index("uq_idempotency_scope", "requested_by", "idempotency_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id"), index=True)
    version: Mapped[str] = mapped_column(String(50))
    environment: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(100), default="system")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    simulate_failure: Mapped[str] = mapped_column(String(20), default="none")
    failure_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rollback_of: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    request_correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)

    events: Mapped[list["DeploymentEvent"]] = relationship(
        back_populates="deployment", order_by="DeploymentEvent.id", cascade="all, delete-orphan"
    )
    __mapper_args__ = {"version_id_col": row_version}


class EnvironmentState(Base):
    """Single source of truth for 'what is live' per (model, environment).

    Updated in the SAME transaction as the deployment result. Avoids inferring liveness from timestamps
    (clock skew across workers) and lets seeded/legacy live versions exist without a deployment row.
    """

    __tablename__ = "environment_state"
    __table_args__ = (
        CheckConstraint("environment IN ('dev','staging','production')", name="ck_envstate_environment"),
        ForeignKeyConstraint(["model_id", "version"], ["model_versions.model_id", "model_versions.version"],
                             name="fk_envstate_version", ondelete="RESTRICT"),
    )

    model_id: Mapped[str] = mapped_column(ForeignKey("models.id"), primary_key=True)
    environment: Mapped[str] = mapped_column(String(20), primary_key=True)
    version: Mapped[str] = mapped_column(String(50))
    live_deployment_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class DeploymentEvent(Base):
    __tablename__ = "deployment_events"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(50))
    environment: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    event: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    deployment: Mapped[Deployment] = relationship(back_populates="events")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(150), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class MetricPoint(Base):
    """High-volume table: partition by time (and hash model_id) in Postgres; see docs/architecture.md."""

    __tablename__ = "metric_points"
    __table_args__ = (
        UniqueConstraint("model_id", "version", "environment", "timestamp", name="uq_metric_point"),
        Index("ix_metric_model_ts", "model_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(50))
    environment: Mapped[str] = mapped_column(String(20))
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime)
    latency_ms: Mapped[float] = mapped_column(Float)
    throughput_rpm: Mapped[float] = mapped_column(Float)
    error_rate: Mapped[float] = mapped_column(Float)
    quality_score: Mapped[float] = mapped_column(Float)
    drift_score: Mapped[float] = mapped_column(Float)
    availability: Mapped[float] = mapped_column(Float)
