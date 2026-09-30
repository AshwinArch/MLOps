"""Pure domain rules for model-version lifecycle. No I/O, fully unit-testable."""
from app.core.errors import ConflictError
from app.domain.enums import ApprovalStatus, Environment, Stage

# action -> (allowed source stages, resulting stage)
ACTIONS: dict[str, tuple[set[Stage], Stage]] = {
    "validate": ({Stage.DRAFT}, Stage.VALIDATED),
    "approve": ({Stage.VALIDATED}, Stage.APPROVED),
    # approval can be revoked until the version is live in production
    "reject": ({Stage.DRAFT, Stage.VALIDATED, Stage.APPROVED, Stage.STAGING}, Stage.DRAFT),
    "archive": ({Stage.DRAFT, Stage.VALIDATED, Stage.APPROVED, Stage.STAGING}, Stage.ARCHIVED),
    # withdraw = "never use again" (e.g. a superseded version found faulty). Not allowed for the live version.
    "withdraw": (
        {Stage.DRAFT, Stage.VALIDATED, Stage.APPROVED, Stage.STAGING, Stage.ARCHIVED}, Stage.WITHDRAWN),
}

# Stages a version may be in to be deployed to an environment.
DEPLOYABLE_STAGES: dict[Environment, set[Stage]] = {
    Environment.DEV: {Stage.VALIDATED, Stage.APPROVED, Stage.STAGING, Stage.PRODUCTION},
    Environment.STAGING: {Stage.APPROVED, Stage.STAGING, Stage.PRODUCTION},
    Environment.PRODUCTION: {Stage.APPROVED, Stage.STAGING, Stage.PRODUCTION},
}

ACTION_ROLES = {"validate": "engineer", "approve": "approver", "reject": "approver", "archive": "approver",
                "withdraw": "approver"}


def apply_action(action: str, stage: Stage) -> tuple[Stage, ApprovalStatus | None]:
    """Return (new_stage, new_approval_status or None if unchanged) or raise ConflictError."""
    if action not in ACTIONS:
        raise ConflictError(f"Unknown lifecycle action '{action}'", code="UNKNOWN_ACTION")
    sources, target = ACTIONS[action]
    if stage not in sources:
        allowed = ", ".join(sorted(s.value for s in sources))
        raise ConflictError(
            f"Cannot {action} a version in stage {stage.value}; allowed from: {allowed}",
            details={"stage": stage.value, "action": action},
            code="INVALID_TRANSITION",
        )
    approval = {
        "approve": ApprovalStatus.APPROVED,
        "reject": ApprovalStatus.REJECTED,
        "withdraw": ApprovalStatus.REJECTED,
    }.get(action)
    return target, approval


def assert_deployable(
    env: Environment, stage: Stage, approval: ApprovalStatus, *, model_id: str, version: str
) -> None:
    """Governance gate evaluated synchronously at request time and again by the worker."""
    if env in (Environment.STAGING, Environment.PRODUCTION) and approval != ApprovalStatus.APPROVED:
        raise ConflictError(
            f"Version {version} of {model_id} is not approved and cannot be deployed to {env.value}",
            details={"approval_status": approval.value, "stage": stage.value},
            code="VERSION_NOT_APPROVED",
        )
    if stage not in DEPLOYABLE_STAGES[env]:
        raise ConflictError(
            f"Version in stage {stage.value} cannot be deployed to {env.value}",
            details={"stage": stage.value, "environment": env.value},
            code="STAGE_NOT_DEPLOYABLE",
        )


def stage_after_success(env: Environment, current: Stage) -> Stage:
    if env == Environment.PRODUCTION:
        return Stage.PRODUCTION
    if env == Environment.STAGING and current in (Stage.APPROVED,):
        return Stage.STAGING
    return current
