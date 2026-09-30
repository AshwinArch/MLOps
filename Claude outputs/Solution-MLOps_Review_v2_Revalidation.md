# Solution-MLOps — Review v2: Re-validation of the Revised Solution

Scope: the revised `Solution-MLOps` folder, checked against my v1 findings (F1–F18) and the author's `docs/review-response.md`.
Evidence tags: **[RUN]** executed here, **[INSPECT]** read only, **[N/A]** not verifiable here.

## 1. Bottom line

Most of the v1 findings are genuinely fixed, and I reproduced that by running code, not just by reading the author's table. The work is materially better: the state machine, governance, reconciliation and Postgres behaviour are now credible.

It is **not yet "everything fixed"**. Three things stand out:

1. The **Kubernetes manifest is still wrong in ways that would break a real deploy** (ingress path, service name, NetworkPolicy blocks). The author's claim that the docs/manifest drift is "Fixed" is only half true.
2. **Four-eyes can be bypassed by changing the case of the `X-User` header.** The author's own limitations doc says identity headers are forgeable, but this is a cheap bypass even inside that model.
3. **Artifact checksum is stored but never verified**, so F14 is only partly closed.

Lifecycle vocabulary: backend behaviour is now **Validated** (on SQLite and PG16). K8s/CI is **Designed, not Validated**.

## 2. What I ran

| Check | Result |
|---|---|
| Backend suite, SQLite | 85 tests pass, 94.16% coverage (gate 85%) **[RUN]** |
| Backend suite, PostgreSQL 16 (`TEST_DATABASE_URL`) | 85 pass **[RUN]** |
| ruff | clean **[RUN]** |
| import-linter | 2 contracts kept, 0 broken **[RUN]** |
| Alembic `upgrade head` → `downgrade base` → `upgrade head` → `alembic check` on PG16 | passes, "No new upgrade operations detected"; tables incl. `environment_state`; indexes `uq_active_deployment`, `uq_idempotency_scope` present **[RUN]** |
| Angular production build | OK **[RUN]** |
| Angular tests | 21 of 21 pass **[RUN]** |
| My v1 defect probes adapted to the new API (P1–P10, PG probes) | results below **[RUN]** |

Not run: live `demo.sh` / docker compose (no Docker here), a real K8s dry-run, `pip-audit`/`npm audit` results.

## 3. Finding-by-finding verdict

