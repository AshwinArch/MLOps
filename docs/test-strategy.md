# Test strategy

Run: `make test` (or `cd backend && python -m pytest --cov=app` and `cd frontend && npx ng test --watch=false --browsers=ChromeHeadlessCI`).

## Unit tests (backend, `tests/unit`, 23 tests)
Lifecycle transitions and rejection of illegal ones, approval gate per environment, deployment FSM, failure classification (unknown -> PERMANENT), retry rules, health classification and summary math.

## API tests (`tests/api`, 22 tests)
Success paths, validation envelope with correlation id, 404s, duplicates, conflicts, idempotency (replay / key reuse / natural dedupe), RBAC (viewer, engineer vs approver), audit, async contract (202 then status), transient retry, permanent non-retry, revoked approval caught by worker.

## Integration tests (`tests/integration`, 64 tests; real worker code, SQLite or PostgreSQL)
The full acceptance flow: register -> approve -> staging -> production -> second release -> rollback (and every unsafe rollback refusal); concurrency (8 simultaneous identical requests -> 1 deployment); exclusive worker claim; reconciler adopting external success and failing unknown state; metrics comparison; Prometheus output; idempotent seed loading against the real sample data.

## Angular tests (22 tests, Karma + Jasmine, headless Chromium)
`ApiService` + interceptors (headers, param building, idempotency header, error envelope mapping, network failure), shared `StateComponent` (loading / error / empty / content), badge tones, chart rendering, and `DeploymentsPage` (loading, rows + Retry action, empty, API failure with correlation id).

## End-to-end scenario
`scripts/demo.sh` drives the real API + worker: register -> approve -> blocked unapproved prod -> idempotent duplicate -> staging -> production -> release 2 -> rollback. Screenshots in `docs/screenshots` come from the running UI.

## Coverage and CI
Backend: **109 tests, 95% coverage** (gate 85%), green on SQLite and on PostgreSQL 16 (`TEST_DATABASE_URL`, schema recreated per test). Review regressions live in `test_review_regressions.py` and `test_governance_and_state.py` and `test_v2_review.py` and `test_v3_review.py` (JWT validation incl. forged headers, production startup guard, governance re-check at completion with compensating undeploy, `VERSION_IN_USE`, late-completion sweep, drift check, database CHECK/unique/FK invariants, HTTP runtime adapter contract, correlation id propagation, nginx template not empty; header-case four-eyes bypass, timeout adoption, checksum enforcement, stuck VALIDATING requeue, stale retry, input bounds, hostile correlation ids, fail-closed defaults, four-eyes, live-state pointer, idempotency scoping, runtime timeout, concurrency, STALE monitoring, N+1 guard). Architecture contracts checked by import-linter. CI (`.github/workflows/ci.yml`): ruff, lint-imports, pytest with coverage gate, a PostgreSQL job (pytest plus Alembic up/down/up and drift check), kubeconform schema check of the K8s manifest, image smoke test (nginx serves the SPA, security headers, 404 on missing assets), gating pip-audit/npm audit, Angular tests, production build, docker build.

## Limitations
No browser E2E suite in CI (Playwright is the next step); no load/soak tests; real-runtime adapters untested; `SKIP LOCKED` is not used (atomic UPDATE claim). The partial unique index and Alembic migration (up/down/up, no drift) were verified on PostgreSQL 16.
