# Delivery plan (team of 5: 3 backend/platform, 1 frontend, 1 SRE/QA; 10 weeks)

| Sprint | Goal | Exit criteria |
|---|---|---|
| 0 (wk 1) | Foundations | Repo, CI, environments, ADR-001..004 agreed, OpenAPI contract draft, definition of done |
| 1 (wk 2-3) | Registry + governance | Models/versions/lifecycle/RBAC/audit live in dev; Angular inventory + detail |
| 2 (wk 4-5) | Deployment pipeline | Async worker, idempotency, retry, rollback, reconciler; deployments UI; staging env |
| 3 (wk 6-7) | Real runtime + monitoring | First runtime adapter (KServe), metric ingest + rollups, monitoring dashboard, alerts |
| 4 (wk 8-9) | Hardening | OIDC, load test, chaos test of worker/DB, runbooks, security review, zero-downtime migration drill |
| 5 (wk 10) | Pilot + handover | 3 pilot models in production, SLOs published, on-call onboarding |

Working agreements: trunk-based with short PRs, required review by the module owner, contract tests on the OpenAPI spec, feature flags for promotion controls, demo every sprint. Tech lead: owns ADRs, review standards, cross-team interfaces, risk register.
