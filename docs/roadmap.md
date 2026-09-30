# Roadmap

## Near term (next 1-2 quarters)
IdP integration test of the JWT mode (implemented, validated with locally signed tokens only); validate the HTTP runtime adapter against a real runtime (KServe/SageMaker shim) with per-environment fencing generations; two-phase runtime contract (submit/poll/cancel); Postgres `SKIP LOCKED` claim and separate worker Deployment (manifest included); metric ingestion API + rollups + retention; OpenTelemetry tracing; generated TypeScript client with contract tests; Playwright E2E in CI; Angular Material design-system pass; (import-linter boundaries are done).

## Mid term
Canary / shadow deployments with automatic rollback on SLO breach; model cards and lineage (training data, code, metrics); drift-triggered retraining hooks; transactional outbox -> event bus (Kafka) for integrations; approval workflows with multiple approvers and change windows.

## Future
Multi-tenancy with row-level security; multi-region active/passive; cost/GPU accounting per model; policy-as-code (OPA) for promotion rules; feature store and pipeline integration; ClickHouse/Timescale for monitoring at fleet scale.
