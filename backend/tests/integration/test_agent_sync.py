"""Registry → ``agent_profiles`` sync (FR-041; spec WP-3.1b goal 6): at API start and with ``seed-agents``."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.agents.registry import AgentRegistry, RegistryError, load_registry, parse_registry
from app.cli import main as cli_main
from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.redaction import Redactor
from app.main import create_app
from app.models.agents import AgentProfile
from app.models.audit import AuditEvent
from app.services.agents import SyncResult, sync_registry
from tests.conftest import make_test_env, reset_data

REGISTRY = Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml"
AUDIT = AuditWriter(Redactor())


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def registry_without(raw: dict[str, Any], agent_id: str | None = None, **changes: Any) -> AgentRegistry:
    data = copy.deepcopy(raw)
    if agent_id is not None:
        data["agents"] = [a for a in data["agents"] if a["agent_id"] != agent_id]
    for key, value in changes.items():
        target, field = key.split("__")
        next(a for a in data["agents"] if a["agent_id"] == target)[field] = value
    return parse_registry(data)


async def sync(engine: AsyncEngine, registry: AgentRegistry) -> SyncResult:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        result = await sync_registry(session, registry, AUDIT)
        await session.commit()
    return result


async def sync_audits(session: AsyncSession) -> int:
    query = select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "agent.registry_synced")
    return await session.scalar(query) or 0


async def test_first_sync_inserts_all(engine: AsyncEngine, session: AsyncSession) -> None:
    before = await sync_audits(session)
    result = await sync(engine, load_registry(REGISTRY))
    assert result == SyncResult(inserted=53, updated=0, retired=0, unchanged=0)
    assert await session.scalar(select(func.count()).select_from(AgentProfile)) == 53
    a02 = await session.get(AgentProfile, "A02")
    assert a02 is not None
    assert a02.permission_profile["approval_grant"] == "deny"
    assert a02.permission_profile["data_sensitivity_max"] == "confidential"
    assert a02.registry_hash == load_registry(REGISTRY).by_id("A02").canonical_hash()  # type: ignore[union-attr]
    assert await sync_audits(session) == before + 1


async def test_second_sync_changes_nothing(engine: AsyncEngine, session: AsyncSession) -> None:
    await sync(engine, load_registry(REGISTRY))
    before = await sync_audits(session)
    result = await sync(engine, load_registry(REGISTRY))
    assert result == SyncResult(inserted=0, updated=0, retired=0, unchanged=53)
    assert await sync_audits(session) == before  # nothing changed, nothing audited


async def test_changed_entry_is_updated(
    engine: AsyncEngine, session: AsyncSession, raw: dict[str, Any]
) -> None:
    await sync(engine, load_registry(REGISTRY))
    changed = registry_without(raw, A02__description="A new description.")
    result = await sync(engine, changed)
    assert (result.updated, result.unchanged) == (1, 52)
    a02 = await session.get(AgentProfile, "A02")
    assert a02 is not None
    assert a02.description == "A new description."


async def test_missing_id_is_retired_not_deleted(
    engine: AsyncEngine, session: AsyncSession, raw: dict[str, Any]
) -> None:
    await sync(engine, load_registry(REGISTRY))
    result = await sync(engine, registry_without(raw, "H41"))
    assert (result.retired, result.unchanged) == (1, 52)
    h41 = await session.get(AgentProfile, "H41")
    assert h41 is not None
    assert (h41.in_registry, h41.enabled) == (False, False)
    back = await sync(engine, load_registry(REGISTRY))
    assert back.updated == 1  # the id came back: in the registry again, enabled as the file says
    await session.refresh(h41)
    assert (h41.in_registry, h41.enabled) == (True, True)


async def test_concurrent_syncs(engine: AsyncEngine, session: AsyncSession) -> None:
    registry = load_registry(REGISTRY)
    first, second = await asyncio.gather(sync(engine, registry), sync(engine, registry))
    assert sorted([first.inserted, second.inserted]) == [0, 53]
    assert await session.scalar(select(func.count()).select_from(AgentProfile)) == 53


def test_api_syncs_at_start(migrated_database: str) -> None:
    with TestClient(create_app(load_settings(make_test_env()))) as client:
        assert client.get("/api/v1/health/live").status_code == 200

    async def count() -> int:  # a loop of its own: TestClient ran the app in another
        engine = create_async_engine(migrated_database)
        try:
            async with async_sessionmaker(engine)() as session:
                return await session.scalar(select(func.count()).select_from(AgentProfile)) or 0
        finally:
            await engine.dispose()

    assert asyncio.run(count()) == 53


def test_api_refuses_to_start_with_an_invalid_registry(tmp_path: Path, raw: dict[str, Any]) -> None:
    data = copy.deepcopy(raw)
    next(a for a in data["agents"] if a["agent_id"] == "A02")["permissions"]["approval_grant"] = "allow"
    bad = tmp_path / "registry.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    app = create_app(load_settings(make_test_env(AGENT_REGISTRY_PATH=str(bad))))
    with pytest.raises(RegistryError) as raised, TestClient(app):
        pass
    assert any(problem.startswith("A02: ") for problem in raised.value.problems)


def test_seed_agents_cli(
    monkeypatch: pytest.MonkeyPatch, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    assert cli_main(["seed-agents"]) == 0
    assert "inserted=53" in capsys.readouterr().out
    assert cli_main(["seed-agents"]) == 0
    assert "unchanged=53" in capsys.readouterr().out
