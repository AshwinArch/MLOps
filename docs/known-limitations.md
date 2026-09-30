# Known limitations

Deliberately not built (honest scope). Authentication: JWT mode exists but was only tested with locally signed tokens; the HTTP runtime adapter was only tested against a mock transport; the simulator is the default in dev.

Not built: plant/site dimension and multi-tenancy; real IdP integration and per-environment/per-model permissions (in the default header mode, `X-User`/`X-Role` are forgeable); fencing generations and cancel-on-timeout for runtime calls; worker lease/heartbeat, automatic retry with backoff and a dead-letter state; queue-depth and deployment-duration metrics; rollback pre-flight (artifact availability, scans, compatibility) and a SUPERSEDED stage distinct from ARCHIVED; approvals bound to artifact digest with expiry; immutable audit storage; idempotency-key TTL; OpenTelemetry tracing; generated TypeScript client; Playwright E2E in CI; per-model monitoring thresholds; virtual scrolling; message broker (DB-claimed queue; `SKIP LOCKED` not used); idempotency-key TTL; `/metrics` is unauthenticated (restrict via NetworkPolicy/ingress); the reconciler treats an unknown runtime state as failed (safe, may need manual retry); seeded metrics are dated July 2026 so the UI shows STALE (a seed option that rebases timestamps is not built); the K8s manifest is designed and schema-checked, not cluster-validated; artifact checksum is verified only by runtime adapters that implement `verify_artifact` (the simulator only contradicts a registered artifact); `STALE_RETRY` uses creation order, so cross-replica clock skew could misorder near-simultaneous requests (a DB sequence would be stricter); `monitoring.by_version` blends environments unless `environment` is passed; every worker replica runs the reconciler each poll (cheap at this scale).

- Auth is header-based (`X-Role`); no token validation. Do not expose as is.
- Runtime is simulated; no real KServe/SageMaker adapter; no canary/blue-green.
- Worker polls the DB; Postgres `SKIP LOCKED`/`LISTEN` optimisation not implemented (atomic UPDATE claim used).
- Metrics are seeded from CSV; there is no ingestion API, rollups or retention job.
- Sample data inconsistencies (metrics/events reference versions absent from the registry file, e.g. compressor 2.0.0, valve 3.0.0) are loaded as `seed-inferred` placeholder versions, stage VALIDATED, not approved.
- `dep-1003` (seed, `valve-health-model 3.0.0`) shows as retryable but will fail validation if retried: that version is unapproved, which is the correct outcome.
- Audit log is append-only by convention, not cryptographically tamper-evident.
- Multi-tenancy, OpenTelemetry tracing, rate limiting (beyond ingress) and generated TS client are designed but not built.
- Angular uses plain CSS rather than Angular Material to keep the bundle small (95 kB transfer).
- In-process metrics counters reset on restart and are per-replica.