| # | v1 finding | Verdict | Evidence |
|---|---|---|---|
| F1 | No Makefile / CI | **Partly fixed** | Both exist and are sensible (ruff, lint-imports, pytest with 85% gate, PG job, Angular test/build, compose build). But `ci.yml` sits at the repo root, not `.github/workflows/`, so it will not run until moved. The author flags this as a hand-copy step. `pip-audit` and `npm audit` end in `|| true`, so they never fail the build. [INSPECT] |
| F2 | Stuck `VALIDATING` never recovered | **Fixed** | Probe: row claimed then abandoned → reconciler re-queues → next `run_pending` gives SUCCEEDED. Poison row is failed after 3 re-queues with `validation_requeue_limit`. Stuck `DEPLOYING` with runtime-side success is adopted via `lookup()` → SUCCEEDED. [RUN] |
| F3 | Stale retry downgrades live version | **Fixed** | v1 fails, v2 succeeds, retry of v1 → `409 STALE_RETRY`; `retryable` flag is false. [RUN] |
| F4 | Approval / withdraw / re-approval / four-eyes holes | **Mostly fixed** | Reject now also works from STAGING (returns to DRAFT/REJECTED, re-validate works). `WITHDRAWN` exists; rollback to a withdrawn version → `409 ROLLBACK_TARGET_UNSAFE`. After a rollback the bad version goes back to VALIDATED/PENDING and re-deploy → `409 VERSION_NOT_APPROVED`. Self-approval → `403 SELF_APPROVAL`. **Gap:** `X-User: ALICE` approves Alice's own version (see N1). [RUN] |
| F5 | PG-only 500s; 500 loses correlation id | **Fixed** | On PG16: 100-char correlation id, 600-char artifact_uri, 80-char framework, 300-char algorithm, 5 MB metadata → 201 or clean 422, no 500. Oversized correlation id is replaced with a fresh one; a forced 500 keeps the (valid) inbound id in body and header. [RUN] |
| F6 | Fail-open defaults | **Mostly fixed** | Defaults now: `default_role=viewer`, four-eyes on, simulation off, seeding off; no-header write and `/audit` → 403; `simulate_failure` with simulation off → `422 SIMULATION_DISABLED`. `/metrics` is still unauthenticated (documented) and `X-Role`/`X-User` are still forgeable (`X-Role: admin` → 201), which the author now states plainly in `known-limitations.md`. Compose and `make run` still ship demo switches on with port 8000 published. [RUN] |
| F7 | Reconciliation claim vs implementation | **Mostly fixed** | `ModelRuntime.lookup(token)` exists and the reconciler uses it (probe P2c). Caveat: a runtime **timeout** marks the row FAILED immediately, so the reconciler never gets to adopt a call that later completes remotely; the code comment says it can. Retry reuses the same token, which protects against a double deploy, but the "adopt via lookup" wording is inaccurate for timeouts. [INSPECT] |
| F8 | Dual source of truth for live state | **Fixed** | `EnvironmentState` is updated in the same transaction as the deployment result. Seeded data probe: production deploy shows `previous_version=1.0.0`, rollbackable, stages consistent (1.0.0 ARCHIVED, 1.1.0 PRODUCTION). [RUN] |
| F9 | Observability gaps | **Fixed** (with a K8s caveat) | Worker serves `/metrics` on 9100; latency histogram added; denied attempts write `*.denied` audit rows. But the K8s NetworkPolicy blocks Prometheus from scraping it (see N2). [INSPECT] |
| F10 | Migrations on every start; incomplete K8s | **Partly fixed** | `MIGRATE_ON_START=false` by default; Alembic chain is clean on PG. K8s manifest now has a migrate Job, worker, HPA, PDB, NetworkPolicies, but see N2 for defects. [RUN]/[INSPECT] |
| F11 | Monitoring has no staleness | **Fixed** | STALE status after `stale_metrics_hours`, worst-of-last-3 logic, `data_age_hours`, frontend banner. Unit tests present. [INSPECT] |
| F12 | N+1, 200-row cap, 2.5 s polling | **Mostly fixed** | `GET /models` with 20 models: **3 SQL statements** (was 22). Polling pauses in hidden tabs; paging added. The UI client still requests `limit=200`. [RUN]/[INSPECT] |
| F13 | Sequential worker, no timeout, 32-bit ids, global idempotency keys | **Fixed** | Thread-pool concurrency (default 4), runtime timeout (60 s default), ids `dep-<12 hex>`, idempotency scoped per user. PG: 16 parallel same-key requests → exactly 1 row; 16 distinct keys → 1 row (others 200/409). [RUN] |
| F14 | No artifact integrity | **Partly fixed** | Prefix allowlist and `sha256:` format check exist. The checksum is never verified against the artifact anywhere, and is optional, so it is metadata, not integrity. [INSPECT] |
| F15 | SQLite-only tests | **Fixed** | Whole suite runs on PG16; CI has a PG job. Note the PG path builds schema with `create_all`, but I separately proved Alembic parity. [RUN] |
| F16 | Frontend issues | **Fixed** | Native `confirm()` replaced by a typed-confirmation service, user field editable, time-axis charts, role="alert"/"status" used. 21 tests pass. [RUN]/[INSPECT] |
| F17 | Unpinned deps, no .dockerignore, nginx headers | **Fixed** | `requirements.lock`, `.dockerignore`, non-root UID 10001, CSP/nosniff/DENY/no-referrer headers, missing assets return 404. [INSPECT] |
| F18 | No plant dimension; VALIDATED only a label | **Partly / documented** | Plant dimension explicitly documented as not built. `validation_required_fields` makes VALIDATED enforce something, but it defaults to empty, so out of the box it is still only a label. [INSPECT] |

Tally: **Fixed 10 (F2, F3, F5, F8, F9*, F11, F13, F15, F16, F17)**, **Mostly fixed 5 (F4, F6, F7, F12, F10 backend side)**, **Partly fixed 3 (F1, F14, F18)**, **Not fixed 0**. (*F9 has the K8s scrape caveat.)

## 4. New feedback (introduced or newly visible)

