"""Readiness logic without real dependencies (FR-149)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services import health
from app.services.health import readiness


async def ok() -> bool:
    return True


async def falsy() -> bool:
    return False


async def boom() -> bool:
    raise ConnectionError("could not connect to postgresql://kk:hunter2secret@db:5432/x")


async def slow() -> bool:
    await asyncio.sleep(10)
    return True


async def test_readiness_reports_ok_and_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.05)
    result = await readiness({"a": ok, "b": falsy, "c": boom, "d": slow})
    assert result == {"a": "ok", "b": "unavailable", "c": "unavailable", "d": "unavailable"}


def client_with_checks(settings: Settings, checks: dict[str, health.ReadinessCheck]) -> TestClient:
    app = create_app(settings)
    app.state.dependencies.readiness_checks = lambda: checks
    return TestClient(app)


def test_ready_200_when_all_checks_pass(settings: Settings) -> None:
    client = client_with_checks(settings, {"db": ok, "redis": ok, "sandbox_manager": ok, "migrations": ok})
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["data"] == {
        "status": "ok",
        "checks": {"db": "ok", "redis": "ok", "sandbox_manager": "ok", "migrations": "ok"},
    }


def test_ready_503_names_failed_checks_without_exception_text(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    client = client_with_checks(
        settings, {"db": boom, "redis": ok, "sandbox_manager": falsy, "migrations": ok}
    )
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert body["data"]["checks"] == {
        "db": "unavailable",
        "redis": "ok",
        "sandbox_manager": "unavailable",
        "migrations": "ok",
    }
    assert "hunter2secret" not in response.text
    logs = capsys.readouterr().out
    assert "hunter2secret" not in logs
    assert "ConnectionError" in logs


def test_live_is_always_ok(settings: Settings) -> None:
    response = TestClient(create_app(settings)).get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}


def test_redis_client_has_explicit_timeouts(settings: Settings) -> None:
    """A Redis that accepts but never answers must fail the rate limiter fast (fail closed)."""
    from app.services.health import Dependencies

    deps = Dependencies.from_settings(settings)
    kwargs = deps.redis.connection_pool.connection_kwargs
    assert kwargs.get("socket_timeout") == 2.0
    assert kwargs.get("socket_connect_timeout") == 2.0
