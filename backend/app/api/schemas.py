import json
from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domain.enums import Environment, SimulatedFailure

T = TypeVar("T")
Tag = Annotated[str, StringConstraints(min_length=1, max_length=50)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
    correlation_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{1,98}$")
    name: str = Field(min_length=2, max_length=200)
    owner: str = Field(min_length=2, max_length=200)
    framework: str = Field(min_length=2, max_length=50)
    description: str | None = Field(None, max_length=2000)
    tags: list[Tag] = Field(default_factory=list, max_length=20)


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(max_length=50, examples=["1.0.0"])
    algorithm: str | None = Field(None, max_length=100)
    framework: str | None = Field(None, max_length=50)
    artifact_uri: str = Field(max_length=500, examples=["s3://models/pump/1.0.0"])
    training_data_ref: str | None = Field(None, max_length=500)
    artifact_checksum: str | None = Field(None, max_length=80, examples=["sha256:" + "0" * 64])
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _metadata_small(cls, v: dict) -> dict:
        if len(json.dumps(v)) > 10_000:
            raise ValueError("metadata must be at most 10 KB of JSON")
        return v


class VersionOut(ORM):
    id: int
    model_id: str
    version: str
    framework: str | None
    algorithm: str | None
    artifact_uri: str
    training_data_ref: str | None
    artifact_checksum: str | None = None
    tags: list[str]
    metadata: dict[str, Any] = Field(validation_alias="extra")
    stage: str
    approval_status: str
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TransitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(validate|approve|reject|archive|withdraw)$")
    comment: str | None = Field(None, max_length=500)


class ModelOut(ORM):
    id: str
    name: str
    owner: str
    framework: str
    description: str | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    version_count: int = 0
    latest_version: str | None = None
    production_version: str | None = None


class ModelDetailOut(ModelOut):
    versions: list[VersionOut] = []


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(max_length=100)
    version: str = Field(max_length=50)
    environment: Environment
    simulate_failure: SimulatedFailure = SimulatedFailure.NONE


class DeploymentOut(ORM):
    id: str
    model_id: str
    version: str
    environment: str
    status: str
    attempt: int
    requested_by: str
    failure_class: str | None
    failure_reason: str | None
    external_ref: str | None
    rollback_of: str | None
    previous_version: str | None
    simulate_failure: str
    retryable: bool = False
    rollbackable: bool = False
    created_at: datetime
    updated_at: datetime


class EventOut(ORM):
    id: int
    deployment_id: str
    model_id: str
    version: str
    environment: str
    status: str
    event: str
    detail: dict[str, Any]
    correlation_id: str | None
    timestamp: datetime


class MetricPointOut(ORM):
    timestamp: datetime
    version: str
    environment: str
    latency_ms: float
    throughput_rpm: float
    error_rate: float
    quality_score: float
    drift_score: float
    availability: float


class MetricsOut(BaseModel):
    model_id: str
    summary: dict[str, Any]
    by_version: dict[str, dict[str, Any]]
    points: list[MetricPointOut]


class AuditOut(ORM):
    id: int
    actor: str
    role: str
    action: str
    entity_type: str
    entity_id: str
    detail: dict[str, Any]
    correlation_id: str | None
    timestamp: datetime
