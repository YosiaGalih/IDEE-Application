"""Idempotency-Key for mutating requests (API_CONTRACTS §1): replay the first response for 24 h."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.core.config import load_settings
from app.main import create_app
from tests.conftest import make_test_env

CSRF = "c" * 43


class State:
    def __init__(self) -> None:
        self.count = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()


@pytest.fixture
async def redis_clean() -> AsyncIterator[None]:
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    await redis.flushdb()
    yield
    await redis.flushdb()
    await redis.aclose()


def build(state: State, **env: str) -> FastAPI:
    app = create_app(load_settings(make_test_env(COOKIE_SECURE="false", **env)))

    @app.post("/api/v1/_test/count")
    async def count(body: dict[str, int]) -> JSONResponse:
        state.count += 1
        response = JSONResponse({"n": state.count, "echo": body}, status_code=201)
        response.set_cookie("probe", "first")
        return response

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
async def state(redis_clean: None) -> State:
    return State()


@pytest.fixture
async def app(state: State) -> AsyncIterator[FastAPI]:
    app = build(state)
    yield app
    await app.state.dependencies.aclose()


def client(app: FastAPI, session: str | None = "token-a") -> httpx.AsyncClient:
    cookies = {"kk_csrf": CSRF} | ({"kk_session": session} if session else {})
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        cookies=cookies,
        headers={"X-CSRF-Token": CSRF},
    )


def keyed(key: str = "key-1") -> dict[str, str]:
    return {"Idempotency-Key": key}


async def test_replay_returns_the_first_response(app: FastAPI, state: State) -> None:
    async with client(app) as c:
        first = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
        second = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json() == {"n": 1, "echo": {"a": 1}}
    assert state.count == 1
    assert second.headers.get("idempotent-replay") == "true"
    assert "idempotent-replay" not in first.headers
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_replay_never_sets_cookies(app: FastAPI) -> None:
    async with client(app) as c:
        first = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
        second = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
    assert "probe=first" in first.headers.get("set-cookie", "")
    assert "probe" not in second.headers.get("set-cookie", "")


async def test_same_key_with_a_different_body_conflicts(app: FastAPI, state: State) -> None:
    async with client(app) as c:
        await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
        response = await c.post("/api/v1/_test/count", json={"a": 2}, headers=keyed())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert state.count == 1


async def test_same_key_while_in_flight_conflicts(app: FastAPI, state: State) -> None:
    async with client(app) as c:
        first = asyncio.create_task(c.post("/api/v1/_test/slow", headers=keyed()))
        await asyncio.wait_for(state.started.wait(), timeout=5)
        second = await c.post("/api/v1/_test/slow", headers=keyed())
        state.release.set()
        assert (await first).status_code == 200
    assert second.status_code == 409
    assert state.count == 1


async def test_other_session_is_not_replayed(app: FastAPI, state: State) -> None:
    async with client(app, "token-a") as a, client(app, "token-b") as b:
        await a.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
        other = await b.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
    assert other.json()["n"] == 2
    assert "idempotent-replay" not in other.headers


async def test_server_errors_are_not_stored(app: FastAPI, state: State) -> None:
    async with client(app) as c:
        statuses = [(await c.post("/api/v1/_test/boom", headers=keyed())).status_code for _ in range(2)]
    assert statuses == [500, 500]
    assert state.count == 2


async def test_invalid_key_is_rejected(app: FastAPI, state: State) -> None:
    async with client(app) as c:
        response = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed("bad key!"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    assert state.count == 0


async def test_reads_auth_paths_and_anonymous_requests_are_untouched(app: FastAPI, state: State) -> None:
    async with client(app) as c, client(app, session=None) as anon:
        for _ in range(2):
            await c.get("/api/v1/_test/read", headers=keyed())
            await c.post("/api/v1/auth/_test_exempt", headers=keyed())
            await anon.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
    assert state.count == 6


async def test_redis_down_fails_closed_only_for_keyed_requests(state: State) -> None:
    app = build(state, REDIS_URL="redis://localhost:6390/0")
    try:
        async with client(app) as c:
            keyed_response = await c.post("/api/v1/_test/count", json={"a": 1}, headers=keyed())
            plain = await c.post("/api/v1/_test/count", json={"a": 1})
    finally:
        await app.state.dependencies.aclose()
    assert keyed_response.status_code == 503
    assert keyed_response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert plain.status_code == 201
    assert state.count == 1
