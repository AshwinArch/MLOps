# Code-review checklist
- [ ] Domain rules live in `app/domain` (pure) and have unit tests, including the illegal path.
- [ ] New endpoint: typed request/response, validation, error codes documented, RBAC enforced in the service.
- [ ] State change writes event + audit in the **same transaction**; correlation id present.
- [ ] Idempotent or safely repeatable? What happens on retry, double click, concurrent request?
- [ ] Failure path classified (TRANSIENT / PERMANENT / RECONCILIATION); no silent `except`.
- [ ] External call: intent recorded first, idempotency token, timeout, reconciliation story.
- [ ] Migration is expand/contract, backward compatible, reviewed separately.
- [ ] No N+1 on list endpoints; pagination bounded; indexes for new filters.
- [ ] Logs structured, no secrets/PII; metrics added for new failure modes.
- [ ] Angular: loading / empty / error / success states; errors show code + correlation id; no direct URL strings outside `ApiService`.
- [ ] Tests: unit + API + (integration when touching worker/DB); CI green; docs/ADR updated.

# Production-readiness checklist
- [ ] Real authn/z (OIDC), roles mapped, `DEFAULT_ROLE` removed, audit reviewed
- [ ] Secrets in secret store, `.env` not in repo, DB not publicly exposed, TLS at ingress
- [ ] Postgres managed, backups + restore tested, migrations via pre-upgrade Job, rollback plan
- [ ] API >= 3 replicas, PDB, HPA, readiness/liveness wired; workers scaled separately
- [ ] SLOs defined (API availability, deployment lead time), alerts routed, runbooks for stuck deployments and reconciliation failures
- [ ] Dashboards live (see `observability.md`); logs shipped; traces enabled
- [ ] Load test at 10x expected; chaos test (kill worker mid-deploy, DB failover)
- [ ] Dependency and image scanning clean; SBOM; license check
- [ ] Data retention and partitioning for metrics/events/audit
- [ ] Rate limits, request size limits, CORS restricted to known origins
- [ ] Disaster recovery (RPO/RTO) agreed; on-call trained
