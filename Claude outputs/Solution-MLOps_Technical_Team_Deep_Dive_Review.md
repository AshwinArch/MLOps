# TECHNICAL TEAM ACTIVATED — Deep Dive Review
**Subject:** `Solution-MLOps` (G12 Principal/Tech-Lead assignment: FastAPI + Angular MLOps control plane)
**Mode:** Activate the Technical Team + Deep Dive
**Reviewed:** 2026-10-01

---

## 0. Evidence basis (read this first)

Every finding below carries an evidence tag so you can tell what was proven from what was only read.

| Tag | Meaning |
|---|---|
| **[RUN]** | I executed it and observed the result |
| **[INSPECT]** | Read from the code/docs only, not executed |
| **[N/A]** | Could not be verified in this session |

**What I read:** all 4 ADRs, all docs, README, backend (every `.py`, Dockerfile, compose, Alembic migration), K8s manifest, demo script, seed data, all Angular source + specs + nginx/Dockerfile, architecture diagram and 3 screenshots.

**What I executed:**
- Backend suite: 50/50 pass, 93% coverage (docs claim 92%) **[RUN]**
- `ruff check`: clean **[RUN]**
- Angular production build OK, 10/10 Karma tests pass in headless Chromium **[RUN]**
- Live API on uvicorn with the real worker thread + `scripts/demo.sh`: walks the whole acceptance flow successfully **[RUN]**
- PostgreSQL 16 (real server): `alembic upgrade head` succeeds, `alembic check` reports no drift, partial unique index present **[RUN]**
- Targeted probes (SQLite + PostgreSQL) for the defects reported here **[RUN]**

**Not verified [N/A]:** the original assignment brief/rubric (only the solution folder was connected; I reviewed against the requirements as restated in `docs/00-problem-statement-and-plan.md`), `docker compose up` and the K8s manifests on a real cluster (no Docker/K8s here), load/chaos behaviour, a real model runtime.

