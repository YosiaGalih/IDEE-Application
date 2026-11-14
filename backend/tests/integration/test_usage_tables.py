"""``usage_records``, the usage ledger (migration 0010; spec WP-3.1f Keputusan Lead #3, #4, #12): checks, one
row per call attempt, the cascades, ten decimal places, and ``agent_runs.estimated_cost`` widened with it."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.models.agents import AgentRun
from app.models.projects import Project
from app.models.registry import Base
from app.models.tasks import Task
from app.models.usage import UsageRecord
from tests.conftest import alembic_config, reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.tool_fakes import make_run


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


def record(project_id: uuid.UUID, **overrides: object) -> UsageRecord:
    """A valid ``ok`` attempt; each test breaks one rule."""
    values: dict[str, object] = {
        "project_id": project_id,
        "call_id": uuid.uuid4(),
        "attempt": 1,
        "retry_count": 0,
        "provider": "scripted",
        "model": "claude-haiku-5-5",
        "request_id": "req_1",
        "status": "ok",
        "error_kind": None,
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "input_token_bound": 100,
        "output_token_cap": 8192,
        "estimated_cost": Decimal("0.0000035"),
        "cost_status": "estimated",
        "pricing_version": "2026-11-05",
        "duration_ms": 12,
    }
    values.update(overrides)
    return UsageRecord(**values)


NO_TOKENS: dict[str, object] = {
    "input_tokens": None,
    "output_tokens": None,
    "cache_creation_tokens": None,
    "cache_read_tokens": None,
}

# One case per CHECK, each breaking that rule only (PostgreSQL reports the first failing CHECK by name).
CHECK_CASES: dict[str, dict[str, object]] = {
    "attempt": {"attempt": 0, "retry_count": -1},
    "retry_count": {"attempt": 2, "retry_count": 0},
    "status": {"status": "done", "cost_status": "partial"},
    "error_kind": {"status": "error", "error_kind": "boom", "cost_status": "partial"},
    "ok_has_no_error": {"error_kind": "transient"},
    "cancelled_kind": {"status": "cancelled", "error_kind": "transient", "cost_status": "partial"},
    "tokens": {"input_tokens": -1},
    "tokens_together": {"output_tokens": None},
    "input_token_bound": {"input_token_bound": -1},
    "output_token_cap": {"output_token_cap": 0},
    "estimated_cost": {"estimated_cost": Decimal("-0.1")},
    "actual_cost": {"actual_cost": Decimal("0.1")},
    "currency": {"currency": "EUR"},
    "cost_status": {"cost_status": "free"},
    "cost_matches_status": {"cost_status": "unavailable"},
    "failed_not_estimated": {"status": "error", "error_kind": "transient"},
    "no_tokens_unavailable": NO_TOKENS,
    "duration_ms": {"duration_ms": -1},
    "run_has_task": {"agent_id": None},  # the run is attached in the test
}


@pytest.mark.parametrize("rule", list(CHECK_CASES))
async def test_checks(session: AsyncSession, rule: str) -> None:
    project = await make_project(session)
    overrides = dict(CHECK_CASES[rule])
    if rule == "run_has_task":
        task = await make_task(session, project)
        overrides |= {"task_id": task.id, "agent_run_id": await make_run(session, task.id)}
    session.add(record(project.id, **overrides))
    with pytest.raises(IntegrityError, match=f"ck_usage_records_{rule}"):
        await session.flush()


async def test_cancelled_kind_also_refuses_a_cancelled_kind_on_an_error(session: AsyncSession) -> None:
    project = await make_project(session)
    session.add(record(project.id, status="error", error_kind="cancelled", cost_status="partial"))
    with pytest.raises(IntegrityError, match="ck_usage_records_cancelled_kind"):
        await session.flush()


async def test_every_attempt_shape_is_accepted(session: AsyncSession) -> None:
    """The rows the writer produces: ok, failed with partial usage, failed or cancelled without usage."""
    project = await make_project(session)
    task = await make_task(session, project)
    run = await make_run(session, task.id)
    session.add(record(project.id, task_id=task.id, agent_run_id=run, agent_id="B14"))
    session.add(record(project.id, status="error", error_kind="transient", cost_status="partial"))
    session.add(record(project.id, status="cancelled", error_kind="cancelled", cost_status="partial"))
    unavailable = {**NO_TOKENS, "estimated_cost": None, "cost_status": "unavailable"}
    session.add(record(project.id, status="error", error_kind="rate_limited", request_id=None, **unavailable))
    session.add(record(project.id, status="error", error_kind=None, **unavailable))
    session.add(record(project.id, status="cancelled", error_kind="cancelled", **unavailable))
    session.add(record(project.id, model="claude-unpriced", estimated_cost=None, cost_status="unavailable"))
    await session.flush()
    rows = (await session.scalars(select(UsageRecord))).all()
    assert len(rows) == 7
    assert {row.currency for row in rows} == {"USD"}


async def test_one_row_per_call_attempt(session: AsyncSession) -> None:
    project = await make_project(session)
    call_id = uuid.uuid4()
    session.add(record(project.id, call_id=call_id))
    session.add(record(project.id, call_id=call_id, attempt=2, retry_count=1))
    await session.flush()
    session.add(record(project.id, call_id=call_id))
    with pytest.raises(IntegrityError, match="uq_usage_records_call_id_attempt"):
        await session.flush()


async def _count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(UsageRecord)) or 0


async def test_rows_go_with_their_project(session: AsyncSession) -> None:
    project = await make_project(session)
    other = await make_project(session, slug="other")
    session.add(record(project.id))
    session.add(record(other.id))
    await session.flush()
    await session.execute(delete(Project).where(Project.id == project.id))
    assert await _count(session) == 1


async def test_rows_go_with_their_task(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    session.add(record(project.id, task_id=task.id))
    session.add(record(project.id))
    await session.flush()
    await session.execute(delete(Task).where(Task.id == task.id))
    assert await _count(session) == 1


async def test_rows_go_with_their_run(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    run = await make_run(session, task.id)
    session.add(record(project.id, task_id=task.id, agent_run_id=run, agent_id="B14"))
    session.add(record(project.id, task_id=task.id))
    await session.flush()
    await session.execute(delete(AgentRun).where(AgentRun.id == run))
    assert await _count(session) == 1


async def test_cost_keeps_ten_decimal_places(session: AsyncSession) -> None:
    project = await make_project(session)
    row = record(project.id, estimated_cost=Decimal("0.0000000001"))
    session.add(row)
    await session.flush()
    stored = await session.scalar(
        select(UsageRecord.estimated_cost)
        .where(UsageRecord.id == row.id)
        .execution_options(populate_existing=True)
    )
    assert stored == Decimal("0.0000000001")
    assert stored is not None and stored > 0


async def test_run_cost_keeps_ten_decimal_places(session: AsyncSession) -> None:
    """Keputusan Lead #12: a run total must not round away what the ledger keeps."""
    project = await make_project(session)
    task = await make_task(session, project)
    run = await make_run(session, task.id)
    await session.execute(
        text("UPDATE agent_runs SET estimated_cost = :cost WHERE id = :id"),
        {"cost": Decimal("0.0000000001"), "id": run},
    )
    assert await session.scalar(select(AgentRun.estimated_cost).where(AgentRun.id == run)) == Decimal(
        "0.0000000001"
    )


