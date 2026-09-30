# Risk register

| # | Risk | L | I | Mitigation | Owner |
|---|---|---|---|---|---|
| 1 | Runtime succeeds but DB write fails -> split brain | M | H | Intent-before-call, idempotent token, reconciler, alert on RECONCILIATION | Deployment team |
| 2 | Unapproved or revoked model reaches production | L | H | Gate at request + re-check in worker; audit; alert on prod deploys lacking approval | Governance |
| 3 | Concurrent promotions corrupt state | M | H | Partial unique index, optimistic locking, tests | Deployment team |
| 4 | Metric volume overwhelms Postgres | H | M | Partitioning, rollups, ingest separation, TSDB decision gate (ADR-004) | Observability |
| 5 | Header-based auth shipped to production | M | H | `AUTH_MODE=jwt` implemented; `ENVIRONMENT=production` refuses header auth at start-up; still needs IdP integration test | Security / Tech lead |
| 11 | Approval revoked or version archived while a deployment runs | M | H | Row lock + re-check at completion, compensating undeploy, `VERSION_IN_USE`, DB CHECKs | Governance |
| 12 | Timed-out runtime call completes late and diverges from the database | M | H | Late-completion sweep, periodic drift check, retry adopts via `lookup`; fencing generations not built | Deployment team |
| 13 | Runtime adapter never validated against a real platform | H | H | Adapter contract + mock-transport tests; staging validation is an exit criterion before go-live | Deployment team |
| 6 | Single-team knowledge silo | M | M | ADRs, checklists, pairing, rotation across modules | Tech lead |
| 7 | Unsafe rollback during an incident | M | H | Rollback guard rails; future health check of target and 2-person rule | Deployment team |
| 8 | Schema migration causes downtime | M | H | Expand/contract, pre-upgrade Job, migration drill | SRE |
| 9 | Runtime adapter differences leak into domain | M | M | Port/adapter + capability flags; contract tests per adapter | Deployment team |
| 10 | Sample/real data mismatch (unknown versions) | H | L | Seed inference flagged `seed-inferred`; data-quality checks at ingest | Platform |
