"""Auth flow over HTTP against real PostgreSQL and Redis (AC-01, API_CONTRACTS §2, SECURITY_MODEL §8)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import AuthContext, require_permission
from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.ratelimit import FixedWindowLimiter
from app.core.redaction import Redactor
from app.core.security import hash_password, hash_token
from app.main import create_app
from app.models.audit import AuditEvent
from app.models.identity import Session, User, UserRole
from app.services import auth
from app.services.users import create_owner
from tests.conftest import make_test_env, reset_data

EMAIL = "owner@example.test"
PASSWORD = "owner-long-passphrase"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
LOGOUT = "/api/v1/auth/logout"


@pytest.fixture(autouse=True)
async def clean_state(engine: AsyncEngine) -> AsyncIterator[None]:
    await reset_data(engine)
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    await redis.flushdb()
    yield
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture
async def owner_id(engine: AsyncEngine) -> uuid.UUID:
    return await create_owner(
        make_sessionmaker(engine),
        AuditWriter(Redactor()),
        email=EMAIL,
        display_name="Owner",
        password=PASSWORD,
    )


def build_app(**overrides: str) -> FastAPI:
    return create_app(load_settings(make_test_env(COOKIE_SECURE="false", **overrides)))


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    app = build_app()
    yield app
    await app.state.dependencies.aclose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def login(client: httpx.AsyncClient, email: str = EMAIL, password: str = PASSWORD) -> httpx.Response:
    return await client.post(LOGIN, json={"email": email, "password": password, "remember_me": False})


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["kk_csrf"]}


def set_cookie(response: httpx.Response, name: str) -> str:
    matches = [c for c in response.headers.get_list("set-cookie") if c.startswith(f"{name}=")]
    assert len(matches) == 1, matches
    return matches[0]


async def count_audit(engine: AsyncEngine, action: str, user_id: uuid.UUID) -> int:
    """audit_events is append-only and outlives each test, so counts are scoped to this test's user."""
    async with make_sessionmaker(engine)() as session:
        return await session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == action,
                (AuditEvent.actor_id == str(user_id)) | (AuditEvent.resource_id == str(user_id)),
            )
        )


async def test_login_sets_cookies_and_me(client: httpx.AsyncClient, owner_id: uuid.UUID) -> None:
    response = await login(client)
    assert response.status_code == 200, response.text
    session_cookie = set_cookie(response, "kk_session").lower()
    assert "httponly" in session_cookie and "samesite=lax" in session_cookie and "path=/" in session_cookie
    assert "httponly" not in set_cookie(response, "kk_csrf").lower()
    assert response.json()["data"]["user"]["email"] == EMAIL
    assert "password_hash" not in response.text

    me = await client.get(ME)
    assert me.status_code == 200, me.text
    data = me.json()["data"]
    assert data["user"] == {
        "id": str(owner_id),
        "email": EMAIL,
        "display_name": "Owner",
        "status": "active",
        "created_at": data["user"]["created_at"],
        "last_login_at": data["user"]["last_login_at"],
    }
    assert data["user"]["last_login_at"] is not None
    assert data["roles"] == [{"role": "owner", "scope": "global"}]
    assert "audit:read" in data["permissions"]
    assert data["permissions"] == sorted(data["permissions"])


