# ADR-003: Rollback is a new, governed deployment

## Status
Accepted

## Context
Rollback happens during incidents, when mistakes are most likely. It must be safe, auditable and idempotent, and must not bypass controls.

## Decision
Record `previous_version` when a production deployment succeeds. A rollback request creates a new deployment (`rollback_of = original`) to that version, executed by the same worker pipeline; on success the original becomes `ROLLED_BACK` and version stages swap. Guard rails: live deployment only, no rollback of rollbacks, target must be approved, repeat requests return the same rollback.

## Alternatives Considered
- Flip a pointer / mutate the original row: loses history, bypasses validation.
- Redeploy "any older version" manually: no safety rails, easy to pick a revoked version.

## Consequences
### Positive
Full history, same failure handling/retry as deploys, rollback itself can fail visibly.
### Negative
Rollback is asynchronous (seconds); cannot roll back the very first deployment.

## Follow-up Actions
Automatic rollback on SLO breach (canary analysis); second approver for production rollback outside incidents.
