"""Auth hardening regressions (SECURITY_MODEL §6.2, §8; API_CONTRACTS §2, §11 #17)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.core.ratelimit import FixedWindowLimiter
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.main import create_app
from app.models.audit import AuditEvent
from app.models.identity import User
from app.services import auth
from app.services.users import create_owner
from tests.conftest import make_test_env

EMAIL = "owner@example.test"
PASSWORD = "owner-long-passphrase"
NEW_PASSWORD = "brand-new-passphrase"
LOGIN = "/api/v1/auth/login"


@pytest.fixture(autouse=True)
async def clean_redis() -> AsyncIterator[None]:
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


async def make_client(**overrides: str) -> tuple[FastAPI, httpx.AsyncClient]:
    app = create_app(load_settings(make_test_env(**{"COOKIE_SECURE": "false", **overrides})))
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app, client = await make_client()
    async with client:
        yield client
    await app.state.dependencies.aclose()


async def login(client: httpx.AsyncClient, email: str = EMAIL, password: str = PASSWORD) -> httpx.Response:
    return await client.post(LOGIN, json={"email": email, "password": password, "remember_me": False})


async def add_user(engine: AsyncEngine, email: str, **fields: object) -> None:
    async with make_sessionmaker(engine)() as session:
        session.add(User(email=email, display_name="U", password_hash=hash_password(PASSWORD), **fields))
        await session.commit()


async def test_failure_responses_are_identical(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    await add_user(engine, "locked@example.test", locked_until=datetime.now(UTC) + timedelta(minutes=10))
    await add_user(engine, "disabled@example.test", status="disabled")
    attempts = {
        "unknown": await login(client, email="nobody@example.test"),
        "locked": await login(client, email="locked@example.test"),
        "disabled": await login(client, email="disabled@example.test"),
        "bad_password": await login(client, password="wrong-password-123"),
    }
    bodies = {}
    for reason, response in attempts.items():
        assert response.status_code == 401, reason
        body = response.json()
        body.pop("request_id")
        bodies[reason] = body
        assert "kk_session" not in response.headers.get("set-cookie", "")
    assert len({repr(b) for b in bodies.values()}) == 1, bodies
    assert (await login(client, email="  Owner@Example.TEST ")).status_code == 200


async def test_no_secret_in_responses_logs_or_audit(
    client: httpx.AsyncClient,
    owner_id: uuid.UUID,
    engine: AsyncEngine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bodies = [(await login(client)).text]
    session_token = client.cookies["kk_session"]
    headers = {"X-CSRF-Token": client.cookies["kk_csrf"]}
    bodies.append((await client.get("/api/v1/auth/me")).text)
    bodies.append(
        (await client.post("/api/v1/auth/password/forgot", json={"email": EMAIL}, headers=headers)).text
    )
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    try:
        reset_token = await auth.request_password_reset(
            make_sessionmaker(engine),
            AuditWriter(Redactor()),
            FixedWindowLimiter(redis),
            email=EMAIL,
            ip=None,
        )
    finally:
        await redis.aclose()
    assert reset_token is not None
    body = {"token": reset_token, "new_password": NEW_PASSWORD}
    bodies.append((await client.post("/api/v1/auth/password/reset", json=body, headers=headers)).text)
    bodies.append((await login(client, password=NEW_PASSWORD)).text)
    headers = {"X-CSRF-Token": client.cookies["kk_csrf"]}
    change = {"current_password": NEW_PASSWORD, "new_password": PASSWORD}
    bodies.append((await client.post("/api/v1/auth/password/change", json=change, headers=headers)).text)
    bodies.append((await client.post("/api/v1/auth/logout", headers=headers)).text)

    async with make_sessionmaker(engine)() as session:
        audit_rows = [
            repr((e.actor_id, e.resource_id, e.metadata_))
            for e in await session.scalars(
                select(AuditEvent).where(AuditEvent.ts > datetime.now(UTC) - timedelta(minutes=1))
            )
        ]
    output = "\n".join(bodies) + capsys.readouterr().out + "\n".join(audit_rows)
    for secret in (PASSWORD, NEW_PASSWORD, "$argon2", session_token, reset_token, "password_hash"):
        assert secret not in output, secret


async def test_presession_csrf_cannot_authorize_session_mutation(
    client: httpx.AsyncClient, owner_id: uuid.UUID
) -> None:
    await login(client)
    session_token = client.cookies["kk_session"]
    planted = "p" * 43  # e.g. tossed in by a sibling subdomain before the victim signed in
    app, attacker = await make_client()
    async with attacker:
        attacker.cookies.set("kk_session", session_token)
        attacker.cookies.set("kk_csrf", planted)
        response = await attacker.post(
            "/api/v1/auth/password/change",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers={"X-CSRF-Token": planted},
        )
    await app.state.dependencies.aclose()
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


async def test_cookies_are_host_only_secure_and_samesite_lax(owner_id: uuid.UUID) -> None:
    app, client = await make_client(COOKIE_SECURE="true")
    async with client:
        response = await login(client)
    await app.state.dependencies.aclose()
    cookies = [c.lower() for c in response.headers.get_list("set-cookie")]
    assert {c.split("=", 1)[0] for c in cookies} == {"kk_session", "kk_csrf"}
    for cookie in cookies:
        assert "domain" not in cookie
        assert "samesite=lax" in cookie
        assert "secure" in cookie
        assert "path=/" in cookie


async def test_audit_denied_login_has_no_email_or_password(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    await login(client, email="probe@example.test", password="probe-password-123")
    async with make_sessionmaker(engine)() as session:
        event = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "auth.login", AuditEvent.result == "denied")
            .order_by(AuditEvent.id.desc())
        )
    assert event is not None
    assert event.actor_id == "anonymous"
    assert event.metadata_ == {"reason": "unknown_email"}
    row = repr((event.actor_id, event.resource_id, event.metadata_))
    assert "probe@example.test" not in row and "probe-password-123" not in row


@pytest.mark.parametrize(("app_env", "logged"), [("test", False), ("development", True)])
async def test_reset_link_is_logged_only_in_development(
    owner_id: uuid.UUID, capsys: pytest.CaptureFixture[str], app_env: str, logged: bool
) -> None:
    app, client = await make_client(APP_ENV=app_env)
    async with client:
        await client.get("/api/v1/health/live")
        await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": EMAIL},
            headers={"X-CSRF-Token": client.cookies["kk_csrf"]},
        )
    await app.state.dependencies.aclose()
    assert ("reset_path" in capsys.readouterr().out) is logged


async def test_revoked_session_is_rejected(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    await login(client)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE sessions SET revoked_at = now()"))
    assert (await client.get("/api/v1/auth/me")).status_code == 401
