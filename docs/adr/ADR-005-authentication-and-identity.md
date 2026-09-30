# ADR-005: Authentication and identity

## Context
The first version trusted `X-User` and `X-Role` headers. That is fine for a demo and unacceptable for production: anyone who reaches the API can approve and deploy, four-eyes is cosmetic, and the audit log names whoever the caller claims to be.

## Decision
Two modes behind one `get_actor` seam. `header` (default, dev/demo only) and `jwt`: every route except `/health` and `/ready` requires a bearer token validated for signature (HS256 secret or RS256/ES256 via JWKS), `exp`, `sub`, issuer and audience. Identity is the `sub` claim, role comes from a configurable claim and falls back to viewer. Header identity is ignored in jwt mode. `ENVIRONMENT=production` makes start-up fail unless jwt mode, a real runtime, PostgreSQL, a metrics token and the governance flags are set.

## Alternatives
- Gateway-only authentication (oauth2-proxy/ingress) with trusted headers: simpler, but the app can never prove who called it and a mis-routed request bypasses everything.
- Sessions and our own user store: duplicates the identity provider and adds credential storage.
- Per-route opt-in auth: already failed once (reads were anonymous); a router-level dependency makes exposure the exception.

## Consequences
Positive: identity and role are cryptographically bound to the caller; four-eyes compares stable subjects; reads are protected. Negative: needs an IdP, key rotation via JWKS cache, and the UI must obtain tokens (today a pasted token field only). Tested with locally signed tokens, not a real IdP.

## Risks and revisit trigger
Role claims are coarse: move to per-environment and per-model permissions (policy table or OPA) before multiple teams share production. Revisit when a second identity provider or service-to-service callers appear.
