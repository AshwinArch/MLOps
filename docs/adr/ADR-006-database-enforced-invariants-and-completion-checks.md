# ADR-006: Database-enforced invariants and completion-time governance

## Context
Governance rules lived only in application code. A review reproduced a revoked version completing into production because approval was checked once, at the start of the worker run, and the database accepted any string for stage and approval.

## Decision
1. The finalising transaction locks the version row and re-validates governance; on failure it compensates (`undeploy`) and fails the deployment permanently. Transitions that would revoke a version are refused while it is being deployed.
2. The database enforces what it can: CHECK constraints for closed sets, `stage IN (STAGING, PRODUCTION) => approval = APPROVED`, one PRODUCTION version per model (partial unique index), composite FKs to versions, 64-bit keys on high-volume tables.
3. Runtime timeouts are treated as unknown outcomes: a sweep and a drift check reconcile them against the runtime.

## Alternatives
- Application checks only: simplest, but any interleaving, script or second service can corrupt state.
- Serializable isolation everywhere: stronger but slower and still needs retries; row locks on the one contended row are cheaper.
- Cancel the external call on revocation: not possible across a network boundary; compensation is the realistic tool.

## Consequences
Positive: invariants hold even if code is wrong; the approval-revoked race closes. Negative: stricter writes (seed and tests had to respect the constraints), a compensating call can itself fail (logged, audited, left for the drift check).

## Risks and revisit trigger
Fencing generations per (model, environment) are not built, so two overlapping runtime calls are reconciled after the fact rather than prevented. Revisit when a real runtime is integrated.
