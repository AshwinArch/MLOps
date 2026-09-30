import pytest

from app.core.errors import ConflictError
from app.domain import deployment_fsm as fsm
from app.domain.enums import DeploymentStatus as S


def test_legal_path():
    for a, b in [(S.REQUESTED, S.VALIDATING), (S.VALIDATING, S.DEPLOYING), (S.DEPLOYING, S.SUCCEEDED),
                 (S.SUCCEEDED, S.ROLLED_BACK), (S.FAILED, S.REQUESTED)]:
        fsm.assert_transition(a, b)


@pytest.mark.parametrize("a,b", [(S.REQUESTED, S.SUCCEEDED), (S.ROLLED_BACK, S.REQUESTED),
                                 (S.FAILED, S.SUCCEEDED), (S.SUCCEEDED, S.FAILED)])
def test_illegal(a, b):
    with pytest.raises(ConflictError):
        fsm.assert_transition(a, b)


def test_failure_classification():
    assert fsm.classify("runtime_timeout").value == "TRANSIENT"
    assert fsm.classify("approval_validation_failed").value == "PERMANENT"
    assert fsm.classify("state_reconciliation_required").value == "RECONCILIATION"
    assert fsm.classify("something_unknown").value == "PERMANENT"  # fail safe: never auto-retry the unknown


def test_retry_rules():
    fsm.assert_retryable(S.FAILED, "TRANSIENT", 1, 3)
    for status, cls, attempt in [(S.SUCCEEDED, None, 1), (S.FAILED, "PERMANENT", 1), (S.FAILED, "TRANSIENT", 3)]:
        with pytest.raises(ConflictError):
            fsm.assert_retryable(status, cls, attempt, 3)
