import pytest

from app.core.errors import ConflictError
from app.domain import lifecycle
from app.domain.enums import ApprovalStatus, Environment, Stage


def test_happy_path_validate_then_approve():
    stage, approval = lifecycle.apply_action("validate", Stage.DRAFT)
    assert (stage, approval) == (Stage.VALIDATED, None)
    stage, approval = lifecycle.apply_action("approve", stage)
    assert (stage, approval) == (Stage.APPROVED, ApprovalStatus.APPROVED)


@pytest.mark.parametrize("action,stage", [("approve", Stage.DRAFT), ("validate", Stage.APPROVED),
                                          ("approve", Stage.PRODUCTION), ("archive", Stage.PRODUCTION)])
def test_illegal_transitions_rejected(action, stage):
    with pytest.raises(ConflictError) as e:
        lifecycle.apply_action(action, stage)
    assert e.value.code == "INVALID_TRANSITION"


def test_unknown_action():
    with pytest.raises(ConflictError):
        lifecycle.apply_action("delete", Stage.DRAFT)


def test_unapproved_version_blocked_from_production():
    with pytest.raises(ConflictError) as e:
        lifecycle.assert_deployable(Environment.PRODUCTION, Stage.VALIDATED, ApprovalStatus.PENDING,
                                    model_id="m", version="1.0.0")
    assert e.value.code == "VERSION_NOT_APPROVED"


def test_dev_allows_validated_but_not_draft():
    lifecycle.assert_deployable(Environment.DEV, Stage.VALIDATED, ApprovalStatus.PENDING, model_id="m", version="1")
    with pytest.raises(ConflictError) as e:
        lifecycle.assert_deployable(Environment.DEV, Stage.DRAFT, ApprovalStatus.PENDING, model_id="m", version="1")
    assert e.value.code == "STAGE_NOT_DEPLOYABLE"


def test_archived_cannot_be_deployed_even_if_approved():
    with pytest.raises(ConflictError) as e:
        lifecycle.assert_deployable(Environment.PRODUCTION, Stage.ARCHIVED, ApprovalStatus.APPROVED,
                                    model_id="m", version="1")
    assert e.value.code == "STAGE_NOT_DEPLOYABLE"


def test_withdraw_rules():
    assert lifecycle.apply_action("withdraw", Stage.ARCHIVED) == (Stage.WITHDRAWN, ApprovalStatus.REJECTED)
    with pytest.raises(ConflictError):
        lifecycle.apply_action("withdraw", Stage.PRODUCTION)  # the live version cannot be withdrawn
    with pytest.raises(ConflictError):
        lifecycle.assert_deployable(Environment.STAGING, Stage.WITHDRAWN, ApprovalStatus.APPROVED,
                                    model_id="m", version="1")


def test_reject_allowed_from_staging():
    assert lifecycle.apply_action("reject", Stage.STAGING)[0] == Stage.DRAFT