**N1 — Four-eyes bypass by header case (Medium, [RUN]).** Alice registers a version, then sends `X-User: ALICE` with an approver role: `200`. The check compares raw strings. Even in a header-identity model this should normalise (strip + casefold) on both sides, and ideally compare a stable subject id.

**N2 — Kubernetes manifest would fail on a real cluster (High for the "production view" claim, [INSPECT]).**
- Ingress routes `/api` (Prefix) straight to the `api` Service with no rewrite. The backend serves `/models`, not `/api/models`, so every UI call 404s. Compose works only because nginx strips the prefix.
- `frontend/nginx.conf` proxies to `http://backend:8000`, but the K8s Service is named `api`, so that path cannot resolve.
- `default-deny` for Ingress and Egress plus allow rules only for `api/worker` egress and `api/frontend` ingress from `ingress-nginx` means: the **db-migrate Job has no egress** (no `app` label matches), **Prometheus cannot scrape worker:9100**, and **frontend→api traffic is blocked**.
- The frontend Deployment has no `securityContext`, and nginx on port 80 needs root unless an unprivileged base is used (the comment admits this).
- The manifest header says it is not applied or dry-run validated, which is honest, but `review-response.md` lists the drift as "Fixed". I would word it "Designed".

**N3 — Timeout semantics vs the stated reconciler guarantee (Low/Medium, [INSPECT]).** On `runtime_timeout` the row goes straight to FAILED/TRANSIENT, so `lookup()` adoption never applies. If the remote call later completes, the runtime holds a live deployment while the DB says FAILED until someone retries (same token makes the retry safe). Either leave the row DEPLOYING for the reconciler, or have retry call `lookup()` first, and fix the code comment.

**N4 — Checksum is decorative (Medium, [INSPECT]).** Either make it required for PRODUCTION-bound versions and verify it at the VALIDATING step (or at least pass it to the runtime), or rename the claim to "artifact metadata".

**N5 — CI is not live until moved, and scanners don't gate (Medium, [INSPECT]).** Move `ci.yml` to `.github/workflows/ci.yml`; drop `|| true` on `pip-audit`/`npm audit` or set an explicit severity threshold.

**N6 — Demo defaults still ship on (Low/Medium, [INSPECT]).** `docker-compose.yml` enables seed, failure simulation and migrate-on-start and publishes port 8000 directly; `make run` sets `DEFAULT_ROLE=admin` and four-eyes off. Fine for a demo, but call the file `docker-compose.demo.yml` or put a prominent banner, so nobody promotes it.

**N7 — Seeded metrics render as STALE (Low, [INSPECT]).** Documented by the author (data dated July 2026). Correct behaviour, but a reviewer's first screen looks alarming; consider a seed option that rebases timestamps to "now".

**N8 — Smaller items (Low).**
- `/ready` inspects the private `worker._thread` attribute; expose an `is_alive()` method.
- Worker `_loop` runs reconcile on every poll cycle in every replica; fine at this scale, but mention the cost.
- `STALE_RETRY` compares creation order, so clock skew between replicas could misorder two near-simultaneous requests; a DB sequence or `row_version` would be stricter.
- `run.py` coverage is about 66% (the worker entrypoint and metrics server are the least tested code).
- `monitoring.by_version` groups by version only, not by (version, environment), so staging and production points blend in one summary unless the caller passes `environment`.
- The UI still asks for `limit=200`.

## 5. Recommended next steps (smallest set that would let me call it done)

1. Fix N2: ingress rewrite (or route via frontend nginx), service name, NetworkPolicy allow rules for migrate Job, frontend→api, Prometheus→worker; add a `kubeconform`/`kubectl --dry-run=client` step to CI.
2. Fix N1 (casefold/strip `X-User`) and add a test.
3. Move `ci.yml` into `.github/workflows/` and make the audit steps gating.
4. Decide on N3/N4: verify the checksum at validation time; make timeout leave the row reconcilable.
5. Relabel the review-response statuses: backend items "Fixed (validated)", K8s items "Designed".

## 6. Suggested wording for the author's response table

- "K8s manifest: Designed, not cluster-validated. Known defects being fixed: ingress rewrite, service name, NetworkPolicy."
- "Artifact integrity: format check + allowlist implemented; content verification not implemented."
