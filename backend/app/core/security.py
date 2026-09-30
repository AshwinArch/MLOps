"""Role-based authorisation. Header-based stand-in for JWT/OIDC claims (see docs/architecture.md#security)."""
from dataclasses import dataclass

from app.core.errors import ForbiddenError
from app.domain.enums import Role

_RANK = {Role.VIEWER: 0, Role.ENGINEER: 1, Role.APPROVER: 2, Role.ADMIN: 3}


@dataclass(frozen=True)
class Actor:
    user: str
    role: Role

    def require(self, minimum: Role, action: str = "perform this action") -> None:
        if _RANK[self.role] < _RANK[minimum]:
            raise ForbiddenError(
                f"Role '{self.role.value}' may not {action}; requires '{minimum.value}' or higher",
                details={"role": self.role.value, "required": minimum.value},
            )


SYSTEM = Actor("system", Role.ADMIN)
