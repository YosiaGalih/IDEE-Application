"""Idempotency-Key for mutating requests (API_CONTRACTS §1): replay the first response for 24 h.

Uses real sessions (login), because the middleware only acts for a valid session (WP-1.1f review).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response, StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import CurrentUser
from app.core import idempotency
from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.main import create_app
from app.services.users import create_owner
from tests.conftest import make_test_env, reset_data

EMAIL = "owner@example.test"
PASSWORD = "owner-long-passphrase"


class State:
    def __init__(self) -> None:
        self.count = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.transient = True


@pytest.fixture
async def state(engine: AsyncEngine) -> AsyncIterator[State]:
    await reset_data(engine)
    await create_owner(
        make_sessionmaker(engine), AuditWriter(Redactor()), email=EMAIL, display_name="O", password=PASSWORD
    )
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    await redis.flushdb()
    yield State()
    await redis.flushdb()
    await redis.aclose()


def build(state: State, **env: str) -> FastAPI:
    app = create_app(load_settings(make_test_env(COOKIE_SECURE="false", **env)))

    @app.post("/api/v1/_test/count")
    async def count(body: dict[str, int]) -> JSONResponse:
        state.count += 1
        response = JSONResponse({"n": state.count, "echo": body}, status_code=201)
        response.set_cookie("probe", "first")
        response.headers["Location"] = f"/api/v1/things/{state.count}"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/v1/_test/whoami")
    async def whoami(ctx: CurrentUser) -> dict[str, Any]:
        state.count += 1
        return {"n": state.count, "user": str(ctx.user.id)}

    @app.post("/api/v1/_test/slow")
    async def slow() -> dict[str, int]:
        state.count += 1
        state.started.set()
        await state.release.wait()
        return {"n": state.count}

    @app.post("/api/v1/_test/boom")
    async def boom() -> JSONResponse:
        state.count += 1
        return JSONResponse({"error": "x"}, status_code=500)

    @app.post("/api/v1/_test/flaky")
    async def flaky() -> JSONResponse:
        state.count += 1
        if state.transient:
            state.transient = False
            return JSONResponse({"error": "slow down"}, status_code=429, headers={"Retry-After": "1"})
        return JSONResponse({"n": state.count}, status_code=200)

    @app.post("/api/v1/_test/big")
    async def big() -> Response:
        state.count += 1
        return Response(b"x" * (idempotency.MAX_STORED_BODY + 1), status_code=201, media_type="text/plain")

    @app.post("/api/v1/_test/stream")
    async def stream() -> StreamingResponse:
        state.count += 1

        async def chunks() -> AsyncIterator[bytes]:
            for i in range(3):
                yield f"chunk{i}".encode()

        return StreamingResponse(chunks(), media_type="text/plain")

    @app.get("/api/v1/_test/read")
    async def read() -> dict[str, int]:
        state.count += 1
        return {"n": state.count}

    @app.post("/api/v1/auth/_test_exempt")
    async def exempt() -> dict[str, int]:
        state.count += 1
        return {"n": state.count}

    return app


@pytest.fixture
async def app(state: State) -> AsyncIterator[FastAPI]:
    app = build(state)
    yield app
    await app.state.dependencies.aclose()


@asynccontextmanager
async def signed_in(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = client.cookies["kk_csrf"]
        yield client


def keyed(key: str = "key-1") -> dict[str, str]:
    return {"Idempotency-Key": key}


async def test_replay_returns_the_first_response(app: FastAPI, state: State) -> None:
    async with signed_in(app) as c:
        first = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
        second = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json() == {"n": 1, "echo": {"a": 1}}
    assert state.count == 1
    assert second.headers.get("idempotent-replay") == "true"
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_replay_keeps_safe_headers_but_never_cookies(app: FastAPI) -> None:
    async with signed_in(app) as c:
        first = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
        second = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
    assert "probe=first" in first.headers.get("set-cookie", "")
    assert "probe" not in second.headers.get("set-cookie", "")
    assert second.headers["location"] == first.headers["location"]
    assert second.headers["cache-control"] == "no-store"


async def test_same_key_with_a_different_body_or_query_conflicts(app: FastAPI, state: State) -> None:
    async with signed_in(app) as c:
        await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
        other_body = await c.post("/api/v1/_test/count", json={"a": 2}, headers=keyed())
        other_query = await c.post("/api/v1/_test/count?force=true", json={"a": 1}, headers=keyed())
    assert other_body.status_code == other_query.status_code == 409
    assert other_body.json()["error"]["code"] == "CONFLICT"
    assert state.count == 1


async def test_same_key_while_in_flight_conflicts(app: FastAPI, state: State) -> None:
    async with signed_in(app) as c:
        first = asyncio.create_task(c.post("/api/v1/_test/slow", headers=keyed()))
        await asyncio.wait_for(state.started.wait(), timeout=5)
        second = await c.post("/api/v1/_test/slow", headers=keyed())
        state.release.set()
        assert (await first).status_code == 200
    assert second.status_code == 409
    assert state.count == 1


async def test_pending_marker_is_kept_alive_for_long_requests(
    app: FastAPI, state: State, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(idempotency, "PENDING_TTL_SECONDS", 1)
    monkeypatch.setattr(idempotency, "PENDING_REFRESH_SECONDS", 0.3)
    async with signed_in(app) as c:
        first = asyncio.create_task(c.post("/api/v1/_test/slow", headers=keyed()))
        await asyncio.wait_for(state.started.wait(), timeout=5)
        await asyncio.sleep(1.5)  # longer than the pending TTL
        second = await c.post("/api/v1/_test/slow", headers=keyed())
        state.release.set()
        await first
    assert second.status_code == 409
    assert state.count == 1


async def test_other_session_is_not_replayed(app: FastAPI, state: State) -> None:
    async with signed_in(app) as a, signed_in(app) as b:
        await a.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
        other = await b.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
    assert other.json()["n"] == 2
    assert "idempotent-replay" not in other.headers


async def test_replay_requires_a_live_session(app: FastAPI, state: State) -> None:
    """Review WP-1.1f HIGH: after logout the old cookie got the stored response back."""
    async with signed_in(app) as c:
        token = c.cookies["kk_session"]
        first = await c.post("/api/v1/_test/whoami", headers=keyed())
        assert first.status_code == 200
        assert (await c.post("/api/v1/auth/logout")).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"kk_session": token, "kk_csrf": "c" * 43},
        headers={"X-CSRF-Token": "c" * 43},
    ) as stale:
        replay = await stale.post("/api/v1/_test/whoami", headers=keyed())
    assert replay.status_code == 401
    assert "idempotent-replay" not in replay.headers


async def test_invalid_sessions_never_touch_the_store(app: FastAPI, state: State) -> None:
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"kk_session": "forged", "kk_csrf": "c" * 43},
        headers={"X-CSRF-Token": "c" * 43},
    ) as forged:
        for i in range(3):
            await forged.post(f"/api/v1/nope/{i}", headers=keyed())
    keys = await redis.keys("kk:idem:*")
    await redis.aclose()
    assert keys == []


async def test_server_errors_and_transient_rejections_are_not_stored(app: FastAPI, state: State) -> None:
    async with signed_in(app) as c:
        booms = [(await c.post("/api/v1/_test/boom", headers=keyed("b"))).status_code for _ in range(2)]
        flaky = [(await c.post("/api/v1/_test/flaky", headers=keyed("f"))).status_code for _ in range(2)]
    assert booms == [500, 500]
    assert flaky == [429, 200]
    assert state.count == 4


async def test_oversized_success_is_not_executed_again(app: FastAPI, state: State) -> None:
    async with signed_in(app) as c:
        first = await c.post("/api/v1/_test/big", headers=keyed())
        again = await c.post("/api/v1/_test/big", headers=keyed())
    assert first.status_code == 201
    assert again.status_code == 409
    assert "executed" in again.json()["error"]["message"]
    assert state.count == 1


async def test_store_failure_after_success_is_not_executed_again(
    app: FastAPI, state: State, monkeypatch: pytest.MonkeyPatch
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def fail(*_: Any, **__: Any) -> None:
        raise RedisConnectionError("redis went away")

    monkeypatch.setattr(idempotency.IdempotencyMiddleware, "_finalize", fail)
    async with signed_in(app) as c:
        first = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
        monkeypatch.undo()
        again = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
    assert first.status_code == 201
    assert again.status_code == 409
    assert state.count == 1


async def test_invalid_key_and_oversized_body_are_rejected(app: FastAPI, state: State) -> None:
    async with signed_in(app) as c:
        bad_key = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed("bad key!"))
        too_big = await c.post(
            "/api/v1/_test/count",
            content=b"{" + b" " * idempotency.MAX_REQUEST_BODY + b"}",
            headers={**keyed(), "Content-Type": "application/json"},
        )
    assert bad_key.status_code == 400 and bad_key.json()["error"]["code"] == "BAD_REQUEST"
    assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert state.count == 0


async def test_reads_auth_paths_and_anonymous_requests_are_untouched(app: FastAPI, state: State) -> None:
    async with (
        signed_in(app) as c,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"kk_csrf": "c" * 43},
            headers={"X-CSRF-Token": "c" * 43},
        ) as anon,
    ):
        for _ in range(2):
            await c.get("/api/v1/_test/read", headers=keyed())
            await c.post("/api/v1/auth/_test_exempt", headers=keyed())
            await anon.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
    assert state.count == 6


async def test_redis_down_fails_closed_only_for_keyed_requests(state: State) -> None:
    app = build(state)
    try:
        async with signed_in(app) as c:
            # Break only the idempotency store after login (login itself needs the rate limiter).
            for middleware in app.user_middleware:
                if middleware.cls is idempotency.IdempotencyMiddleware:
                    middleware.kwargs["redis"] = Redis.from_url("redis://localhost:6390/0")
            app.middleware_stack = None  # rebuild with the broken client
            keyed_response = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
            plain = await c.post("/api/v1/_test/count", json={"a": 1})
    finally:
        await app.state.dependencies.aclose()
    assert keyed_response.status_code == 503
    assert keyed_response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert plain.status_code == 201
    assert state.count == 1


# --- raw ASGI: disconnects and streaming, which httpx.ASGITransport cannot express ---


async def raw_call(
    app: FastAPI, path: str, session: str, csrf: str, messages: list[dict[str, Any]], *, then_block: bool
) -> list[dict[str, Any]]:
    queue = list(messages)
    forever = asyncio.Event()

    async def receive() -> dict[str, Any]:
        if queue:
            return queue.pop(0)
        if then_block:
            await forever.wait()
        return {"type": "http.disconnect"}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"cookie", f"kk_session={session}; kk_csrf={csrf}".encode()),
            (b"x-csrf-token", csrf.encode()),
            (b"idempotency-key", b"raw-1"),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    return sent


async def test_client_disconnect_mid_body_executes_nothing(app: FastAPI, state: State) -> None:
    async with signed_in(app) as c:
        session, csrf = c.cookies["kk_session"], c.cookies["kk_csrf"]
    partial = [{"type": "http.request", "body": b'{"a": ', "more_body": True}, {"type": "http.disconnect"}]
    await raw_call(app, "/api/v1/_test/count", session, csrf, partial, then_block=False)
    assert state.count == 0
    full = [{"type": "http.request", "body": json.dumps({"a": 1}).encode(), "more_body": False}]
    sent = await raw_call(app, "/api/v1/_test/count", session, csrf, full, then_block=False)
    assert sent[0]["status"] == 201  # the key was not poisoned by the truncated attempt
    assert state.count == 1


async def test_streaming_response_with_a_key_does_not_hang(app: FastAPI, state: State) -> None:
    async with signed_in(app) as c:
        session, csrf = c.cookies["kk_session"], c.cookies["kk_csrf"]
    body = [{"type": "http.request", "body": b"", "more_body": False}]
    sent = await raw_call(app, "/api/v1/_test/stream", session, csrf, body, then_block=True)
    assert sent[0]["status"] == 200
    assert b"".join(m.get("body", b"") for m in sent[1:]) == b"chunk0chunk1chunk2"
