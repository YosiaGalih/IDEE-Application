"""``/settings/providers*`` (API §9, §11 #8, §11.3; spec 3.1e Goal 5, Review Focus 3, 4)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.main import create_app
from app.models.audit import AuditEvent
from app.services import provider_settings, smoke_lock
from app.services.provider_smoke import SmokeEnv, run_smoke
from tests.conftest import make_test_env
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    OWNER,
    clean_state,
    headers,
    make_user,
    owner_id,
    signed_in,
    workspace_root,
)

PROVIDERS = "/api/v1/settings/providers"
PRICING = """
version: "2026-11-08-test"
currency: USD
source: https://example.test/pricing
verify_before_production: false
models:
  claude-test-model: {input: "3", output: "15", cache_write: "3.75", cache_read: "0.30"}
  claude-test-fast: {input: "1", output: "5", cache_write: "1.25", cache_read: "0.10"}
"""
FIELDS = {
    "id", "provider", "base_url", "model_policy", "effective_models", "secret_status", "secret_hint",
    "secret_checked_at", "enabled", "pricing_version", "llm_provider_mode", "readiness_status",
    "readiness_checked_at", "readiness_effective",
}  # fmt: skip


class InlineSmoke:
    """Stands in for Celery: runs the worker's smoke at once (``run``), or never (a slow worker)."""

    def __init__(self, app: FastAPI, *, run: bool = True, fail: bool = False) -> None:
        self.app, self.run, self.fail = app, run, fail
        self.calls: list[uuid.UUID] = []

    async def enqueue_provider_smoke(self, *, job_id: uuid.UUID, provider: str) -> None:
        if self.fail:
            raise ConnectionError("broker unreachable")
        self.calls.append(job_id)
        if self.run:
            deps = self.app.state.dependencies
            env = SmokeEnv(
                settings=self.app.state.settings, sessions=deps.sessions, audit=AuditWriter(Redactor())
            )
            await run_smoke(env, actor_id="worker", job_id=job_id, provider=provider)
            await smoke_lock.release(deps.redis, provider, job_id)


@pytest.fixture
async def app(workspace_root: Path, tmp_path: Path) -> AsyncIterator[FastAPI]:  # noqa: F811
    pricing = tmp_path / "pricing.yaml"
    pricing.write_text(PRICING, encoding="utf-8")
    settings = load_settings(
        make_test_env(COOKIE_SECURE="false", WORKSPACE_ROOT=str(workspace_root), PRICING_PATH=str(pricing),
                      SMOKE_WAIT_S="1")
    )  # fmt: skip
    app = create_app(settings)
    app.state.dependencies.smoke_jobs = InlineSmoke(app)
    yield app
    await app.state.dependencies.aclose()


@pytest.fixture
async def owner(app: FastAPI, owner_id: uuid.UUID) -> AsyncIterator[httpx.AsyncClient]:  # noqa: F811
    client = await signed_in(app, *OWNER)
    yield client
    await client.aclose()


async def provider_id(client: httpx.AsyncClient) -> str:
    response = await client.get(PROVIDERS)
    assert response.status_code == 200, response.text
    return response.json()["data"][0]["id"]


async def events(engine: AsyncEngine, action: str) -> list[AuditEvent]:
    async with make_sessionmaker(engine)() as session:
        query = select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id.desc()).limit(5)
        return list((await session.scalars(query)).all())


async def test_the_list_shows_the_configured_provider_before_any_smoke(owner: httpx.AsyncClient) -> None:
    response = await owner.get(PROVIDERS)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    [row] = response.json()["data"]
    assert set(row) == FIELDS
    assert (row["provider"], row["secret_status"], row["readiness_status"]) == (
        "anthropic",
        "unknown",
        "unknown",
    )
    assert row["effective_models"] == {"default": "claude-test-model", "fast": "claude-test-model",
                                       "complex": "claude-test-model"}  # fmt: skip
    assert row["readiness_effective"] == {"state": "unknown", "reason": "no_report"}
    assert (await owner.get(PROVIDERS)).json()["data"][0]["id"] == row["id"]  # one row, made once


async def test_a_smoke_that_lands_in_time_answers_with_the_report(
    owner: httpx.AsyncClient, app: FastAPI, engine: AsyncEngine
) -> None:
    pid = await provider_id(owner)
    response = await owner.post(f"{PROVIDERS}/{pid}/smoke-test", headers=headers(owner))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["data"]) == {"provider", "mode", "ready", "checks", "ran_at"}
    assert body["data"]["ready"] is True and body["data"]["mode"] == "scripted"
    assert body["meta"]["job_id"] == str(app.state.dependencies.smoke_jobs.calls[0])
    readiness = (await owner.get(f"{PROVIDERS}/{pid}/readiness")).json()["data"]
    assert readiness["readiness_status"] == "passed" and len(readiness["checks"]) == 9
    assert readiness["effective"] == {"state": "ready", "reason": "passed"}
    [requested] = await events(engine, "provider.smoke_requested")
    assert requested.actor_type == "user" and requested.metadata_["job_id"] == body["meta"]["job_id"]
    assert (await events(engine, "provider.readiness_recorded"))[0].actor_id == "worker"


async def test_a_slow_worker_gets_202_and_a_second_request_409(
    owner: httpx.AsyncClient, app: FastAPI
) -> None:
    """Lead decision 3.1e #3; mutant E11 through the API."""
    app.state.dependencies.smoke_jobs = InlineSmoke(app, run=False)
    pid = await provider_id(owner)
    first = await owner.post(f"{PROVIDERS}/{pid}/smoke-test", headers=headers(owner))
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "accepted" and uuid.UUID(first.json()["data"]["job_id"])
    second = await owner.post(f"{PROVIDERS}/{pid}/smoke-test", headers=headers(owner))
    assert second.status_code == 409 and second.json()["error"]["code"] == "CONFLICT"
    assert len(app.state.dependencies.smoke_jobs.calls) == 1


