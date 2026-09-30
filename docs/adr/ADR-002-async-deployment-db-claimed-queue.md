# ADR-002: Asynchronous deployments with a DB-claimed work queue

## Status
Accepted

## Context
Deployments call external runtimes that are slow and fail. Holding an HTTP request open couples API availability to the runtime. A broker adds infrastructure and a second source of truth that can disagree with the DB.

## Decision
`POST /deployments` validates synchronously, persists `REQUESTED`, returns `202`. A worker claims rows with an atomic `UPDATE ... WHERE status='REQUESTED'`, re-validates, calls the runtime with an idempotency token, and finalises in one transaction. A reconciler heals rows stuck in `DEPLOYING`.

## Alternatives Considered
- Synchronous deploy in the request: simple but fragile, timeouts leave unknown state.
- Celery/RabbitMQ now: scalable, but premature; dual-write problem between DB and broker.
- Kubernetes Job per deployment: strong isolation, slower start, more moving parts.

## Consequences
### Positive
Fast API, honest `REQUESTED/VALIDATING/DEPLOYING` states for the UI, crash-safe, horizontally scalable (claim is exclusive), deterministic tests (`run_pending()`).
### Negative
Polling latency (<= poll interval), DB load from polling at high scale, eventual consistency for clients (they poll).

## Follow-up Actions
`SELECT ... FOR UPDATE SKIP LOCKED` claim on Postgres, LISTEN/NOTIFY to cut latency, transactional outbox + broker when throughput demands.
