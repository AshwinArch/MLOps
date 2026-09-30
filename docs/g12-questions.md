# G12 questions answered

**1. How would this scale from 100 to 10,000 models?**
The control plane is small data (10^4 models, 10^5 versions) but bursty. Path: (a) 100 models: current shape. (b) 1,000: run API and workers as separate Deployments, index-backed filters (already present), pagination (already enforced, max 200), PgBouncer. (c) 10,000: read replicas for inventory and metrics queries, Redis cache for inventory summaries (invalidate on version events), move the work queue from DB polling to a broker while keeping the DB claim as source of truth, shard worker pools per environment/runtime, and replace the per-model `versions` eager load in list views with an aggregate query. The limiting resource is metric volume, not registry size (Q6).

**2. How are conflicting promotions prevented?**
Three layers: the synchronous gate rejects a second *different* version while one is active (`DEPLOYMENT_IN_PROGRESS`); a partial unique index makes the same rule true at the database even across replicas and races; `row_version` optimistic locking stops two writers updating the same version/deployment (`CONCURRENT_UPDATE`). Verified by a concurrent-request test (8 threads, one deployment).

**3. How is external success / internal database failure reconciled?**
We persist intent (`runtime_call_started`, token = deployment id) before calling the runtime, and the runtime call is idempotent on that token. If the process dies after the runtime succeeded, the row is stuck `DEPLOYING`; the reconciler (every poll) looks at rows older than `STUCK_DEPLOYMENT_SECONDS`, asks the runtime for the state by token/ref, and either completes the deployment (`SUCCEEDED`) or fails it as `RECONCILIATION` (retryable, alertable). Tested both ways. Future: transactional outbox so the runtime call is triggered from a committed row and cannot be lost or duplicated.

Update: on a runtime timeout the row fails as TRANSIENT, and the retry first asks `lookup(token)` and adopts a call that completed remotely, otherwise re-sends the same token. The runtime port exposes `lookup(idempotency_token)` so the reconciler can ask the runtime about a deployment it lost track of; stuck `VALIDATING` rows are re-queued by the reconciler (cap 3), stuck `DEPLOYING` rows are resolved from the runtime. The `EnvironmentState` table is updated in the same transaction as the deployment result, so it never disagrees with deployment history.

**4. How are multiple model runtimes supported?**
`ModelRuntime` is a port (`deploy`, `status`). Each runtime (KServe, SageMaker, Azure ML, Triton, batch) is an adapter selected by a `runtime` attribute on the model/version/environment. Adapters must be idempotent on a token and report status by reference; capability differences (canary support, GPU) are declared by the adapter, not leaked into the domain.

**5. How would multi-tenancy work?**
Add `tenant_id` to every aggregate and to every unique constraint (`(tenant_id, model_id)`), derive it from the OIDC token (never from the request body), enforce with a repository-level filter plus Postgres row-level security as defence in depth, namespace artifact prefixes and runtime targets per tenant, and partition metrics by tenant. Noisy-neighbour control: per-tenant rate limits and worker pool quotas. Stronger isolation tier (dedicated DB/schema) for regulated tenants.

**6. How are large metric volumes partitioned?**
Metrics are a separate ingest path, not API writes. Raw points go to a time-partitioned table (daily/weekly range partitions, hash sub-partition by `model_id` at scale), or ClickHouse/Timescale beyond ~10^8 rows. Pre-aggregated rollups (5 min, 1 h, 1 d) serve dashboards; raw data is retained for days, rollups for months; old partitions are detached/dropped (instant, no vacuum). The unique key `(model, version, env, timestamp)` makes ingestion idempotent.

**7. How is unsafe rollback prevented?**
Only the live `SUCCEEDED` non-rollback deployment can be rolled back; the target is the recorded `previous_version`; the target must still be approved; first deployments have no target (refuse rather than "undeploy"); rolling back a rollback is refused; a rollback runs through the same worker validation and the one-active-deployment constraint; it is audited; the target must be approved and not `WITHDRAWN`, and the rolled-back version returns to VALIDATED/PENDING so it needs re-approval. Four-eyes (`SELF_APPROVAL`) applies to approvals. Future: require the target's last known health to be non-critical, and two-person approval for production rollbacks outside incidents.

**8. How are zero-downtime schema migrations handled?**
Alembic, expand/contract (migrations run only via the pre-upgrade Job or the explicit `MIGRATE_ON_START` flag; `AUTO_CREATE_SCHEMA` is off outside dev): (1) expand — add nullable columns/tables/indexes (`CONCURRENTLY` in Postgres) in a release that old and new code both tolerate; (2) deploy code that writes both; (3) backfill in batches; (4) switch reads; (5) contract — drop old columns in a later release. Migrations run as a pre-upgrade Job, never on every replica start; rollbacks of code never require rolling back schema because expansion is backward compatible. `render_as_batch` is enabled so SQLite dev works.

**9. How is Angular isolated from backend internals?**
Only `ApiService` knows URLs; components consume typed interfaces (`core/models.ts`) that mirror the OpenAPI contract, not DB shapes. Everything goes through `/api` (nginx/ingress), so the backend can move or be split without UI changes. Errors arrive as one envelope (`code`, `message`, `correlation_id`) and are normalised by an interceptor. Contract-first next step: generate the TS client from `openapi.json` in CI and fail on drift.

**10. How would work be split across teams?**
Team A *Registry & governance* (registry, approval, RBAC, audit). Team B *Deployment & runtime* (worker, adapters, reconciler, rollback). Team C *Observability & monitoring* (metric ingest, aggregation, alerts, dashboards). Team D *Frontend/DX* (Angular, design system, generated client). Platform/SRE owns CI, K8s, migrations, SLOs. Module boundaries in the codebase already match; contracts between teams are the OpenAPI spec and the `ModelRuntime` port.
