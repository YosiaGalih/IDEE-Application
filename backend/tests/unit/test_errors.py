"""Envelope, error mapping and request id — API_CONTRACTS §1."""

from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app

REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class Payload(BaseModel):
    name: str


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/api/v1/_test/conflict")
    async def conflict() -> None:
        raise AppError(
            "VERSION_CONFLICT", "File changed on the server", http_status=409, details=[{"loc": ["path"]}]
        )

    @app.post("/api/v1/_test/validate")
    async def validate(payload: Payload) -> dict[str, str]:
        return {"name": payload.name}

    @app.get("/api/v1/_test/crash")
    async def crash() -> None:
        raise RuntimeError("db at postgresql://kk:hunter2secret@db/x exploded in /srv/app/secret.py")

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def assert_failed_envelope(body: dict[str, object], code: str) -> None:
    assert set(body) == {"request_id", "status", "data", "error", "meta"}
    assert body["status"] == "failed"
    assert body["data"] is None
    error = body["error"]
    assert isinstance(error, dict) and error["code"] == code


def test_app_error_is_wrapped_in_failed_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/_test/conflict")
    assert response.status_code == 409
    body = response.json()
    assert_failed_envelope(body, "VERSION_CONFLICT")
    assert body["error"]["details"] == [{"loc": ["path"]}]
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_unknown_route_is_not_found_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert_failed_envelope(response.json(), "NOT_FOUND")


def test_wrong_method_is_enveloped(client: TestClient) -> None:
    response = client.delete("/api/v1/_test/conflict")
    assert response.status_code == 405
    assert_failed_envelope(response.json(), "METHOD_NOT_ALLOWED")


def test_validation_error_lists_loc_and_msg_without_input(client: TestClient) -> None:
    response = client.post("/api/v1/_test/validate", json={"name": 123, "extra_secret": "hunter2secret"})
    assert response.status_code == 422
    body = response.json()
    assert_failed_envelope(body, "VALIDATION_ERROR")
    detail = body["error"]["details"][0]
    assert set(detail) == {"loc", "msg"}
    assert detail["loc"] == ["body", "name"]
    assert "hunter2secret" not in response.text


def test_unhandled_error_hides_message_trace_and_paths(client: TestClient) -> None:
    response = client.get("/api/v1/_test/crash")
    assert response.status_code == 500
    assert_failed_envelope(response.json(), "INTERNAL_ERROR")
    assert "hunter2secret" not in response.text
    assert "/srv/app" not in response.text
    assert "Traceback" not in response.text
    assert REQUEST_ID.match(response.headers["X-Request-ID"])


def test_valid_client_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "client_req-0001"})
    assert response.headers["X-Request-ID"] == "client_req-0001"
    assert response.json()["request_id"] == "client_req-0001"


@pytest.mark.parametrize("bad", ["short", "has space here", "x" * 65, "evil\r\nSet-Cookie:x", "<script>"])
def test_invalid_client_request_id_is_replaced(client: TestClient, bad: str) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": bad})
    rid = response.headers["X-Request-ID"]
    assert rid != bad and rid.startswith("req_") and REQUEST_ID.match(rid)


def test_logs_are_json_and_redacted(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    client.get("/api/v1/_test/crash", headers={"X-Request-ID": "trace_me_12345"})
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    crash = [r for r in records if r.get("event") == "unhandled_exception"]
    assert crash, records
    assert crash[0]["request_id"] == "trace_me_12345"
    assert "hunter2secret" not in "\n".join(lines)


def test_configured_secret_literals_are_redacted_in_logs(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.core.logging import get_logger

    create_app(settings)
    capsys.readouterr()
    get_logger("test").info("leak_attempt", value=f"key is {settings.secret_key}")
    out = capsys.readouterr().out
    assert settings.secret_key not in out
    assert "[REDACTED]" in out


def test_stdlib_logging_with_traceback_is_redacted(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    import logging

    create_app(settings)
    capsys.readouterr()
    try:
        raise RuntimeError("dsn postgresql://kk:hunter2secret@db/x")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    out = capsys.readouterr()
    assert "Exception in ASGI application" in out.out + out.err
    assert "hunter2secret" not in out.out + out.err


def test_structured_secret_fields_are_redacted(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.core.logging import get_logger

    create_app(settings)
    capsys.readouterr()
    get_logger("t").info(
        "x", password="hunter22", token="abcd1234", error=ConnectionError("redis://:pw77long@r/0")
    )
    out = capsys.readouterr().out
    assert "hunter22" not in out and "abcd1234" not in out and "pw77long" not in out


def test_405_keeps_allow_header(client: TestClient) -> None:
    response = client.delete("/api/v1/_test/conflict")
    assert response.status_code == 405
    assert "GET" in response.headers.get("allow", "")


def test_request_id_is_stable_within_a_request(app: FastAPI) -> None:
    from starlette.requests import Request

    from app.core.errors import request_id_of

    request = Request({"type": "http", "state": {}})
    assert request_id_of(request) == request_id_of(request)


def test_query_string_ticket_is_redacted_in_stdlib_logs(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    import logging

    create_app(settings)
    capsys.readouterr()
    logging.getLogger("uvicorn.access").info('"GET /api/v1/x?ticket=SUPERSECRETTICKET1 HTTP/1.1" 200')
    out = capsys.readouterr()
    assert "SUPERSECRETTICKET1" not in out.out + out.err


def test_openapi_is_not_served_in_production(settings: Settings) -> None:
    from app.core.config import load_settings
    from tests.conftest import make_test_env

    prod = load_settings(
        make_test_env(APP_ENV="production", LLM_PROVIDER="anthropic", SANDBOX_DRIVER="docker")
    )
    assert TestClient(create_app(prod)).get("/api/v1/openapi.json").status_code == 404
    assert TestClient(create_app(settings)).get("/api/v1/openapi.json").status_code == 200
