# Response to the Technical Team Deep Dive Review

Status legend: **Fixed (validated)** = code + regression test, run on SQLite and PostgreSQL 16; **Designed** = specified but not cluster-validated; **Documented** (scope decision recorded in `known-limitations.md`), **Hand-copy** (protected file).

| Area | Finding | Status |
|---|---|---|
| P0 | Stuck `VALIDATING` rows never recovered | Fixed (validated): reconciler re-queues (cap 3); test in `test_review_regressions.py` |
| P0 | Retry of a superseded deployment | Fixed (validated): `STALE_RETRY`, `retryable` hint false when superseded |
| P0 | Unbounded inputs | Fixed (validated): length limits, tag pattern, metadata <= 10KB |
| P0 | Hostile correlation ids / 500 loses id | Fixed (validated): sanitised regex; 500 body keeps id |
| P0 | Insecure defaults (role, seeding, simulation) | Fixed (validated): viewer / off / off; demo switches only in compose and `make run` |
| P0 | Docs vs code drift (migrations, K8s, worker metrics) | Fixed (validated): K8s manifest has migrate Job, worker, HPA, PDB, NetworkPolicies; worker serves `/metrics` on 9100 |
| P0 | Makefile / CI out of date | Fixed, but both are protected files: copy `Makefile` and `.github/workflows/ci.yml` by hand |
| P1 | "What is live" derived from history | Fixed (validated): `EnvironmentState` table |
| P1 | Runtime port could not answer "what did you do?" | Fixed (validated): `lookup()` + reconciler |
| P1 | No runtime timeout / serial worker | Fixed (validated): timeout and concurrent pool |
| P1 | Idempotency keys global | Fixed (validated): scoped per user |
| P1 | No self-approval control; no withdraw; denied actions unaudited | Fixed (validated): four-eyes, `WITHDRAWN`, `*.denied` audit |
| P1 | Rollback left bad version approved | Fixed (validated): returns to VALIDATED/PENDING |
| P1 | Monitoring hid stale data; no latency histogram | Fixed (validated): STALE + worst-of-3 + histogram |
| P1 | Artifact integrity | Fixed (validated): checksum + store allowlist; `file://` removed |
| P1 | N+1 on inventory | Fixed (validated): `selectinload`, query-count test |
| P1 | No PostgreSQL testing; unpinned deps; layering unchecked | Fixed (validated): suite green on PG 16, `requirements.lock`, import-linter contracts, PG CI job |
| P2 | Frontend: charts, confirmations, paging, polling, user field | Fixed (validated): time-axis charts, typed confirmation for production rollback/withdraw, paging, hidden-tab pause, editable user; 21 tests |
| P2 | nginx headers / missing assets | Fixed (validated) |
| P2 | Plant dimension, OIDC, OpenTelemetry, generated client, Playwright CI, per-model thresholds, virtual scroll, broker / SKIP LOCKED | Documented as not built |

Validation: 85 backend tests on SQLite and PostgreSQL 16 (94% coverage), ruff clean, import-linter 2/2 kept, Alembic up/down/up with no drift, Angular build OK, 21 frontend tests, live run with `scripts/demo.sh`.

## Review v2 follow-up

| Item | Status |
|---|---|
| N1 four-eyes bypass by `X-User` case | Fixed (validated): trim + casefold; test with `ALICE`, `  Alice `, `aLiCe` |
| N2 K8s ingress path, service name, NetworkPolicies, frontend security context | **Designed**: Ingress routes only to frontend (nginx proxies `/api/` to `api`), `API_UPSTREAM` env, unprivileged nginx on 8080, policies for frontend->api, Prometheus->api/worker, migrate Job egress; kubeconform in CI; not cluster-validated |
| N3 timeout vs reconciler wording | Fixed (validated): retry calls `lookup(token)` first and adopts a completed remote call; comment corrected |
| N4 checksum decorative | Fixed (validated) for the port: required for production, verified via `verify_artifact`, mismatch is PERMANENT. Real adapters must implement it; the simulator only contradicts a registered artifact |
| N5 CI not live, scanners non-gating | `pip-audit`/`npm audit` now gate; `ci.yml` must be copied to `.github/workflows/` by hand (protected path) |
| N6 demo defaults | Compose carries a DEMO ONLY banner; a rename to `docker-compose.demo.yml` was not done |
| N7 STALE on seeded data | Documented; rebase option not built |
| N8 `is_alive()`, UI `limit=200` | Fixed (validated) (UI default page size 50). Reconcile cost, `STALE_RETRY` ordering, `by_version` blending: Documented |

Artifact integrity wording: format check + allowlist + runtime-side content verification where the adapter supports it.

## Review v3 (independent SME review)

| Finding | Status |
|---|---|
| F1 P0 no authentication | Fixed (validated with locally signed tokens): JWT mode on every route but health/ready, production guard, dev header mode remains the default. Not validated against a real IdP |
| F2 P0 simulator-only runtime | Partly fixed: runtime chosen by `RUNTIME_ADAPTER`, production guard forbids the simulator, a REST adapter with a documented contract exists. **Not validated against a real runtime**; real KServe/SageMaker integration remains |
| F3 P1 revoked/archived version completes | Fixed (validated on SQLite and PostgreSQL): row lock + re-check at completion, compensating undeploy, `VERSION_IN_USE`, tests for revoked and archived |
| F4 P1 late timeout completion | Partly fixed: late-completion sweep (undeploy when superseded, flag otherwise) and a periodic drift check. Fencing generations and cancel-on-timeout not built |
| F5 P1 database invariants | Fixed (validated on PostgreSQL 16, migration up/down/up, no drift): CHECKs, single-production index, composite FKs, 64-bit keys |
| F6 P1 pipeline / nginx | Fixed in the repo: the nginx template was 0 bytes (my mistake) and is now real with a regression test; CI gains an image smoke job. `ci.yml` still has to be placed at `.github/workflows/ci.yml` by hand, and CI has never run |
| F7 runtime port | Partly: `undeploy` added to the port, HTTP adapter maps failures; two-phase submit/poll/cancel not built |
| F8 lease, dead-letter, auto retry | Not fixed (documented) |
| F9 scale | Partly: 64-bit keys. UI polling, search index, partitions not done |
| F10 observability | Partly: request correlation id now follows the deployment into worker events; queue-depth and duration metrics still missing |
| F11 idempotency edges, F12 rollback pre-flight, F13 scoped approvals, F14 audit immutability, F15 migration drill, F16 tenancy | Not fixed (documented) |
| F17 doc drift | Fixed (rollback stage, counts, ADR-001 follow-up); ADR-005 and ADR-006 added |
| F20 checksum, F22 unsafe flags | F22 fixed by the production guard; F20 (make `verify_artifact` mandatory) not done |
| F18, F19, F21 | Not fixed (documented) |

Validation: 109 backend tests on PostgreSQL 16 (108 + 1 SQLite skip), 95% coverage, ruff and import-linter clean, Alembic up/down/up with no drift, 22 frontend tests, frontend build.