async def test_a_queue_that_is_down_answers_503_and_frees_the_lock(
    owner: httpx.AsyncClient, app: FastAPI, engine: AsyncEngine
) -> None:
    app.state.dependencies.smoke_jobs = InlineSmoke(app, fail=True)
    pid = await provider_id(owner)
    response = await owner.post(f"{PROVIDERS}/{pid}/smoke-test", headers=headers(owner))
    assert response.status_code == 503 and response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert await smoke_lock.holder(app.state.dependencies.redis, "anthropic") is None
    assert (await events(engine, "provider.smoke_requested"))[0].result == "error"


async def test_smoke_requests_are_rate_limited(
    owner: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_settings, "SMOKE_RATE_LIMIT", 2)
    pid = await provider_id(owner)
    codes = [(await owner.post(f"{PROVIDERS}/{pid}/smoke-test", headers=headers(owner))).status_code
             for _ in range(3)]  # fmt: skip
    assert codes == [200, 200, 429]


async def test_changing_the_model_policy_resets_readiness_and_is_audited(
    owner: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """Mutant E4."""
    pid = await provider_id(owner)
    assert (await owner.post(f"{PROVIDERS}/{pid}/smoke-test", headers=headers(owner))).status_code == 200
    response = await owner.patch(f"{PROVIDERS}/{pid}", json={"model_policy": {"fast": "claude-test-fast"}},
                                 headers=headers(owner))  # fmt: skip
    assert response.status_code == 200, response.text
    row = response.json()["data"]
    assert row["model_policy"] == {"fast": "claude-test-fast"} and row["readiness_status"] == "unknown"
    assert row["effective_models"]["fast"] == "claude-test-fast"
    assert row["readiness_effective"]["state"] == "unknown"
    [updated] = await events(engine, "provider.updated")
    assert updated.metadata_["changes"]["model_policy"] == [{}, {"fast": "claude-test-fast"}]
    assert updated.metadata_["readiness_reset"] is True
    # Merged per alias; an empty value drops one.
    again = await owner.patch(f"{PROVIDERS}/{pid}", json={"model_policy": {"complex": "claude-test-model",
                              "fast": ""}}, headers=headers(owner))  # fmt: skip
    assert again.json()["data"]["model_policy"] == {"complex": "claude-test-model"}


async def test_disabling_the_provider_is_said(owner: httpx.AsyncClient, engine: AsyncEngine) -> None:
    pid = await provider_id(owner)
    response = await owner.patch(f"{PROVIDERS}/{pid}", json={"enabled": False}, headers=headers(owner))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["readiness_effective"] == {"state": "disabled", "reason": "disabled"}
    assert (await events(engine, "provider.updated"))[0].metadata_["changes"] == {"enabled": [True, False]}


@pytest.mark.parametrize(
    "body",
    [
        {"base_url": "https://attacker.example"},
        {"base_url": "https://attacker.example", "enabled": False},
        {"api_key": "sk-ant-" + "not-a-real-key-000000"},
        {"secret_ref": "OTHER"},
        {"model_policy": {"fast": "gpt-4o"}},
        {"model_policy": {"turbo": "claude-x"}},
        {},
        {"enabled": None},
    ],
)
async def test_patch_refuses_what_it_may_not_change(owner: httpx.AsyncClient, body: dict[str, Any]) -> None:
    """Mutant E6: base_url and keys are not fields; model ids are checked."""
    pid = await provider_id(owner)
    response = await owner.patch(f"{PROVIDERS}/{pid}", json=body, headers=headers(owner))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "attacker" not in response.text and "not-a-real-key" not in response.text


async def test_patch_needs_the_csrf_token(owner: httpx.AsyncClient) -> None:
    pid = await provider_id(owner)
    response = await owner.patch(f"{PROVIDERS}/{pid}", json={"enabled": False})
    assert response.status_code == 403


async def test_an_unknown_provider_is_404(owner: httpx.AsyncClient) -> None:
    missing = uuid.uuid4()
    assert (await owner.get(f"{PROVIDERS}/{missing}/readiness")).status_code == 404
    assert (
        await owner.patch(f"{PROVIDERS}/{missing}", json={"enabled": True}, headers=headers(owner))
    ).status_code == 404
    assert (await owner.post(f"{PROVIDERS}/{missing}/smoke-test", headers=headers(owner))).status_code == 404


async def test_every_route_needs_settings_providers(
    app: FastAPI, owner: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """Mutant E7: a developer is refused everywhere; an admin is let in; nobody signed out gets in."""
    pid = await provider_id(owner)
    await make_user(engine, "dev@example.test", ("developer", "global"))
    await make_user(engine, "admin@example.test", ("admin", "global"))
    developer = await signed_in(app, "dev@example.test", "user-long-passphrase")
    admin = await signed_in(app, "admin@example.test", "user-long-passphrase")
    anonymous = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        calls = [
            ("GET", PROVIDERS, None),
            ("GET", f"{PROVIDERS}/{pid}/readiness", None),
            ("PATCH", f"{PROVIDERS}/{pid}", {"enabled": False}),
            ("POST", f"{PROVIDERS}/{pid}/smoke-test", None),
        ]
        for method, url, body in calls:
            refused = await developer.request(method, url, json=body, headers=headers(developer))
            assert refused.status_code == 403, (method, url, refused.text)
            assert (await anonymous.request(method, url, json=body)).status_code in (401, 403)
        assert (await admin.get(PROVIDERS)).status_code == 200
    finally:
        await developer.aclose()
        await admin.aclose()
        await anonymous.aclose()
