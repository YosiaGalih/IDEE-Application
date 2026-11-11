"""provider_configs (DATA_MODEL, ADR-032; spec 3.1e Goal 1): checks, defaults, one row per provider, and the
model matching migration 0009."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.providers import ProviderConfig
from app.models.registry import Base
from tests.conftest import alembic_config, reset_data


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("provider", "openai", "ck_provider_configs_provider"),
        ("readiness_status", "maybe", "ck_provider_configs_readiness_status"),
        ("secret_status", "leaked", "ck_provider_configs_secret_status"),
        ("llm_provider_mode", "other", "ck_provider_configs_llm_provider_mode"),
        ("secret_fingerprint", "not-hex-at-all!!", "ck_provider_configs_secret_fingerprint"),
    ],
)
async def test_checks(session: AsyncSession, field: str, value: object, constraint: str) -> None:
    row = ProviderConfig(provider="anthropic")
    setattr(row, field, value)
    session.add(row)
    with pytest.raises(IntegrityError, match=constraint):
        await session.flush()


async def test_defaults_say_nothing_is_known_yet(session: AsyncSession) -> None:
    session.add(ProviderConfig(provider="anthropic"))
    await session.flush()
    row = (await session.scalars(select(ProviderConfig))).one()
    await session.refresh(row)
    assert (row.readiness_status, row.secret_status, row.enabled) == ("unknown", "unknown", True)
    assert (row.readiness_report, row.model_policy, row.secret_ref) == ({}, {}, "ANTHROPIC_API_KEY")
    assert (
        row.readiness_checked_at is None and row.secret_fingerprint is None and row.llm_provider_mode is None
    )


async def test_one_row_per_provider(session: AsyncSession) -> None:
    session.add(ProviderConfig(provider="anthropic"))
    await session.flush()
    session.add(ProviderConfig(provider="anthropic"))
    with pytest.raises(IntegrityError, match="uq_provider_configs_provider"):
        await session.flush()


def _table_diff(connection: Connection) -> list[object]:
    def touches(change: object) -> bool:
        if isinstance(change, list | tuple):
            return any(touches(item) for item in change)  # pyright: ignore[reportUnknownVariableType]
        if isinstance(change, str):
            return change == "provider_configs"
        table = getattr(change, "table", None)
        return "provider_configs" in (getattr(change, "name", None), getattr(table, "name", None))

    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "compare_server_default": True}
    )
    return [change for change in compare_metadata(context, Base.metadata) if touches(change)]


async def test_models_match_the_migration(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        assert await connection.run_sync(_table_diff) == []


def test_migration_round_trip(migrated_database: str) -> None:
    config = alembic_config(migrated_database)
    command.downgrade(config, "-1")
    command.upgrade(config, "head")
