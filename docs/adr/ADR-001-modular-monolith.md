# ADR-001: Modular monolith with enforced boundaries

## Status
Accepted

## Context
Approval, stage changes, deployment status, events and audit must commit atomically; the team is small; there is no independent scaling need for registry vs deployment yet. Delivery time-box is 12-16 hours and a runnable `docker compose up` is required.

## Decision
One deployable FastAPI application organised as modules (`domain`, `services`, `workers`, `api`) with a one-way dependency rule. The worker is the same codebase/image, runnable in-process (assignment) or as its own Deployment.

## Alternatives Considered
- Microservices (registry, deployment, monitoring): independent scaling, but distributed transactions/sagas for every promotion, more infra, slower delivery.
- Serverless functions per endpoint: poor fit for long-running orchestration and DB pooling.

## Consequences
### Positive
Atomic governance transitions, simple local run, fast tests, easy refactor. The boundaries are the future service seams.
### Negative
Single release unit; a bad module can affect all; needs discipline to keep boundaries (enforced by import-linter contracts in CI and the review checklist).

## Follow-up Actions
Split monitoring ingestion first when metric volume justifies it.
