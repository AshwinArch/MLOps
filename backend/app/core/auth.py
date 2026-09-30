"""Bearer-token (JWT/OIDC) validation. Identity = `sub`; role comes from a configurable claim."""
import jwt
from jwt import PyJWKClient

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.security import Actor
from app.domain.enums import Role

_jwks: dict[str, PyJWKClient] = {}


def _key(settings: Settings, token: str):
    if settings.jwt_jwks_url:
        client = _jwks.setdefault(settings.jwt_jwks_url, PyJWKClient(settings.jwt_jwks_url, cache_keys=True))
        return client.get_signing_key_from_jwt(token).key
    return settings.jwt_secret


def actor_from_bearer(settings: Settings, authorization: str | None) -> Actor:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing bearer token")
    token = authorization[7:].strip()
    try:
        algs = ["RS256", "ES256"] if settings.jwt_jwks_url else ["HS256"]
        opts = {"require": ["exp", "sub"], "verify_aud": bool(settings.jwt_audience)}
        claims = jwt.decode(token, _key(settings, token), algorithms=algs, issuer=settings.jwt_issuer or None,
                            audience=settings.jwt_audience or None, options=opts)
    except Exception:  # signature, expiry, issuer, audience, malformed: all the same answer, no detail leaked
        raise UnauthorizedError("Invalid or expired token") from None
    raw = claims.get(settings.jwt_role_claim)
    raw = raw[0] if isinstance(raw, list) and raw else raw
    try:
        role = Role(str(raw).lower())
    except ValueError:
        role = Role.VIEWER  # unknown/missing role claim => least privilege
    return Actor(user=str(claims["sub"]).strip().casefold()[:64], role=role)
