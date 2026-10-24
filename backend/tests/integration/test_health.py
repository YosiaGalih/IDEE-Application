"""Readiness against real PostgreSQL 16 and Redis 7; sandbox-manager mocked (it is built in WP-1.3)."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.main import create_app
from tests.conftest import make_test_env

SANDBOX_HEALTHZ = "http://sandbox-manager.test:8100/healthz"


@pytest.fixture
def mock_sandbox_manager() -> respx.MockRouter:
    with respx.mock(assert_all_called=False) as router:
        router.get(SANDBOX_HEALTHZ).mock(return_value=httpx.Response(200, json={"status": "ok"}))
        yield router


def test_ready_with_real_db_and_redis(mock_sandbox_manager: respx.MockRouter) -> None:
    with TestClient(create_app(load_settings(make_test_env()))) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["checks"] == {
        "db": "ok",
        "redis": "ok",
        "sandbox_manager": "ok",
        "migrations": "ok",
    }


def test_ready_degrades_when_redis_is_unreachable(mock_sandbox_manager: respx.MockRouter) -> None:
    settings = load_settings(make_test_env(REDIS_URL="redis://:wrongpass99@localhost:6390/0"))
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["data"]["checks"]["redis"] == "unavailable"
    assert response.json()["data"]["checks"]["db"] == "ok"
    assert "wrongpass99" not in response.text


def test_ready_degrades_when_sandbox_manager_is_down() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(SANDBOX_HEALTHZ).mock(side_effect=httpx.ConnectError("refused"))
        with TestClient(create_app(load_settings(make_test_env()))) as client:
            response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["data"]["checks"]["sandbox_manager"] == "unavailable"
