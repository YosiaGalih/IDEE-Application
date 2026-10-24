"""Auth hardening regressions (SECURITY_MODEL §6.2, §8; API_CONTRACTS §2, §11 #17)."""

from __future__ import annotations

import asyncio
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


async def test_failed_logins_never_hold_two_connections(owner_id: uuid.UUID, engine: AsyncEngine) -> None:
    """Review WP-1.1d HIGH: a denied login held its read session while writing the audit row in a second one,
    so concurrent failures exhausted the pool (self-deadlock until pool_timeout)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.db import make_sessionmaker as sessionmaker_for

    await add_user(engine, "locked@example.test", locked_until=datetime.now(UTC) + timedelta(minutes=10))
    await add_user(engine, "disabled@example.test", status="disabled")
    small = create_async_engine(make_test_env()["DATABASE_URL"], pool_size=2, max_overflow=0, pool_timeout=3)
    app, client = await make_client()
    app.state.dependencies.sessions = sessionmaker_for(small)
    emails = ["nobody@example.test", "locked@example.test", "disabled@example.test"] * 3
    try:
        async with client:
            responses = await asyncio.gather(*(login(client, email=e) for e in emails[:9]))
    finally:
        await app.state.dependencies.aclose()
        await small.dispose()
    assert [r.status_code for r in responses] == [401] * 9


async def test_argon2_runs_off_the_event_loop(
    client: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review WP-1.1d HIGH: argon2 is CPU-bound (~70 ms); on the event loop it froze every request."""
    import threading

    from app.core import security

    threads: list[str] = []
    real_verify, real_hash = security.verify_password, security.hash_password

    def spy_verify(password_hash: str, password: str) -> bool:
        threads.append(threading.current_thread().name)
        return real_verify(password_hash, password)

    def spy_hash(password: str) -> str:
        threads.append(threading.current_thread().name)
        return real_hash(password)

    monkeypatch.setattr(security, "verify_password", spy_verify)
    monkeypatch.setattr(security, "hash_password", spy_hash)
    await login(client, email="nobody@example.test")
    await login(client, password="wrong-password-123")
    await login(client)
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    response = await client.post(
        "/api/v1/auth/password/change", json=change, headers={"X-CSRF-Token": client.cookies["kk_csrf"]}
    )
    assert response.status_code == 200
    assert len(threads) >= 5
    assert all(name.startswith("argon2") for name in threads), threads


async def test_lockout_cannot_be_bypassed_by_a_burst(owner_id: uuid.UUID, engine: AsyncEngine) -> None:
    """Security pre-review WP-1.1d: a concurrent burst got 15 guesses evaluated, not 5 per lockout window."""
    app, _ = await make_client()
    clients = [
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(f"198.51.100.{i}", 1)), base_url="http://t"
        )
        for i in range(20)
    ]
    try:
        responses = await asyncio.gather(
            *(login(c, password=f"wrong-guess-{i:04d}") for i, c in enumerate(clients))
        )
        late = await login(clients[0])
    finally:
        for c in clients:
            await c.aclose()
        await app.state.dependencies.aclose()
    assert [r.status_code for r in responses] == [401] * 20
    assert late.status_code == 401  # the right password no longer helps once the account is locked
    async with make_sessionmaker(engine)() as session:
        reasons = (
            await session.scalars(
                select(AuditEvent.metadata_).where(
                    AuditEvent.action == "auth.login", AuditEvent.actor_id == str(owner_id)
                )
            )
        ).all()
        lockouts = (
            await session.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.action == "auth.lockout", AuditEvent.resource_id == str(owner_id)
                )
            )
        ).all()
    evaluated = [r for r in reasons if r.get("reason") == "bad_password"]
    assert len(evaluated) <= 5, reasons
    assert len(lockouts) == 1


async def test_reset_waits_for_the_token_row_lock(owner_id: uuid.UUID, engine: AsyncEngine) -> None:
    """Deterministic proof of FOR UPDATE: a reset blocked on the token row fails once the holder uses it."""
    from app.core.errors import AppError

    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    sessions = make_sessionmaker(engine)
    try:
        token = await auth.request_password_reset(
            sessions, AuditWriter(Redactor()), FixedWindowLimiter(redis), email=EMAIL, ip=None
        )
        async with sessions() as holder:
            await holder.execute(text("SELECT id FROM password_reset_tokens FOR UPDATE"))
            racer = asyncio.create_task(
                auth.reset_password(
                    sessions,
                    AuditWriter(Redactor()),
                    FixedWindowLimiter(redis),
                    token=token or "",
                    new_password=NEW_PASSWORD,
                    ip=None,
                )
            )
            await asyncio.sleep(0.5)
            assert not racer.done()  # blocked on the row lock
            await holder.execute(text("UPDATE password_reset_tokens SET used_at = now()"))
            await holder.commit()
        with pytest.raises(AppError) as caught:
            await racer
        assert caught.value.code == "BAD_REQUEST"
    finally:
        await redis.aclose()


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        ('{"email": "a@b.c", "password": "x"}', "text/plain"),
        ("email=a%40b.c&password=x", "application/x-www-form-urlencoded"),
    ],
)
async def test_login_rejects_non_json_bodies(
    client: httpx.AsyncClient, content: str, content_type: str
) -> None:
    """The login CSRF exemption is safe only because a cross-site form cannot send JSON."""
    response = await client.post(LOGIN, content=content, headers={"Content-Type": content_type})
    assert response.status_code == 422
