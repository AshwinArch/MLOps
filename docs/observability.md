# Observability

## What exists
- **Structured logs** (JSON, one line per event) with `correlation_id`; HTTP access log includes method, path, status, duration.
- **Correlation IDs**: `X-Correlation-ID` accepted or generated per request, echoed in the response, stored on deployment events and audit rows, shown in UI error toasts; workers use `worker-<deployment id>` / `reconcile-<id>`.
- **Metrics** (`GET /metrics`, Prometheus text): `http_requests_total{method,status}`, `deployments_requested_total{environment}`, `deployments_succeeded_total`, `deployments_failed_total{failure_class,reason}`, `deployments_retried_total`, `deployments_idempotent_replays_total`, `rollbacks_requested_total`.
- **Latency histogram** `http_request_duration_seconds` (buckets, `_sum`, `_count`). Inbound correlation ids are sanitised (`^[A-Za-z0-9._-]{1,64}$`, else regenerated); 500 bodies keep the id.
- **Worker metrics**: the standalone worker (`python -m app.workers.run`) serves its own `/metrics` on port `WORKER_METRICS_PORT` (9100).
- **Monitoring status**: worst of the last 3 points; **STALE** when the newest point is older than `STALE_METRICS_HOURS` (48); `data_age_hours` is returned.
- **Health**: `/health` liveness, `/ready` readiness (DB + worker alive).
- **Failure classification**: `TRANSIENT` / `PERMANENT` / `RECONCILIATION` stored on the deployment and labelled on the failure counter.

## Operational dashboard proposal (Grafana)
1. *Platform health*: request rate, p50/p95/p99 latency, 5xx ratio, readiness flaps.
2. *Deployment flow*: requested vs succeeded vs failed per environment, **time in each state** (REQUESTED->SUCCEEDED lead time), queue depth (`count(status='REQUESTED')`), age of oldest active deployment.
3. *Failure analysis*: failures by `failure_class` and `reason`, retry success rate, reconciliation count.
4. *Governance*: approvals per day, time from VALIDATED to APPROVED, production deployments without staging (should be 0), rollbacks per week.
5. *Model health*: fleet heat-map of monitoring status per model/version, drift top-N, quality regression vs previous version.

## Alerts (initial)
| Alert | Condition | Severity |
|---|---|---|
| Stuck deployments | any `DEPLOYING` > 10 min | page |
| Reconciliation failures | `failure_class=RECONCILIATION` > 0 in 15 min | page |
| Queue backlog | oldest `REQUESTED` > 2 min | ticket |
| Error budget burn | API 5xx > 1% for 5 min | page |
| Production model critical | monitoring status CRITICAL > 15 min | page model owner |
| Permanent failure spike | `PERMANENT` failures > 5 / 10 min | ticket |

## OpenTelemetry (desirable, roadmap)
Add `opentelemetry-instrumentation-fastapi/sqlalchemy`; propagate W3C `traceparent` (correlation id becomes the trace id); one span per worker phase (validate, runtime call, finalise); export to an OTLP collector. The current correlation-id plumbing is the seam.
