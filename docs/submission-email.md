Subject: MLOps Technical Assignment Submission - G12

Hi Team,

Role level:
G12

GitHub repository:
<add URL after pushing this folder>

Architecture summary:
Modular monolith (FastAPI domain/services/api) with an asynchronous deployment worker (atomic claim, idempotent runtime port, reconciler), PostgreSQL with DB-enforced single active deployment per model/environment, RBAC + audit, correlation-id logging and Prometheus metrics, Angular SPA isolated behind /api. ADRs, scaling answers, K8s view, risk register and roadmap are in docs/.

Run command:
docker compose up --build   (UI :4200, API docs :8000/docs)

Test command:
make test   (50 backend tests at 92% coverage, 10 Angular tests)

Demo:
scripts/demo.sh and docs/screenshots

Known limitations:
Header-based auth stand-in for OIDC, simulated runtime, seeded (not ingested) metrics. See docs/known-limitations.md.

Estimated effort:
<hours>

Regards,
<Your name>