**Lifecycle vocabulary used** (per team rule #23): Proposed → Designed → Implemented → Tested → Validated → Production-ready. This solution is **Implemented and Tested (mostly on SQLite), partially Validated (PostgreSQL probes), not Production-ready** — which is the correct bar for an assignment and is honestly stated by the author in `known-limitations.md`.

---

## 1. Initial understanding / Missing information / Assumptions

**What we understand.** A governed control plane for ML models across environments: registry with semver versions, a lifecycle with approval as a separate fact from stage, asynchronous deployments (REQUESTED→VALIDATING→DEPLOYING→SUCCEEDED/FAILED) driven by a DB-claimed worker, idempotency (header + natural key + DB constraints), rollback as a new governed deployment, a monitoring read-model over seeded metrics, JSON logs with correlation IDs, Prometheus counters, RBAC via headers, an Angular SPA (5 views), 4 ADRs, 10 G12 answers, K8s view, risk register, roadmap, checklists. Modular monolith; Postgres (SQLite for dev).

**What is unclear**
1. The original grading rubric/brief (weights, mandatory vs optional items).
2. What "plants" mean for the domain — the brief says models run "across plants and environments", but the data model has no plant/site dimension.
3. Whether the submission is judged only on the repo or also on the claimed CI/`make` workflow.
4. Target runtime (KServe/SageMaker/…) and cloud — never specified.
5. Expected metric ingest volume/frequency and retention.

**Assumptions**
A1. This is an assignment submission, not a live production rollout. A2. Header-based auth is an accepted stand-in (documented). A3. The missing `Makefile`/`.github` are genuinely absent from the deliverable, not just excluded from this folder connection (the recursive listing of the folder root shows neither).

---

## 2. Product Director assessment

**Value & fit.** Strong. The submission solves the right problem (governance + lifecycle, not training) and scopes a real vertical slice: all 10 acceptance scenarios are demonstrably covered **[RUN: demo.sh + 50 tests]**. The author found and handled the hidden traps in the sample data (unapproved version sent to prod, transient vs permanent failure, stage ≠ approval, inferred placeholder versions) — this is the most differentiating product judgement in the pack.

**Gaps against product intent**
- **No plant/site dimension.** The stated problem is many models across *plants*. Environment is modeled; plant is not. A real user cannot answer "what is live at plant X?" **[INSPECT]**
- **"VALIDATED" is a label, not a gate.** `validate` just flips the stage; nothing checks artifact existence, metrics thresholds, required fields (e.g. `training_data_ref` is optional) or model-card completeness **[INSPECT]**.
- **No four-eyes.** The user who registers/validates a version can approve it (only role is checked, not identity) **[INSPECT]**.
- **Monitoring is not tied to release decisions** (no health gate before prod, no post-deploy health check) — acknowledged as roadmap.

**Metrics the product should define:** deployment lead time, failed-deploy rate by class, rollback rate, time VALIDATED→APPROVED, % prod deployments with staging evidence. The observability doc lists them, but most cannot be computed from what is exposed (see F9).

**Go/No-go:** **GO** as an assignment submission after the P0 fixes in §16. **NO-GO** for production (see Gate 6).

---

## 3. Product Manager assessment

**Requirements traceability.** The 10 scenarios map cleanly to tests (`test_e2e_scenarios.py`) — good traceability. Acceptance criteria are implicit in tests rather than written as stories, but adequate here.

**Requirement-level defects found**
- *Rollback safety requirement says "still-approved, non-archived" target* (problem statement §2). Code only checks `approval_status == APPROVED`; a superseded version is `ARCHIVED` by design, so "non-archived" cannot be enforced, and there is no way to withdraw a superseded version (F4) **[RUN]**.
- *"Approval failure recorded as an audit event"* (hidden-trap analysis). True only for worker-side failures. A request blocked synchronously (`VERSION_NOT_APPROVED`, 403s) writes **no audit or event row** **[INSPECT; consistent with `test_unapproved_version_cannot_go_to_production` asserting 0 deployments]**.
- *Documentation promises `make test`, CI gates, `pip-audit`, `npm audit`, docker build* — none exist in the folder (F1).

**NFR gaps:** no pagination UI beyond 200 rows, no retention policy, no SLOs defined numerically, no input size limits (F5).

**Definition of Done for the next increment:** see §16.

---

## 4. Architecture Director assessment

**Feasibility:** feasible and proportionate. Modular monolith + DB-claimed queue is the right call for one team and one transactional boundary (ADR-001/002). Not over-engineered; the "scale path" answers (Q1, Q6) are credible.

**Where the architecture claim exceeds the implementation**
1. **Reconciliation (Q3) is the weakest link.** The doc says the reconciler "asks the runtime by token/ref". The `ModelRuntime` port only has `status(external_ref)`. The worker gets the ref by calling `getattr(self.runtime, "_deployed", {})` — reaching into the simulator's private dict (`worker.py:203`). A real adapter cannot be reconciled with this port. "unknown" is also treated as "failed", so a slow-but-running deploy older than 300 s is failed and can be double-driven **[INSPECT]**.
2. **Two sources of truth for "what is live".** `model_versions.stage` and "latest SUCCEEDED deployment by `updated_at`" can disagree. Seed data proves it: after deploying compressor 1.1.0 to production, both 1.0.0 and 1.1.0 show stage `PRODUCTION`, and `previous_version` is `None` so rollback is unavailable although a prior prod version exists **[RUN: P10]**. "Live" also depends on `updated_at` ordering, which breaks under clock skew across worker pods **[INSPECT]**.
3. **Migrations contradict Q8.** Q8/K8s say migrations run as a pre-upgrade Job "never on every replica start". The Dockerfile `CMD` runs `alembic upgrade head` on every container start, and the K8s `api` Deployment sets no `command`, so every API pod also runs migrations (concurrent DDL race) **[INSPECT]**. Additionally `init_db()` calls `create_all()` at every start, which can silently mask migration gaps.
4. **Observability split-brain.** With `WORKER_ENABLED=false` on API pods (the K8s topology), all deployment counters (`deployments_succeeded_total`, `…failed_total{failure_class}`) are incremented in the worker process, which has no HTTP server and no `/metrics`. The alerts in `observability.md` that depend on them cannot fire in the documented topology **[INSPECT]**.
5. **K8s view is incomplete relative to docs.** `app.yaml` has no `Service` objects (Ingress references `api` and `frontend` services that do not exist), no frontend Deployment, no securityContext, no NetworkPolicy, no worker probes, and a Helm hook annotation on a plain manifest **[INSPECT]**.

**Security architecture:** fail-open by default — see F6. **Cost:** appropriate; main future cost driver is metric volume (correctly identified in ADR-004). **DR:** not addressed beyond "managed Postgres"; no RPO/RTO. **Verdict:** Gate 2 **conditional pass**.

---

## 5. Technical Architect assessment (detail + ADR review)

**Strengths**
- Pure domain package with no I/O; FSM + failure classification that *fails safe* (unknown reason → PERMANENT) — tested.
- DB-enforced invariant: partial unique index `uq_active_deployment` exists and works on real PostgreSQL 16; 16 parallel identical requests → exactly 1 row **[RUN]**.
- Intent-before-call pattern and exclusive claim via atomic `UPDATE … WHERE status='REQUESTED'`.
- Alembic migration applies cleanly to PG and matches the models (`alembic check`: no drift) **[RUN]**.
- Rollback-as-new-deployment (ADR-003) preserves history and reuses the safety pipeline — sound.

**Design defects (detailed in §14 Findings)**
- *VALIDATING is a dead-end state:* the reconciler scans only `DEPLOYING`. A worker crash/pod kill after the claim leaves `VALIDATING` forever, and because VALIDATING is "active" in the unique index, that (model, environment) is blocked permanently — new deploys return `DEPLOYMENT_IN_PROGRESS`, retry returns `NOT_RETRYABLE`. **Recovery needs manual SQL [RUN: P2]**.
- *No per-call timeout/cancellation on `runtime.deploy`,* and `run_pending` processes sequentially: one slow runtime blocks every other deployment on that worker **[INSPECT]**.
- *Idempotency keys are global* (one unique column), not scoped to actor/tenant, with no TTL; a second user replaying a key with an identical body receives the first user's deployment object **[INSPECT]**.
- *Deployment ids are `uuid4().hex[:8]`* (32 bits). A primary-key collision would surface as a misleading `DEPLOYMENT_IN_PROGRESS` **[INSPECT]**.
- `rollback_of` is neither FK nor indexed though queried on every rollback.

**ADR quality:** good format, honest negatives. Missing ADRs worth adding: *source of truth for live state*, *auth/identity*, *metrics exposure from workers*, *runtime port contract (token lookup)*.

---

## 6. Technical Lead assessment (code review verdict)

**Overall code quality: high for an assignment.** Clean layering, type hints, small functions, structured errors, ruff-clean, service-level RBAC, event+audit written in the same transaction as state changes.

**Defects (ranked)**

| # | Defect | Evidence |
|---|---|---|
| 1 | **Stale retry can downgrade production.** `retry_deployment` has no supersession check. Reproduced: v1 transient-fail → v2 deployed and live → retry v1 → `202`, worker succeeds, **v1 becomes live over v2**. | **[RUN: P3]** |
| 2 | **500 responses lose the correlation id.** The middleware resets the contextvar *before* building the error body, so `correlation_id` is `"-"` (and header `X-Correlation-ID: -`), defeating the headline "trace any failure" feature exactly when it is needed. | **[RUN: P1]** |
| 3 | **PostgreSQL-only 500s from unvalidated lengths.** Schemas do not bound `artifact_uri`, `framework`, `algorithm`, `training_data_ref`, and the client-supplied `X-Correlation-ID` is stored in `String(64)` columns unsanitized. A 100-char header, 600-char URI, or 80-char framework → **HTTP 500 on PostgreSQL** (SQLite hides it). `metadata` accepts 5 MB. | **[RUN on PG16]** |
| 4 | Worker maps `STAGE_NOT_DEPLOYABLE` to `approval_validation_failed` — misleading failure reason/metrics. | **[INSPECT]** |
| 5 | `/ready` couples every API pod to DB health (all pods go unready together on a DB blip) — acceptable but should be a conscious choice. | **[INSPECT]** |
| 6 | `worker._stop`/`_loop` accessed as private from `run.py`; `run.py` has 0% coverage and is the K8s worker entrypoint. | **[RUN: coverage]** |
| 7 | N+1 on `GET /models`: 22 SQL statements for 20 models. Contradicts the author's own code-review checklist and Q1. | **[RUN: PN]** |
| 8 | `metrics.py` counters are per-process and lost on restart; `http_requests_total` has no duration histogram. | **[INSPECT]** |
| 9 | Dependencies are `>=` ranges with no backend lockfile; no `.dockerignore` (frontend `COPY frontend/ .` would pull a local `node_modules` into the build context). | **[INSPECT]** |

**Repository/standards recommendation:** add `Makefile`, `.github/workflows/ci.yml`, `.dockerignore`, pinned `requirements.lock`, `import-linter` contracts (promised in ADR-001).

---

## 7. Testing Lead assessment (independent quality authority)

**What is solid:** 50 backend tests (20 unit / 22 API / 8 integration) all pass, 93% coverage; counts in `test-strategy.md` are accurate **[RUN]**. Angular: 10/10 pass, production build succeeds **[RUN]**. The e2e scenario test covers the rollback refusal matrix well.

**What the tests do NOT prove (verdict: accept as assignment, reject as production evidence)**
1. **Everything runs on SQLite.** The claim "PostgreSQL behaviour verified by design and migration" is weaker than stated; I found three PG-only failures in minutes (F5). There is no PG job.
2. **Concurrency tests are thin.** One test, same idempotency key. Distinct-key bursts on PG produced correct outcomes (1 row) but 2 of 16 callers got `409` instead of a replay **[RUN]** — acceptable, but untested and undocumented. `test_worker_claim_is_exclusive` is sequential, not parallel.
3. **No tests for:** stuck `VALIDATING`, stale retry, revoking approval after staging, worker thread lifecycle / `/ready` with a live worker (fixture disables it), `run.py`, oversized input, long headers, 500-path correlation id, N+1.
4. **No load, soak, chaos (kill worker mid-deploy), or migration-rollback tests.** The docs list these as future.
5. **CI is claimed but absent (F1)** — coverage gate 85%, pip-audit, npm audit, docker build therefore are unverified promises.
6. **UI:** no browser E2E, no accessibility tests; specs cover the service, interceptors, state component and deployments page only (model-detail, monitoring, timeline untested).

**AI/LLM testing:** not applicable (no AI components).

**Production-readiness criteria Testing Lead would require:** PG-backed CI matrix, property tests for the FSM, crash-injection tests (kill between claim/DEPLOYING/commit), parallel-claim test with 2 workers, contract test generated from `openapi.json`, Playwright happy path + error state.

**Gate 5:** **conditional** for assignment; **rejected** for production.

---

## 8. Senior Engineer #1 (implementation perspective)

The implementation is coherent and I would merge it with small changes. Priorities I'd do first, in order:
1. Add `VALIDATING` to the reconciler (same stuck-age rule, but *re-queue* to `REQUESTED` rather than fail — nothing external happened yet).
2. Guard retry/redeploy with a supersession check: refuse if a newer deployment for the same (model, env) has SUCCEEDED, unless an explicit `force_downgrade` flag + approver role.
3. Validate lengths in Pydantic (mirror DB column sizes), sanitize/limit `X-Correlation-ID` to `[A-Za-z0-9-]{1,64}` else regenerate; fix the middleware ordering so the 500 body reads the id before reset.
4. Flip insecure defaults (see F6).

## 9. Senior Engineer #2 (independent challenge)

I **disagree on two points** with #1:
- **Re-queue vs fail for stuck VALIDATING.** Re-queue is fine *only* because validation is side-effect free. I'd still cap re-queues (e.g. 3) and emit an alertable event, otherwise a poison row loops forever.
- **Supersession rule.** A version-ordering check is fragile (semver ≠ time of intent; rollbacks deliberately deploy lower versions). Safer rule: a retry is allowed only if *no other deployment for the same (model, env) has been created after this one* (compare `created_at`/sequence), which is ordering-agnostic and explains itself in the error.

Extra issue #1 missed: the "live" definition. I'd replace `ORDER BY updated_at DESC` with an explicit `environment_state(model_id, environment, live_deployment_id, version)` table updated in the same transaction as the deployment result. It removes clock-skew risk, removes the stage-vs-deployment ambiguity, makes rollback targets O(1), and gives seeding a single place to express "compressor 1.0.0 is live in prod".

**Disagreement recorded in §13 (D3).**

## 10. Associate Developer assessment

- Unclear requirement: what should happen to `stage` when the same version is deployed to several environments (stage is a single column)?
- Missing detail: how a worker gets a *real* runtime (no config switch; `run.py` hardcodes `SimulatedRuntime`).
- Missing detail: what `simulate_failure` is for in production builds — it is part of the public request schema.
- Small fix I can take: add `maxLength` to Pydantic fields and a regression test per field; add `archive` to the UI action list (API supports it, UI omits it).

## 11. Associate Tester assessment

Missing test cases / edge cases (each verified against behaviour where noted):
- Reject after STAGING/PRODUCTION → `409 INVALID_TRANSITION`; approval cannot be revoked once a version is promoted **[RUN: P4]**.
- Reject an `ARCHIVED` prior-prod version that is still a rollback target → `409` **[RUN: P5]**.
- Redeploy a just-rolled-back "bad" version to production with no re-approval → `202 … SUCCEEDED` **[RUN: P5]**.
- Request without any headers performs admin-only reads (`/audit` → 200) **[RUN: P6]**.
- `simulate_failure=permanent` accepted on a production deployment **[RUN: P9]**.
- `X-Correlation-ID` > 64 chars on a mutating call (PG).
- Two retries clicked in quick succession (second gets `NOT_RETRYABLE` toast; retry has no idempotency).
- Idempotency key reuse after the user edits the form on the model-detail page (key persists until success).
- UI: Retry button on Deployments page has no in-flight disable (model-detail page does).
Testability issue: the reconciler and claim logic are tested by editing rows directly, not by real interleavings.

---

## 12. Findings register (prioritised)

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| F1 | **High** | `Makefile` and `.github/workflows/ci.yml` are referenced by README, test-strategy, plan and the submission email (`make test`) but **do not exist**. CI claims (ruff, 85% gate, pip-audit, npm audit, docker build) are unbacked. | [INSPECT: folder listing] |
| F2 | **High** | Stuck `VALIDATING` rows are never reconciled and permanently block the (model, env). | [RUN] |
| F3 | **High** | Stale retry can overwrite a newer live version (downgrade). | [RUN] |
| F4 | **High** | Governance holes: approval cannot be revoked after promotion; superseded (ARCHIVED) versions cannot be withdrawn and remain rollback-eligible; a rolled-back version can be redeployed to prod without re-approval; no four-eyes. | [RUN] |
| F5 | **High (PG)** | Unbounded inputs → HTTP 500 on PostgreSQL (long `X-Correlation-ID`, `artifact_uri`, `framework`, `algorithm`); 5 MB metadata accepted. | [RUN on PG16] |
| F6 | **High** | Fail-open defaults: `DEFAULT_ROLE=admin` (no headers ⇒ admin; compose publishes :8000 with that default), `SEED_ON_STARTUP=true` default, `simulate_failure` in prod API, unauthenticated `/metrics`, client-forgeable `X-Role`/`X-User`. Documented as stand-in, but the *default* should fail closed. | [RUN + INSPECT] |
| F7 | Med-High | Reconciliation claim > implementation: no token-lookup in the port, private-attr peeking, "unknown"=failed, no runtime timeout, worker hard-wired to simulator. | [INSPECT] |
| F8 | Med-High | Dual source of truth for live state; `updated_at` ordering; seed leaves two PRODUCTION versions and no rollback path. | [RUN + INSPECT] |
| F9 | Medium | Observability: worker metrics unreachable in K8s topology; no latency histogram though dashboards promise p50/p95/p99; blocked requests unaudited; per-replica counters. | [INSPECT] |
| F10 | Medium | Migration story contradicts Q8; `create_all` at startup; incomplete K8s manifest (no Services/frontend/securityContext/NetworkPolicy/worker probes). | [INSPECT] |
| F11 | Medium | Monitoring model: no staleness/freshness state — the screenshot shows **HEALTHY** with last successful inference Jul 30, 2026 (two months before today); status from a single latest point; `by_version` mixes staging+prod; global thresholds; `limit` applies across versions. | [INSPECT + screenshot] |
| F12 | Medium | N+1 on inventory (22 queries/20 models); UI hard-caps 200 rows with no pagination; Deployments page polls every 2.5 s forever (even hidden tab). | [RUN + INSPECT] |
| F13 | Medium | Worker is sequential with no per-call timeout (head-of-line blocking); 32-bit deployment ids; global idempotency keys. | [INSPECT] |
| F14 | Medium | No artifact integrity: diagram says "URI + checksum" but there is no checksum/immutability field; `https://` and `file://` URIs allowed with no allowlist. | [INSPECT] |
| F15 | Low-Med | Tests only on SQLite; no PG CI, no crash/chaos/load, `run.py` 0% covered. | [RUN] |
| F16 | Low | Frontend: hard-coded user `ashwin`; native `confirm()` for prod rollback (no typed confirmation/reason); charts plot by index (no time axis; misaligned across versions); toasts use `role="status"` for errors; network-error view shows `Correlation ID: -`. | [INSPECT + screenshot] |
| F17 | Low | Unpinned Python deps, no `.dockerignore`, nginx serves as root without security headers/CSP, `try_files` returns index.html for missing assets. | [INSPECT] |
| F18 | Low | Plant/site dimension absent; `VALIDATED` has no real validation. | [INSPECT] |

**Strengths worth keeping** (and proven): pure domain layer; fail-safe classification; DB-enforced single-active-deployment on PG; Alembic/PG parity; correct idempotency under 16-way parallel PG requests; honest `known-limitations.md`; ADR discipline; 10/10 acceptance flow demonstrable end-to-end; all 4 UI states implemented.

---

## 13. Cross-functional debate (disagreements documented, not forced)

**D1 — Product Director vs Architecture Director: fix scope before submitting**
- *Position A (PD):* The assignment's value is the vertical slice and the docs; ship now, list F2–F8 as known limitations.
- *Position B (AD):* F2/F3 are correctness bugs in the headline G12 feature (async workflow + rollback safety) and cost < 1 day; a reviewer who probes them will penalise the claim "crash-safe".
- *Trade-off:* time vs credibility of the "crash-safe / rollback-safe" claims.
- **Recommendation:** fix F1, F2, F3, F5(part), F6 defaults (≈1 day); document the rest.

**D2 — Architecture Director vs Technical Lead: DB queue vs broker, in-process vs separate worker**
- *A:* Keep DB-claimed queue; add `SKIP LOCKED` (ADR-002 follow-up). *B:* Adopt a broker/Arq now for timeouts and concurrency.
- *Trade-off:* ops simplicity vs throughput. **Decision required** only when expected deploys/min is known; current evidence favours A.

**D3 — Senior Engineer #1 vs #2: how to decide "stale"; and whether to store `stage` at all**
- *#1:* version/ordering check + keep `stage` column. *#2:* "no newer deployment exists" rule + `environment_state` table, derive stage per environment.
- *Trade-off:* smaller change vs removing a class of inconsistency (F8).
- **Recommendation:** #2 for the stale rule now (tiny), `environment_state` as a roadmap ADR.

**D4 — Technical Architect vs Testing Lead: testability/observability of failure paths**
- TA: reconciler tested by row edits is sufficient at this stage. TL: real interleavings (kill between claim and commit) are the only evidence for "crash-safe".
- **Decision:** add one crash-injection test per state (VALIDATING, DEPLOYING-before-commit, DEPLOYING-after-runtime) and a PG CI job.

**D5 — Product Manager vs Engineering: scope of governance**
- PM wants re-approval after rollback + withdraw capability + four-eyes; Engineering notes that adds states (`WITHDRAWN`/`BLOCKED`). Recommend one new terminal-ish state `WITHDRAWN` (excluded from deploy *and* rollback), allowed from any stage except live PRODUCTION.

---

## 14. Consolidated recommendation

**Verdict.** A high-quality, honest, largely correct G12 submission with excellent documentation and a credible architecture narrative. Its weaknesses are concentrated in (a) documentation/CI claims that don't match the folder, (b) a few real correctness gaps in the failure-handling paths it markets as strengths, and (c) fail-open defaults. All are fixable in about 1–2 days. It should not be presented as production-ready.

**Quality gates**

| Gate | Result |
|---|---|
| 1 Product | Pass |
| 2 Architecture | Conditional (F7, F8, F10) |
| 3 Development approval | Pass |
| 4 Code review | Conditional (F2, F3, F5) |
| 5 Testing | Conditional for assignment / Reject for production |
| 6 Production readiness | **Fail** (auth, PG CI, observability topology, K8s completeness, DR) |

---

## 15. Action plan

**P0 — before submitting (≈1 day)**
1. Add `Makefile` (`install`, `run`, `test`, `test-backend`, `test-frontend`) and `.github/workflows/ci.yml` (ruff, pytest `--cov-fail-under=85`, pip-audit, `npm ci`/test/build, npm audit, docker build); or delete every claim that mentions them. Fill `<add URL>`/`<hours>` in the submission email.
2. Reconciler: handle `VALIDATING` (re-queue, capped, with event + metric). Add crash-injection test.
3. Retry guard: refuse when a newer deployment exists for the same (model, env) (`STALE_RETRY`). Test with the P3 scenario.
4. Middleware: capture `cid` before `reset`; sanitise inbound `X-Correlation-ID` (`^[A-Za-z0-9._-]{1,64}$`, else generate).
5. Pydantic limits mirroring column sizes; cap `metadata` size and list lengths; add PG regression tests.
6. Fail-closed defaults: `DEFAULT_ROLE=viewer` (dev override in `.env.example`), `SEED_ON_STARTUP=false` outside dev, hide/gate `simulate_failure` behind a `ENABLE_FAILURE_SIMULATION` flag.
7. Align docs with reality: K8s claims, Q8 (make the Dockerfile CMD not migrate, or add a `MIGRATE_ON_START` flag default off and set it in compose only), dashboards that need unavailable metrics.

**P1 — next sprint**
Audit blocked attempts (403/409 gates) as `*.denied` events; `WITHDRAWN` state + re-approval after rollback; four-eyes (`approved_by != created_by`); `environment_state` table; add `lookup(token)` to `ModelRuntime`; per-call timeout + worker concurrency; expose worker metrics (tiny HTTP server or push to a gateway) and a request-duration histogram; complete K8s manifests (Services, frontend, securityContext, NetworkPolicy, probes); PG CI matrix.

**P2 — later**
Monitoring freshness state + windowed status + per-env grouping; per-model thresholds; pagination/virtual scroll; artifact checksum + bucket allowlist; OIDC; plant/site dimension; OpenTelemetry; generated TS client with contract tests; Playwright.

---

## FINAL ENGINEERING REVIEW

1. **Problem Understanding** — Governed ML lifecycle control plane across environments; plant dimension missing.
2. **Product Assessment** — Strong scope judgement; GO for assignment after P0.
3. **Requirements** — 10/10 scenarios met; gaps: audit of blocked attempts, non-archived rollback rule, plants, validation gate.
4. **Architecture Assessment** — Proportionate; claims > implementation in reconciliation, live-state truth, migrations, metrics exposure.
5. **Recommended Architecture** — Keep modular monolith + DB queue; add `environment_state`, runtime-token lookup, separate worker with its own metrics port.
6. **Cloud Architecture** — Managed Postgres, stateless API (HPA/PDB), separate worker Deployment, migrate Job, Services + Ingress; add NetworkPolicy, securityContext, secrets store. Cloud/provider undecided (AWS/Azure/GCP comparison needed once runtime chosen).
7. **Technology Stack** — Python 3.12/FastAPI/Pydantic v2/SQLAlchemy 2/Alembic/Postgres 16; Angular 20 standalone + signals; Docker/Compose; all appropriate.
8. **Data Architecture** — Sound relational model with DB invariants; metrics need partitioning/rollups; add `environment_state`, checksum, plant dimension.
9. **AI/ML Architecture** — Control plane only; no AI. Future: drift-triggered hooks, canary analysis (roadmap).
10. **Security Architecture** — RBAC in services (good); identity fail-open by default; no authN; forgeable headers; unauthenticated `/metrics`; artifact URI allowlist missing; no four-eyes; audit omits denied actions.
11. **DevOps / CI-CD** — **CI and Makefile absent**; unpinned deps; migration-on-start; no image scanning evidence.
12. **Observability** — Good correlation/logging design; 500 path loses the id; worker metrics unreachable; no histograms; dashboards ahead of metrics.
13. **Testing Strategy** — Good base (50+10 passing, 93%); add PG CI, crash-injection, parallel-claim, contract, E2E.
14. **Implementation Strategy** — P0 list above, then P1/P2.
15. **Delivery Roadmap** — Phase 0 (P0 fixes) → Phase 1 foundation (PG CI, real auth, K8s complete) → Phase 2 MVP-prod (runtime adapter, metrics ingest) → Phase 3 production (SLOs, DR) → Phase 4 scale (broker/partitioning) → Phase 5 optimisation (canary, policy-as-code).
16. **Team Responsibility Matrix** — Product Director: scope/plants/go-no-go. PM: governance stories (WITHDRAWN, four-eyes, audit-denied). Arch Director: live-state ADR, runtime port contract. Architect: `environment_state`, K8s. Tech Lead: P0 fixes, CI, pinning. Testing Lead: PG CI, crash tests, gates. Sr Eng #1/#2: F2/F3 and reconciler/timeouts. Assoc Dev: input limits, UI fixes. Assoc Tester: regression tests listed in §11.
17. **Risks** — Product: plants missing. Architecture: dual truth. Security: fail-open defaults. Engineering: stuck states. Data: seed inconsistency. Operational: unreachable worker metrics, migration races. Cost: metric volume.
18. **Dependencies** — Runtime choice, IdP, cloud provider, artifact store with checksums, ingest source for metrics.
19. **Cost Considerations (estimates)** — Assignment: negligible. Pilot on managed cloud: small (a few tens of $/month for Postgres + 2–3 small pods, **estimate**); dominant future cost is metrics storage/egress, not compute.
20. **KPIs** — Lead time REQUESTED→SUCCEEDED; failure rate by class; stuck-deployment count (target 0); reconciliation count; rollback rate; p95 API latency (needs histogram); coverage ≥ 85% on PG CI; % prod deploys with staging evidence = 100%.
21. **Open Questions** — Original rubric weights? Plant model? Runtime/cloud? Metric volume/retention? Is `make test` required for grading? Who may withdraw/force a downgrade?
22. **Architecture Decisions** — Accept ADR-001..004; add ADR-005 live-state source of truth, ADR-006 identity, ADR-007 worker observability, ADR-008 runtime port contract.
23. **Points of Disagreement** — D1–D5 above (unresolved: D2 pending throughput data; D3 pending decision).
24. **Recommended Next Actions** — Execute P0 (≈1 day), turn the probes into permanent regression tests, add PG CI, then re-submit with the docs aligned.

*Nothing was written to your folder. Probe scripts live only in the session scratch area and can be turned into permanent tests on request.*
