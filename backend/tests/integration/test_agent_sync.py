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


async def latest_sync_audit(session: AsyncSession) -> dict[str, Any]:
    query = (
        select(AuditEvent.metadata_)
        .where(AuditEvent.action == "agent.registry_synced")
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    metadata = await session.scalar(query)
    assert isinstance(metadata, dict)
    return metadata


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


async def test_a_retired_slug_can_be_taken_in_the_same_sync(
    engine: AsyncEngine, session: AsyncSession, raw: dict[str, Any]
) -> None:
    await sync(engine, load_registry(REGISTRY))
    h41_slug = next(a["slug"] for a in raw["agents"] if a["agent_id"] == "H41")
    result = await sync(engine, registry_without(raw, "H41", G37__slug=h41_slug))
    assert (result.retired, result.updated) == (1, 1)
    g37 = await session.get(AgentProfile, "G37")
    assert g37 is not None and g37.slug == h41_slug


async def test_two_agents_can_swap_slugs(
    engine: AsyncEngine, session: AsyncSession, raw: dict[str, Any]
) -> None:
    await sync(engine, load_registry(REGISTRY))
    slugs = {a["agent_id"]: a["slug"] for a in raw["agents"]}
    result = await sync(engine, registry_without(raw, A04__slug=slugs["C17"], C17__slug=slugs["A04"]))
    assert result.updated == 2
    a04, c17 = await session.get(AgentProfile, "A04"), await session.get(AgentProfile, "C17")
    assert a04 is not None and c17 is not None
    assert (a04.slug, c17.slug) == (slugs["C17"], slugs["A04"])


async def test_a_hand_edited_row_is_restored(engine: AsyncEngine, raw: dict[str, Any]) -> None:
    # The hash on the row is not proof of its content: the columns are compared (silent L4, AG-13 L2).
    await sync(engine, load_registry(REGISTRY))
    async with async_sessionmaker(engine)() as session:
        d22 = await session.get(AgentProfile, "D22")
        assert d22 is not None
        d22.tools = [*d22.tools, "code.write_file"]
        await session.commit()
    result = await sync(engine, load_registry(REGISTRY))
    assert (result.updated, result.unchanged) == (1, 52)
    async with async_sessionmaker(engine)() as session:
        d22 = await session.get(AgentProfile, "D22")
        assert d22 is not None and "code.write_file" not in d22.tools


async def test_the_audit_names_what_changed(
    engine: AsyncEngine, session: AsyncSession, raw: dict[str, Any]
) -> None:
    await sync(engine, load_registry(REGISTRY))
    a02_version = next(a["version"] for a in raw["agents"] if a["agent_id"] == "A02")
    before = parse_registry(copy.deepcopy(raw)).by_id("A02")
    changed = registry_without(raw, "H41", A02__description="Changed.", A02__version=a02_version + 1)
    after = changed.by_id("A02")
    assert before is not None and after is not None
    await sync(engine, changed)
    event = await latest_sync_audit(session)
    assert event["retired"] == ["H41"]
    assert event["updated"] == [
        {
            "agent_id": "A02",
            "version": [a02_version, a02_version + 1],
            "hash": [before.canonical_hash(), after.canonical_hash()],
        }
    ]
    assert event["inserted"] == [] and event["version_not_bumped"] == []
    assert event["unchanged"] == 51


async def test_a_change_without_a_version_bump_is_flagged(
    engine: AsyncEngine, session: AsyncSession, raw: dict[str, Any]
) -> None:
    # Same version, other content: agent_runs.agent_version no longer names one behaviour (silent M4).
    await sync(engine, load_registry(REGISTRY))
    await sync(engine, registry_without(raw, A02__description="Changed, same version."))
    assert (await latest_sync_audit(session))["version_not_bumped"] == ["A02"]


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