async def test_logout_revokes_session_and_clears_cookies(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    await login(client)
    token = client.cookies["kk_session"]
    response = await client.post(LOGOUT, headers=csrf(client))
    assert response.status_code == 200, response.text
    assert "max-age=0" in set_cookie(response, "kk_session").lower()
    assert "max-age=0" in set_cookie(response, "kk_csrf").lower()
    async with make_sessionmaker(engine)() as session:
        revoked = await session.scalar(
            select(Session.revoked_at).where(Session.token_hash == hash_token(token))
        )
    assert revoked is not None
    client.cookies.set("kk_session", token)
    stale = await client.get(ME)
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "UNAUTHENTICATED"
    assert await count_audit(engine, "auth.logout", owner_id) == 1


async def test_lockout_after_five_failures(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    for _ in range(5):
        assert (await login(client, password="wrong-password-123")).status_code == 401
    response = await login(client)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    async with make_sessionmaker(engine)() as session:
        user = await session.get(User, owner_id)
    assert user is not None and user.locked_until is not None
    remaining = user.locked_until - datetime.now(UTC)
    assert timedelta(minutes=14) < remaining <= timedelta(minutes=15)
    assert await count_audit(engine, "auth.lockout", owner_id) == 1


async def test_rate_limit_ten_per_minute_per_ip(client: httpx.AsyncClient, owner_id: uuid.UUID) -> None:
    for _ in range(10):
        assert (await login(client, email="nobody@example.test")).status_code == 401
    response = await login(client)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert 0 < int(response.headers["retry-after"]) <= 60


async def test_session_idle_and_absolute_expiry(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    for statement in (
        "UPDATE sessions SET last_seen_at = now() - interval '13 hours' WHERE token_hash = :h",
        "UPDATE sessions SET expires_at = now() - interval '1 second' WHERE token_hash = :h",
    ):
        await login(client)
        token = client.cookies["kk_session"]
        async with engine.begin() as conn:
            await conn.execute(
                text(statement),
                {"h": hash_token(token)},
            )
        response = await client.get(ME)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "SESSION_EXPIRED"
        async with make_sessionmaker(engine)() as session:
            revoked = await session.scalar(
                select(Session.revoked_at).where(Session.token_hash == hash_token(token))
            )
        assert revoked is not None


async def test_login_rotates_previous_session(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    await login(client)
    first = client.cookies["kk_session"]
    await login(client)
    second = client.cookies["kk_session"]
    assert first != second
    async with make_sessionmaker(engine)() as session:
        revoked = await session.scalar(
            select(Session.revoked_at).where(Session.token_hash == hash_token(first))
        )
    assert revoked is not None


async def test_mutation_needs_session_bound_csrf(app: FastAPI, owner_id: uuid.UUID) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        await login(client)
        session_token = client.cookies["kk_session"]
    forged = "f" * 43
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        cookies={"kk_session": session_token, "kk_csrf": forged},
    ) as attacker:
        response = await attacker.post(LOGOUT, headers={"X-CSRF-Token": forged})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


async def test_concurrent_failures_lock_once(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET failed_login_count = 3"))
    responses = await asyncio.gather(*(login(client, password="wrong-password-123") for _ in range(2)))
    assert [r.status_code for r in responses] == [401, 401]
    async with make_sessionmaker(engine)() as session:
        user = await session.get(User, owner_id)
    assert user is not None and user.locked_until is not None and user.failed_login_count == 0
    assert await count_audit(engine, "auth.lockout", owner_id) == 1


async def test_login_fails_closed_without_redis(owner_id: uuid.UUID) -> None:
    app = build_app(REDIS_URL="redis://localhost:6390/0")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await login(client)
    finally:
        await app.state.dependencies.aclose()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "kk_session" not in response.headers.get("set-cookie", "")


async def test_disabled_user_session_is_rejected(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    await login(client)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET status = 'disabled'"))
    response = await client.get(ME)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_require_permission_guards_global_only_routes(
    app: FastAPI, client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    @app.get("/api/v1/_test/audit")
    async def audit_only(
        ctx: Annotated[AuthContext, Depends(require_permission("audit:read"))],
    ) -> dict[str, str]:
        return {"user": str(ctx.user.id)}

    assert (await client.get("/api/v1/_test/audit")).status_code == 401
    async with make_sessionmaker(engine)() as session:
        dev = User(email="dev@example.test", display_name="Dev", password_hash=hash_password(PASSWORD))
        session.add(dev)
        await session.flush()
        session.add(UserRole(user_id=dev.id, role_name="developer", scope="global"))
        await session.commit()
    await login(client, email="dev@example.test")
    denied = await client.get("/api/v1/_test/audit")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
    await login(client)
    assert (await client.get("/api/v1/_test/audit")).status_code == 200


FORGOT = "/api/v1/auth/password/forgot"
RESET = "/api/v1/auth/password/reset"
CHANGE = "/api/v1/auth/password/change"
NEW_PASSWORD = "brand-new-passphrase"


async def presession_csrf(client: httpx.AsyncClient) -> dict[str, str]:
    await client.get("/api/v1/health/live")
    return csrf(client)


async def issue_reset_token(engine: AsyncEngine, email: str = EMAIL) -> str | None:
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    try:
        return await auth.request_password_reset(
            make_sessionmaker(engine),
            AuditWriter(Redactor()),
            FixedWindowLimiter(redis),
            email=email,
            ip=None,
        )
    finally:
        await redis.aclose()


async def test_forgot_always_200(client: httpx.AsyncClient, owner_id: uuid.UUID) -> None:
    headers = await presession_csrf(client)
    known = await client.post(FORGOT, json={"email": EMAIL}, headers=headers)
    unknown = await client.post(FORGOT, json={"email": "nobody@example.test"}, headers=headers)
    assert known.status_code == unknown.status_code == 200
    assert known.json()["data"] == unknown.json()["data"] == {}


async def test_reset_changes_password_and_revokes_all_sessions(
    app: FastAPI, client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    await login(client)
    token = await issue_reset_token(engine)
    assert token is not None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as other:
        response = await other.post(
            RESET, json={"token": token, "new_password": NEW_PASSWORD}, headers=await presession_csrf(other)
        )
        assert response.status_code == 200, response.text
        assert (await login(other)).status_code == 401
        assert (await login(other, password=NEW_PASSWORD)).status_code == 200
    assert (await client.get(ME)).status_code == 401
    assert await count_audit(engine, "auth.password_reset", owner_id) == 1


async def test_reset_token_is_single_use_and_expires(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    headers = await presession_csrf(client)
    token = await issue_reset_token(engine)
    body = {"token": token, "new_password": NEW_PASSWORD}
    assert (await client.post(RESET, json=body, headers=headers)).status_code == 200
    reused = await client.post(RESET, json=body, headers=headers)
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "BAD_REQUEST"

    expired = await issue_reset_token(engine)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE password_reset_tokens SET expires_at = now() - interval '1 second'"))
    late = await client.post(RESET, json={"token": expired, "new_password": NEW_PASSWORD}, headers=headers)
    assert late.status_code == 400


async def test_new_reset_request_supersedes_older_token(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    first = await issue_reset_token(engine)
    await issue_reset_token(engine)
    response = await client.post(
        RESET, json={"token": first, "new_password": NEW_PASSWORD}, headers=await presession_csrf(client)
    )
    assert response.status_code == 400


async def test_reset_token_single_use_under_race(owner_id: uuid.UUID, engine: AsyncEngine) -> None:
    token = await issue_reset_token(engine)
    assert token is not None
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    try:
        results = await asyncio.gather(
            *(
                auth.reset_password(
                    make_sessionmaker(engine),
                    AuditWriter(Redactor()),
                    FixedWindowLimiter(redis),
                    token=token,
                    new_password=f"{NEW_PASSWORD}-{i}",
                    ip=None,
                )
                for i in range(2)
            ),
            return_exceptions=True,
        )
    finally:
        await redis.aclose()
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1
    assert isinstance(failures[0], AppError) and failures[0].code == "BAD_REQUEST"


async def test_change_password_keeps_current_session_revokes_others(
    app: FastAPI, client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    await login(client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as other:
        await login(other)
        response = await client.post(
            CHANGE, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, headers=csrf(client)
        )
        assert response.status_code == 200, response.text
        assert (await other.get(ME)).status_code == 401
    assert (await client.get(ME)).status_code == 200
    assert await count_audit(engine, "auth.password_change", owner_id) == 1


async def test_change_with_wrong_current_is_403_and_counts_toward_lockout(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    await login(client)
    response = await client.post(
        CHANGE,
        json={"current_password": "not-the-password", "new_password": NEW_PASSWORD},
        headers=csrf(client),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    async with make_sessionmaker(engine)() as session:
        user = await session.get(User, owner_id)
    assert user is not None and user.failed_login_count == 1


@pytest.mark.parametrize("length", [11, 1025])
async def test_new_password_length_rules(client: httpx.AsyncClient, owner_id: uuid.UUID, length: int) -> None:
    await login(client)
    response = await client.post(
        CHANGE, json={"current_password": PASSWORD, "new_password": "x" * length}, headers=csrf(client)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
