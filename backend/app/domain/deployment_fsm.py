"""Deployment state machine + failure classification (pure)."""
from app.core.errors import ConflictError
from app.domain.enums import DeploymentStatus as S
from app.domain.enums import FailureClass

TRANSITIONS: dict[S, set[S]] = {
    S.REQUESTED: {S.VALIDATING, S.FAILED},
    S.VALIDATING: {S.DEPLOYING, S.FAILED},
    S.DEPLOYING: {S.SUCCEEDED, S.FAILED},
    S.SUCCEEDED: {S.ROLLED_BACK},
    S.FAILED: {S.REQUESTED},  # retry only
    S.ROLLED_BACK: set(),
}
ACTIVE = {S.REQUESTED, S.VALIDATING, S.DEPLOYING}

# failure reason code -> class
FAILURE_CLASSES = {
    "runtime_timeout": FailureClass.TRANSIENT,
    "runtime_unavailable": FailureClass.TRANSIENT,
    "approval_validation_failed": FailureClass.PERMANENT,
    "artifact_validation_failed": FailureClass.PERMANENT,
    "stage_validation_failed": FailureClass.PERMANENT,
    "validation_requeue_limit": FailureClass.RECONCILIATION,
    "approval_revoked_during_deploy": FailureClass.PERMANENT,
    "artifact_checksum_missing": FailureClass.PERMANENT,
    "artifact_checksum_mismatch": FailureClass.PERMANENT,
    "runtime_rejected": FailureClass.PERMANENT,
    "state_reconciliation_required": FailureClass.RECONCILIATION,
}


def classify(reason: str) -> FailureClass:
    return FAILURE_CLASSES.get(reason, FailureClass.PERMANENT)


def assert_transition(current: S, target: S) -> None:
    if target not in TRANSITIONS[current]:
        raise ConflictError(
            f"Illegal deployment transition {current.value} -> {target.value}",
            details={"from": current.value, "to": target.value},
            code="INVALID_TRANSITION",
        )


def assert_retryable(status: S, failure_class: str | None, attempt: int, max_attempts: int) -> None:
    if status != S.FAILED:
        raise ConflictError("Only FAILED deployments can be retried", code="NOT_RETRYABLE",
                            details={"status": status.value})
    if failure_class not in (FailureClass.TRANSIENT.value, FailureClass.RECONCILIATION.value):
        raise ConflictError(
            f"Failure class {failure_class} is not retryable; fix the cause and create a new deployment",
            code="NOT_RETRYABLE", details={"failure_class": failure_class},
        )
    if attempt >= max_attempts:
        raise ConflictError(f"Maximum attempts ({max_attempts}) reached", code="RETRY_LIMIT_REACHED")
