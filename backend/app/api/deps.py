from collections.abc import Iterator

from fastapi import Header, Request
from sqlalchemy.orm import Session

from app.core.auth import actor_from_bearer
from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.core.security import Actor
from app.domain.enums import Role


def get_session(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def normalise_user(raw: str | None) -> str:
    """Identity comparison must not depend on case/whitespace (four-eyes bypass otherwise)."""
    return (raw or "").strip().casefold()[:64] or "anonymous"


def get_actor(request: Request, x_user: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None)) -> Actor:
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "jwt":  # header identity is ignored entirely in jwt mode
        return actor_from_bearer(settings, authorization)
    raw = (x_role or settings.default_role).lower()
    try:
        role = Role(raw)
    except ValueError:
        raise ValidationFailed(f"Unknown role '{raw}'", details={"allowed": [r.value for r in Role]}) from None
    return Actor(user=normalise_user(x_user), role=role)


SessionDep = Session
