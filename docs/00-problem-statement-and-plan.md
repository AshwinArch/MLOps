# Problem Statement, Plan and Solution Map (Role level: G12)

## 1. Problem statement

An industrial organisation runs many ML models across plants and environments. There is no single place to register models, control which versions may go live, deploy them, watch their health and undo a bad release. The task is to build a **representative MLOps platform**: a Python backend plus an Angular GUI that can **register, version, approve, deploy, monitor and roll back** models.

The evaluation rewards production-quality engineering, not model training. A complete vertical slice beats broad but unfinished scope. Because the role level is **G12 (Principal / Tech Lead)**, architecture, governance, async processing, observability and leadership artifacts carry 25% + 10% of the score.

### 1.1 Functional requirements

| Area | Requirement |
|---|---|
| Registry | Create models; register versions with tags, metadata, framework, algorithm, artifact URI, training-data reference, approval status, lifecycle stage, audit timestamps |
| Lifecycle | Stages `DRAFT, VALIDATED, APPROVED, STAGING, PRODUCTION, ARCHIVED`; only legal transitions allowed |
| Deployments | Request, choose environment, track status (`REQUESTED, VALIDATING, DEPLOYING, SUCCEEDED, FAILED, ROLLED_BACK`), history, failure simulation, retry, rollback, idempotency |
| Monitoring | Latency, throughput, error rate, quality, drift, availability, last successful inference, monitoring status |
| API | The 12 minimum endpoints, typed requests/responses, validation, consistent errors, structured logs, health, OpenAPI docs |
| GUI | Model inventory, version detail, deployment view, monitoring dashboard, event timeline, filters/search, loading/empty/success/error states, responsive |
| Quality | Unit, API, integration and end-to-end tests; CI; Docker Compose |

### 1.2 Mandatory G12 additions

1. Architecture: modular boundaries, sync vs async rationale, scaling, persistence/cache/queue/concurrency choices, Kubernetes view, trade-offs and rejected alternatives.
2. Governance: approval and promotion workflow, idempotency, concurrency control, rollback safety, audit history, RBAC design, environment promotion controls.
3. **At least one long-running workflow implemented asynchronously** (here: deployment).
4. Observability: structured logs, correlation IDs, metrics, health/readiness, failure classification, dashboard proposal.
5. Leadership artifacts: 2+ ADRs, delivery plan, risk register, roadmap, code-review checklist, production-readiness checklist.
6. Written answers to the 10 G12 questions (scale 100 -> 10,000 models, conflicting promotions, external-success/DB-failure reconciliation, multi-runtime, multi-tenancy, metric partitioning, unsafe rollback, zero-downtime migrations, Angular isolation, team split).

### 1.3 The 10 acceptance scenarios (the definition of done)

1. Register a model and two versions. 2. Approve one. 3. Block an unapproved version from Production. 4. Deploy an approved version. 5. Show monitoring data in Angular. 6. Retry a failed deployment. 7. Roll back a Production deployment. 8. Handle duplicate deployment requests safely. 9. Show API failures clearly in the UI. 10. Verify critical flows with automated tests.

### 1.4 Hidden traps found in the pack data (handled on purpose)

- `sample_deployment_events.json` has `dep-1002` FAILED with `approval_validation_failed`: the unapproved `compressor-anomaly-detector 1.1.0` was sent to production. Proves the approval gate must be enforced server side and recorded as an audit event.
- `dep-1003` `runtime_timeout` is a *transient* failure (retryable); approval failure is *permanent* (not retryable). Failure classification must distinguish them.
- The registry sample has `stage: PRODUCTION` alongside `approved: true`, and `VALIDATED` with `approved: false`: approval and stage are two separate facts.
- Metrics CSV has 90 daily rows for 3 models; the seed loader must be idempotent.
- The seed `openapi_seed.yaml` returns `202` for `POST /deployments`: the async contract is given; the API must not block.
- The seed compose has no worker and uses Postgres with published DB port and default credentials: flagged in docs, made configurable through `.env`.

## 2. Plan

| Step | Output |
|---|---|
| 1. Domain first | Pure-Python lifecycle and deployment state machines, governance rules, unit-tested in isolation |
| 2. Persistence | SQLAlchemy 2.0 models; SQLite default, Postgres via `DATABASE_URL`; optimistic locking `row_version`; unique idempotency key; audit/event tables |
| 3. Services | `RegistryService`, `DeploymentService`, `MonitoringService`, `AuditService` with clear ownership |
| 4. Async worker | In-process worker pool with DB-backed queue semantics (claim by status), simulated runtime adapter with failure injection; swap-in point for Celery/Arq/K8s Jobs |
| 5. API | FastAPI routers, Pydantic v2 schemas, uniform error envelope, correlation-ID middleware, JSON logs, `/health` and `/ready`, `/metrics` |
| 6. Tests | Unit -> API -> integration -> e2e scenario covering all 10 acceptance scenarios |
| 7. Angular | Standalone-component app, typed API service, RxJS state, 5 views, all UI states, error interceptor |
| 8. Packaging | Dockerfiles, `docker-compose.yml` (backend, worker-in-process, frontend, Postgres), Makefile, GitHub Actions |
| 9. G12 docs | Architecture, ADRs, answers to the 10 questions, delivery plan, risk register, roadmap, checklists, test strategy, known limitations |

### Key design decisions

- **Deployment is asynchronous**: `POST /deployments` validates, persists `REQUESTED`, returns `202` and a worker drives `VALIDATING -> DEPLOYING -> SUCCEEDED|FAILED`. Synchronous checks (approval, stage, duplicates) run before enqueue so bad requests fail fast with `409/422`.
- **Idempotency**: client `Idempotency-Key` header, with a natural-key fallback (model + version + environment while an active deployment exists). Same key and same body returns the original; same key and different body returns `422`.
- **Approval is separate from stage**; Production requires `approved=true` and the version in `STAGING` or `APPROVED`; only one active deployment per (model, environment) through a partial unique constraint and row-version checks.
- **Rollback safety**: only a `SUCCEEDED` deployment can be rolled back, only if a previously successful, still-approved, non-archived version exists for that environment; the rollback target is recorded; the rollback is itself a deployment with an audit trail.
- **Reconciliation**: the runtime call is recorded with an external reference *before* the DB commit of the result; a reconciler marks `DEPLOYING` rows older than a timeout by asking the runtime for truth.
- **RBAC**: header-based role (`viewer`, `engineer`, `approver`, `admin`) enforced by a dependency; designed to be replaced by JWT/OIDC.

## 3. Solution map

```
backend/app/...   domain/ services/ api/ workers/ db/ core/
backend/tests/    unit, api, integration, e2e
frontend/         Angular app
docs/             architecture, ADRs, G12 answers, delivery plan, risks, roadmap, checklists
```

See `README.md` for how to run it.
