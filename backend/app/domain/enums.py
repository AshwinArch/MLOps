from enum import StrEnum


class Stage(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"
    ARCHIVED = "ARCHIVED"
    WITHDRAWN = "WITHDRAWN"  # permanently unusable: not deployable, not a rollback target


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DeploymentStatus(StrEnum):
    REQUESTED = "REQUESTED"
    VALIDATING = "VALIDATING"
    DEPLOYING = "DEPLOYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


class FailureClass(StrEnum):
    """Drives retry policy and alerting."""

    TRANSIENT = "TRANSIENT"  # retryable (runtime timeout, 5xx)
    PERMANENT = "PERMANENT"  # not retryable (policy / validation)
    RECONCILIATION = "RECONCILIATION"  # external result unknown, needs reconcile


class Role(StrEnum):
    VIEWER = "viewer"
    ENGINEER = "engineer"
    APPROVER = "approver"
    ADMIN = "admin"


class SimulatedFailure(StrEnum):
    NONE = "none"
    TRANSIENT = "transient"  # fails on first attempt, succeeds on retry
    PERMANENT = "permanent"  # always fails