# --- migration ---------------------------------------------------------------------------------------------


def _table_diff(connection: Connection) -> list[object]:
    tables = {"usage_records", "agent_runs"}

    def touches(change: object) -> bool:
        if isinstance(change, list | tuple):
            return any(touches(item) for item in change)  # pyright: ignore[reportUnknownVariableType]
        if isinstance(change, str):
            return change in tables
        table = getattr(change, "table", None)
        return getattr(change, "name", None) in tables or getattr(table, "name", None) in tables

    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "compare_server_default": True}
    )
    return [change for change in compare_metadata(context, Base.metadata) if touches(change)]


async def test_models_match_the_migration(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        assert await connection.run_sync(_table_diff) == []


async def _schema_state(database_url: str) -> tuple[bool, tuple[int, int]]:
    """(usage_records exists, agent_runs.estimated_cost (precision, scale))."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(text("SELECT to_regclass('public.usage_records') IS NOT NULL"))
            row = (
                await connection.execute(
                    text(
                        "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
                        "WHERE table_name = 'agent_runs' AND column_name = 'estimated_cost'"
                    )
                )
            ).one()
    finally:
        await engine.dispose()
    return bool(exists), (row[0], row[1])


def test_migration_round_trip(migrated_database: str) -> None:
    config = alembic_config(migrated_database)
    assert asyncio.run(_schema_state(migrated_database)) == (True, (20, 10))
    # Pinned to the revision under 0010: "-1" stops meaning 0010 once a later migration is head.
    command.downgrade(config, "0009_provider_configs")
    # The downgrade gives agent_runs back the 0006 precision (Keputusan Lead #12).
    assert asyncio.run(_schema_state(migrated_database)) == (False, (12, 6))
    command.upgrade(config, "head")
    assert asyncio.run(_schema_state(migrated_database)) == (True, (20, 10))
