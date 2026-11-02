"""Request-scoped dependencies: the signed-in user and server-side permission checks (SECURITY_MODEL §8)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.core.audit import AuditWriter
from app.core.csrf import CSRF_HEADER, MUTATING_METHODS, SESSION_COOKIE, tokens_match
from app.core.errors import AppError
from app.core.ratelimit import FixedWindowLimiter
from app.core.rbac import ALL_PERMISSIONS, GLOBAL_ONLY_PERMISSIONS, RoleGrant, has_permission
from app.models.identity import Session, User
from app.services.auth import Sessions, resolve_session
from app.services.health import Dependencies
from app.services.projects import Actor, authorize


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: Session
    grants: list[RoleGrant]


def dependencies(request: Request) -> Dependencies:
    return request.app.state.dependencies


def db_sessions(request: Request) -> Sessions:
    return dependencies(request).sessions


def audit_writer(request: Request) -> AuditWriter:
    return AuditWriter(request.app.state.redactor)


def rate_limiter(request: Request) -> FixedWindowLimiter:
    return FixedWindowLimiter(dependencies(request).redis)


def client_ip(request: Request) -> str | None:
    # Proxy headers are trusted only through uvicorn --forwarded-allow-ips (spec decision #7).
    return request.client.host if request.client else None


SessionsDep = Annotated[Sessions, Depends(db_sessions)]
AuditDep = Annotated[AuditWriter, Depends(audit_writer)]
LimiterDep = Annotated[FixedWindowLimiter, Depends(rate_limiter)]
ClientIp = Annotated[str | None, Depends(client_ip)]


async def current_user(request: Request, sessions: SessionsDep) -> AuthContext:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AppError("UNAUTHENTICATED", "Authentication required", http_status=401)
    user, session, grants = await resolve_session(sessions, request.app.state.settings, token)
    # The middleware already checked header == cookie; a pre-session cookie must not authorise a session.
    if request.method in MUTATING_METHODS and not tokens_match(
        session.csrf_token, request.headers.get(CSRF_HEADER)
    ):
        raise AppError("CSRF_FAILED", "CSRF token missing or invalid", http_status=403)
    return AuthContext(user, session, grants)


CurrentUser = Annotated[AuthContext, Depends(current_user)]


def current_actor(ctx: CurrentUser, ip: ClientIp) -> Actor:
    """The signed-in user as the services see them; the IP goes into every audit event."""
    return Actor(ctx.user.id, ctx.user.email, ctx.user.display_name, tuple(ctx.grants), ip=ip)


CurrentActor = Annotated[Actor, Depends(current_actor)]


def require_project_permission(permission: str) -> Callable[..., Awaitable[Actor]]:
    """First door of a ``/projects/{project_id}`` route (SECURITY_MODEL §8, T6).

    404 when the project is outside the user's scope (its existence never leaks), 403 inside the scope
    without the permission. The service decides again with the same ``authorize``.
    """
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")
    if permission in GLOBAL_ONLY_PERMISSIONS:
        raise ValueError(f"{permission!r} is a global permission; use require_permission")

    async def guard(project_id: uuid.UUID, actor: CurrentActor, sessions: SessionsDep) -> Actor:
        async with sessions() as session:
            await authorize(session, actor, project_id, permission)
        return actor

    return guard


def require_permission(permission: str) -> Callable[..., Awaitable[AuthContext]]:
    """Guard for global-only permissions. Project permissions: ``require_project_permission``."""
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")
    if permission not in GLOBAL_ONLY_PERMISSIONS:
        raise ValueError(f"{permission!r} is a project permission; use require_project_permission")

    async def guard(ctx: CurrentUser) -> AuthContext:
        if not has_permission(ctx.grants, permission, user_id=ctx.user.id):
            raise AppError("FORBIDDEN", "Not allowed", http_status=403)
        return ctx

    return guard
