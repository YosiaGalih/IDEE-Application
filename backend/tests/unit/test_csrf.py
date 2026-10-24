"""CSRF double-submit middleware (API_CONTRACTS §2, SECURITY_MODEL §8)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.core.csrf import CSRF_COOKIE, CsrfMiddleware
from app.main import RequestIdMiddleware

LOGIN = "/api/v1/auth/login"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.get("/thing")
    async def read() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/thing")
    async def write() -> dict[str, bool]:
        return {"ok": True}

    @app.post(LOGIN)
    async def login(response: Response) -> dict[str, bool]:
        response.set_cookie(CSRF_COOKIE, "session-bound-token", path="/", samesite="lax")
        return {"ok": True}

    app.add_middleware(CsrfMiddleware, secure=False, exempt=frozenset({("POST", LOGIN)}))
    app.add_middleware(RequestIdMiddleware)
    return TestClient(app)


def set_cookies(response) -> list[str]:  # type: ignore[no-untyped-def]
    return response.headers.get_list("set-cookie")


def test_post_without_header_or_cookie_is_403_csrf_failed(client: TestClient) -> None:
    response = client.post("/thing")
    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "CSRF_FAILED"
    assert body["request_id"] == response.headers["x-request-id"]


def test_post_with_mismatched_values_is_403(client: TestClient) -> None:
    client.cookies.set(CSRF_COOKIE, "a" * 43)
    response = client.post("/thing", headers={"X-CSRF-Token": "b" * 43})
    assert response.status_code == 403


def test_empty_values_never_match(client: TestClient) -> None:
    client.cookies.set(CSRF_COOKIE, "")
    assert client.post("/thing", headers={"X-CSRF-Token": ""}).status_code == 403


def test_post_with_matching_values_passes(client: TestClient) -> None:
    client.cookies.set(CSRF_COOKIE, "a" * 43)
    response = client.post("/thing", headers={"X-CSRF-Token": "a" * 43})
    assert response.status_code == 200


def test_exempt_login_passes_without_token(client: TestClient) -> None:
    assert client.post(LOGIN).status_code == 200


def test_get_without_cookie_receives_presession_cookie(client: TestClient) -> None:
    response = client.get("/thing")
    cookies = [c for c in set_cookies(response) if c.startswith(f"{CSRF_COOKIE}=")]
    assert len(cookies) == 1
    attributes = cookies[0].lower()
    assert "samesite=lax" in attributes
    assert "path=/" in attributes
    assert "httponly" not in attributes
    assert "domain" not in attributes
    assert len(cookies[0].split(";")[0].split("=", 1)[1]) >= 43


def test_existing_cookie_is_not_reissued(client: TestClient) -> None:
    client.cookies.set(CSRF_COOKIE, "a" * 43)
    assert not [c for c in set_cookies(client.get("/thing")) if c.startswith(f"{CSRF_COOKIE}=")]


def test_route_set_csrf_cookie_is_not_overwritten(client: TestClient) -> None:
    cookies = [c for c in set_cookies(client.post(LOGIN)) if c.startswith(f"{CSRF_COOKIE}=")]
    assert cookies == [c for c in cookies if "session-bound-token" in c]
    assert len(cookies) == 1
