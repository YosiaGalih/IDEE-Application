"""Request-scoped dependencies: the signed-in user and server-side permission checks (SECURITY_MODEL §8)."""

from __future__ import annotations

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


def require_permission(permission: str) -> Callable[..., Awaitable[AuthContext]]:
    """Guard for global-only permissions. Project permissions need the project id (WP-2.1)."""
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")
    if permission not in GLOBAL_ONLY_PERMISSIONS:
        raise ValueError(f"{permission!r} is a project permission; check it with the project id")

    async def guard(ctx: CurrentUser) -> AuthContext:
        if not has_permission(ctx.grants, permission, user_id=ctx.user.id):
            raise AppError("FORBIDDEN", "Not allowed", http_status=403)
        return ctx

    return guard
