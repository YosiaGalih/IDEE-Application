"""The provider key stays in the worker (spec 3.1e Review Focus 1, 2; Lead decision 3.1e #2, ADR-053).

- No response, readiness report or audit event of the provider routes ever carries the key, even when the
  ``api`` process is misconfigured with it and a check fails.
- No module the ``api`` process imports reads ``Settings.anthropic_api_key`` (mutant E8).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.main import create_app
from app.models.audit import AuditEvent
from tests.conftest import BACKEND, make_test_env
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    OWNER,
    clean_state,
    headers,
    owner_id,
    signed_in,
    workspace_root,
)
from tests.integration.test_settings_api import PRICING, PROVIDERS, InlineSmoke

KEY = "sk-ant-" + "api-misconfig-" + "w" * 24 + "g7h8"  # built, not written: a test value


@pytest.fixture
async def app(workspace_root: Path, tmp_path: Path) -> AsyncIterator[FastAPI]:  # noqa: F811
    pricing = tmp_path / "pricing.yaml"
    # No price for the default model: pricing_verified fails, so a failing check is in the report too.
    pricing.write_text(PRICING.replace("claude-test-model", "claude-other-model"), encoding="utf-8")
    settings = load_settings(
        make_test_env(COOKIE_SECURE="false", WORKSPACE_ROOT=str(workspace_root), PRICING_PATH=str(pricing),
                      ANTHROPIC_API_KEY=KEY)
    )  # fmt: skip
    app = create_app(settings)
    app.state.dependencies.smoke_jobs = InlineSmoke(app)  # the "worker" here shares these settings
    yield app
    await app.state.dependencies.aclose()


@pytest.fixture
async def owner(app: FastAPI, owner_id: uuid.UUID) -> AsyncIterator[httpx.AsyncClient]:  # noqa: F811
    client = await signed_in(app, *OWNER)
    yield client
    await client.aclose()


async def test_no_provider_response_or_audit_event_carries_the_key(
    owner: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    listed = await owner.get(PROVIDERS)
    pid = listed.json()["data"][0]["id"]
    smoke = await owner.post(f"{PROVIDERS}/{pid}/smoke-test", headers=headers(owner))
    assert smoke.status_code == 200 and smoke.json()["data"]["ready"] is False, smoke.text
    readiness = await owner.get(f"{PROVIDERS}/{pid}/readiness")
    patched = await owner.patch(f"{PROVIDERS}/{pid}", json={"enabled": False}, headers=headers(owner))
    after = await owner.get(PROVIDERS)
    assert after.json()["data"][0]["secret_hint"] == "sk-ant-…g7h8"
    async with make_sessionmaker(engine)() as session:
        audit = (await session.scalars(select(AuditEvent).where(AuditEvent.action.like("provider.%")))).all()
    texts = [r.text for r in (listed, smoke, readiness, patched, after)]
    texts.append(json.dumps([event.metadata_ for event in audit]))
    for text in texts:
        assert KEY not in text and KEY[7:-4] not in text, text


def _reads_key(source: str) -> bool:
    """An attribute ``<x>.anthropic_api_key`` or a string naming it (prose in docstrings does not count)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "anthropic_api_key":
            return True
        if isinstance(node, ast.Constant) and node.value == "anthropic_api_key":
            return True
    return False


def test_no_module_the_api_imports_reads_the_key() -> None:
    """Every module loaded by ``import app.main`` (the api process), read as source: only the settings
    class itself names ``anthropic_api_key``. The lint-imports contract also forbids the factory, the
    adapter, provider_smoke and app.worker there."""
    probe = (
        "import json, sys, app.main; print(json.dumps(sorted(m for m in sys.modules if m.startswith('app'))))"
    )
    out = subprocess.run(  # noqa: S603 - fixed argv: this interpreter and a constant probe
        [sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True, check=True, timeout=60
    ).stdout
    loaded = json.loads(out.strip().splitlines()[-1])
    assert "app.api.routes.settings" in loaded and "app.services.provider_settings" in loaded
    forbidden = {"app.llm.factory", "app.llm.anthropic_provider", "app.services.provider_smoke"}
    assert forbidden.isdisjoint(loaded), forbidden & set(loaded)
    readers = []
    for name in loaded:
        path = BACKEND / Path(*name.split("."))
        source = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
        if source.exists() and _reads_key(source.read_text(encoding="utf-8")):
            readers.append(name)
    assert readers == ["app.core.config"], readers
