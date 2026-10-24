"""Server-side RBAC (API_CONTRACTS §2, FR-003). Pure logic: callers load grants and project ownership.

Scope rules:
- a ``project:<id>`` grant applies only to that project;
- a ``global`` grant applies to projects the user owns and to projects where the user also holds a role;
- the project owner is always ``owner`` of that project;
- global-only permissions come only from ``global`` grants and only outside a project context.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from app.models.enums import ROLES

_PROJECT_SCOPE = re.compile(r"^project:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")

GLOBAL_ONLY_PERMISSIONS = frozenset(
    {"admin:users", "admin:policies", "settings:providers", "audit:read", "usage:read_all"}
)
_READONLY = frozenset({"project:read", "workspace:read", "task:read", "review:read", "git:read"})
_REVIEWER = _READONLY | {"review:write", "approval:decide"}
_DEVELOPER = _REVIEWER | {
    "project:write",
    "task:create",
    "task:control",
    "workspace:write",
    "test:run",
    "git:branch",
}
ALL_PERMISSIONS = (
    _DEVELOPER | GLOBAL_ONLY_PERMISSIONS | {"project:delete", "deploy:production", "owner:transfer"}
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": ALL_PERMISSIONS,
    "admin": ALL_PERMISSIONS - {"owner:transfer"},
    "developer": _DEVELOPER,
    "reviewer": _REVIEWER,
    "readonly": _READONLY,
}


@dataclass(frozen=True)
class RoleGrant:
    role: str
    scope: str  # "global" or "project:<uuid>"

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")
        if self.scope != "global" and not _PROJECT_SCOPE.match(self.scope):
            raise ValueError("scope must be 'global' or 'project:<uuid>'")

    @property
    def project_id(self) -> uuid.UUID | None:
        match = _PROJECT_SCOPE.match(self.scope)
        return uuid.UUID(match.group(1)) if match else None


def effective_permissions(
    grants: Iterable[RoleGrant],
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    project_owner_id: uuid.UUID | None = None,
) -> frozenset[str]:
    grants = list(grants)
    if project_id is None:
        perms: set[str] = set()
        for grant in grants:
            if grant.scope == "global":
                perms |= ROLE_PERMISSIONS[grant.role]
        return frozenset(perms)

    is_owner = project_owner_id is not None and project_owner_id == user_id
    project_grants = [g for g in grants if g.project_id == project_id]
    roles = {g.role for g in project_grants}
    if is_owner:
        roles.add("owner")
    if is_owner or project_grants:
        roles |= {g.role for g in grants if g.scope == "global"}
    perms = set()
    for role in roles:
        perms |= ROLE_PERMISSIONS[role]
    return frozenset(perms - GLOBAL_ONLY_PERMISSIONS)


def has_permission(
    grants: Iterable[RoleGrant],
    permission: str,
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    project_owner_id: uuid.UUID | None = None,
) -> bool:
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")
    return permission in effective_permissions(
        grants, user_id=user_id, project_id=project_id, project_owner_id=project_owner_id
    )
