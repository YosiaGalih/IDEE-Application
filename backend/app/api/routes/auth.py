"""``/auth/*`` (API_CONTRACTS §2). Thin: validate, call ``services.auth``, set or clear cookies."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from app.api.deps import AuditDep, ClientIp, CurrentUser, LimiterDep, SessionsDep
from app.core.context import REQUEST_ID_HEADER
from app.core.csrf import CSRF_COOKIE, SESSION_COOKIE
from app.core.errors import envelope, request_id_of
from app.core.logging import get_logger
from app.core.rbac import effective_permissions
from app.models.identity import User
from app.services import auth
from app.services.users import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)
NewPassword = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    remember_me: bool = False


class ForgotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)


class ResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)
    new_password: str = NewPassword


class ChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = NewPassword


class UserOut(BaseModel):
    """The public ``User`` shape. Never carries the hash, lockout counters or tokens."""

    id: uuid.UUID
    email: str
    display_name: str
    status: str
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def of(cls, user: User) -> UserOut:
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


def ok(request: Request, data: dict[str, Any], *, background: BackgroundTask | None = None) -> JSONResponse:
    request_id = request_id_of(request)
    # Auth responses carry identity data and set credentials; no cache may keep them.
    return JSONResponse(
        envelope(request_id, status="completed", data=data),
        headers={REQUEST_ID_HEADER: request_id, "Cache-Control": "no-store"},
        background=background,
    )


@router.post("/login")
async def login(
    body: LoginIn, request: Request, sessions: SessionsDep, audit: AuditDep, limiter: LimiterDep, ip: ClientIp
) -> JSONResponse:
    settings = request.app.state.settings
    result = await auth.login(
        sessions,
        audit,
        limiter,
        settings,
        email=body.email,
        password=body.password,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
        previous_token=request.cookies.get(SESSION_COOKIE),
    )
    response = ok(request, {"user": UserOut.of(result.user).model_dump(mode="json")})
    # remember_me only makes the cookie outlive the browser session; the server-side limits are unchanged.
    max_age = int((result.expires_at - datetime.now(UTC)).total_seconds()) if body.remember_me else None
    for name, value, http_only in (
        (SESSION_COOKIE, result.session_token, True),
        (CSRF_COOKIE, result.csrf_token, False),
    ):
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path="/",
            secure=settings.cookie_secure,
            httponly=http_only,
            samesite="lax",
        )
    return response


@router.post("/logout")
async def logout(
    request: Request, ctx: CurrentUser, sessions: SessionsDep, audit: AuditDep, ip: ClientIp
) -> JSONResponse:
    await auth.logout(sessions, audit, session_id=ctx.session.id, user_id=ctx.user.id, ip=ip)
    response = ok(request, {})
    secure = request.app.state.settings.cookie_secure
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=False, samesite="lax")
    return response


@router.get("/me")
async def me(request: Request, ctx: CurrentUser) -> JSONResponse:
    permissions = effective_permissions(ctx.grants, user_id=ctx.user.id)
    return ok(
        request,
        {
            "user": UserOut.of(ctx.user).model_dump(mode="json"),
            "roles": [{"role": g.role, "scope": g.scope} for g in ctx.grants],
            "permissions": sorted(permissions),
        },
    )


@router.post("/password/forgot")
async def forgot_password(
    body: ForgotIn,
    request: Request,
    sessions: SessionsDep,
    audit: AuditDep,
    limiter: LimiterDep,
    ip: ClientIp,
) -> JSONResponse:
    await auth.enforce_rate_limit(limiter, f"kk:rl:forgot:{ip}")
    development = request.app.state.settings.app_env == "development"

    async def issue() -> None:
        # Runs after the response is sent, so known and unknown emails take the same time to answer.
        try:
            token = await auth.issue_password_reset(sessions, audit, email=body.email, ip=ip)
        except Exception:
            logger.exception("password_reset_issue_failed")
            return
        # Email delivery is P1. Only a development install prints the link; production never writes it.
        if token is not None and development:
            logger.info("password_reset_link", reset_path=f"/reset-password/{token}")

    return ok(request, {}, background=BackgroundTask(issue))


@router.post("/password/reset")
async def reset_password(
    body: ResetIn, request: Request, sessions: SessionsDep, audit: AuditDep, limiter: LimiterDep, ip: ClientIp
) -> JSONResponse:
    await auth.reset_password(
        sessions, audit, limiter, token=body.token, new_password=body.new_password, ip=ip
    )
    return ok(request, {})


@router.post("/password/change")
async def change_password(
    body: ChangeIn, request: Request, ctx: CurrentUser, sessions: SessionsDep, audit: AuditDep, ip: ClientIp
) -> JSONResponse:
    await auth.change_password(
        sessions,
        audit,
        user_id=ctx.user.id,
        session_id=ctx.session.id,
        current_password=body.current_password,
        new_password=body.new_password,
        ip=ip,
    )
    return ok(request, {})
