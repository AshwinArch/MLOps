# API design

Interactive docs: `http://localhost:8000/docs` (Swagger) and `/redoc`; raw contract at `/openapi.json`.

| Method | Path | Purpose | Success | Notable errors |
|---|---|---|---|---|
| POST | `/models` | Register model | 201 | 409 `MODEL_EXISTS`, 422 |
| GET | `/models` | Inventory (`q, framework, stage, owner, limit, offset`) | 200 | |
| GET | `/models/{id}` | Model + versions | 200 | 404 `MODEL_NOT_FOUND` |
| POST | `/models/{id}/versions` | Register version | 201 | 409 `VERSION_EXISTS`, 422 |
| GET | `/models/{id}/versions` | List versions | 200 | |
| POST | `/models/{id}/versions/{v}/transitions` | `validate / approve / reject / archive / withdraw` | 200 | 409 `INVALID_TRANSITION`, `SELF_APPROVAL`, `VALIDATION_INCOMPLETE`; 403 |
| POST | `/deployments` | Request deployment (async) | 202 (200 replay) | 409 `VERSION_NOT_APPROVED`, `PROMOTION_REQUIRES_STAGING`, `DEPLOYMENT_IN_PROGRESS`, `ALREADY_DEPLOYED`, `IDEMPOTENCY_KEY_REUSED`, 403 `SIMULATION_DISABLED`-style guard for simulated failures; 403 |
| GET | `/deployments` | List (`model_id, environment, status, q`) | 200 | |
| GET | `/deployments/{id}` | Detail incl. `retryable`, `rollbackable` hints | 200 | 404 |
| GET | `/deployments/{id}/events` | Deployment history | 200 | |
| POST | `/deployments/{id}/retry` | Retry transient failure | 202 | 409 `NOT_RETRYABLE`, `RETRY_LIMIT_REACHED`, `STALE_RETRY` (a newer deployment exists) |
| POST | `/deployments/{id}/rollback` | Roll back to previous version | 202 (200 replay) | 409 `STALE_ROLLBACK`, `ROLLBACK_TARGET_MISSING`, `ROLLBACK_TARGET_UNSAFE`, `ROLLBACK_OF_ROLLBACK` |
| GET | `/events` | Global timeline (`model_id`) | 200 | |
| GET | `/models/{id}/metrics` | Series + summary + per-version comparison (`version, environment, since, limit`) | 200 | 404 |
| GET | `/audit` | Audit trail (approver+) | 200 | 403 |
| GET | `/health`, `/ready`, `/metrics` | Ops | 200 / 503 | |

## Conventions
- **Error envelope** on every failure: `{"error": {"code", "message", "details", "correlation_id"}}`.
- **Idempotency**: `Idempotency-Key` header on `POST /deployments`; replay returns `200` + `Idempotent-Replay: true`.
- **Identity** (dev): `X-User`, `X-Role`. **Tracing**: `X-Correlation-ID`.
- Version registration: `artifact_checksum` (`sha256:<64 hex>`) and artifact URI allowlist (`ARTIFACT_URI_PREFIXES`, error `ARTIFACT_STORE_NOT_ALLOWED`); input sizes are bounded.
- Authentication: in `AUTH_MODE=jwt` a missing/invalid bearer token gives `401 UNAUTHENTICATED` on every route except `/health` and `/ready`. Other new codes: `VERSION_IN_USE` (409, version is being deployed), failure reason `approval_revoked_during_deploy` (permanent), `artifact_checksum_missing/mismatch`.
- Identity headers (dev mode only) are normalised (trim + casefold) before the four-eyes comparison. Production deploys need `artifact_checksum` (`artifact_checksum_missing`, permanent) and the runtime must confirm it (`artifact_checksum_mismatch`, permanent).
- Idempotency keys are scoped per requesting user.
- Pagination `{items,total,limit,offset}`; bounded `limit`.
- Versioning: additive changes only; breaking changes under `/v2`.
