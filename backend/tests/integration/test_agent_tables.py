"""``agent_profiles`` and ``agent_runs`` (DATA_MODEL M07, migration 0006; spec WP-3.1b goal 5)."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.agents import AgentProfile, AgentRun
from app.models.registry import Base
from app.models.tasks import Task
from tests.conftest import alembic_config, reset_data
from tests.integration.test_task_schema import make_project, make_task


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


def profile(agent_id: str = "A02", **overrides: object) -> AgentProfile:
    values: dict[str, object] = {
        "agent_id": agent_id,
        "slug": f"agent_{agent_id.lower()}",
        "name": "Requirement Analyst",
        "group": "planning_intent",
        "version": 1,
        "enabled": True,
        "mvp_mode": "llm",
        "priority": "P0",
        "description": "d",
        "purpose": "p",
        "triggers": ["task_created"],
        "capabilities": ["requirements.extract"],
        "tools": ["repo.read_file", "submit_result"],
        "permission_profile": {"approval_grant": "deny"},
        "model_tier": "default",
        "limits": {"max_iterations": 8, "timeout_seconds": 300},
        "inputs_tools_note": "n",
        "output_note": "n",
        "access_note": "n",
        "registry_hash": "0" * 64,
    }
    values.update(overrides)
    return AgentProfile(**values)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("mvp_mode", "agentic", "ck_agent_profiles_mvp_mode"),
        ("priority", "P9", "ck_agent_profiles_priority"),
        ("model_tier", "huge", "ck_agent_profiles_model_tier"),
        ("group", "marketing", "ck_agent_profiles_group"),
        ("target_mode", "planned", "ck_agent_profiles_target_mode"),
    ],
)
async def test_profile_enum_checks(session: AsyncSession, field: str, value: str, constraint: str) -> None:
    session.add(profile(**{field: value}))
    with pytest.raises(IntegrityError, match=constraint):
        await session.flush()


@pytest.mark.parametrize("permissions", [{"approval_grant": "allow"}, {}])
async def test_no_row_can_grant_approvals(session: AsyncSession, permissions: dict[str, str]) -> None:
    # Defence in depth under the loader (AG-13 L2): the runtime role may UPDATE this table.
    session.add(profile(permission_profile=permissions))
    with pytest.raises(IntegrityError, match="ck_agent_profiles_approval_grant_deny"):
        await session.flush()


async def test_slugs_are_unique_among_live_agents_only(session: AsyncSession) -> None:
    session.add(profile("A02", slug="analyst", in_registry=False, enabled=False))
    session.add(profile("A03", slug="analyst"))  # a retired agent's slug may be taken
    await session.flush()
    session.add(profile("A04", slug="analyst"))
    with pytest.raises(IntegrityError, match="uq_agent_profiles_slug"):
        await session.flush()


async def test_profile_defaults(session: AsyncSession) -> None:
    session.add(profile())
    await session.flush()
    stored = await session.get(AgentProfile, "A02")
    assert stored is not None
    await session.refresh(stored)
    assert stored.in_registry is True
    assert stored.created_at is not None


async def run_for(session: AsyncSession, **overrides: object) -> AgentRun:
    project = await make_project(session)
    task = await make_task(session, project)
    session.add(profile())
    await session.flush()
    values: dict[str, object] = {"task_id": task.id, "agent_id": "A02", "agent_version": 1, "step": "intake"}
    values.update(overrides)
    run = AgentRun(**values)
    session.add(run)
    await session.flush()
    return run


async def test_run_defaults(session: AsyncSession) -> None:
    run = await run_for(session)
    await session.refresh(run)
    assert (run.status, run.iterations, run.input) == ("running", 0, {})
    assert run.started_at is not None
    assert (run.estimated_cost, run.cost_status, run.prompt_version) == (None, None, None)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("status", "done", "ck_agent_runs_status"),
        ("cost_status", "guessed", "ck_agent_runs_cost_status"),
    ],
)
async def test_run_enum_checks(session: AsyncSession, field: str, value: str, constraint: str) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        await run_for(session, **{field: value})


async def test_approval_required_is_a_run_status(session: AsyncSession) -> None:
    run = await run_for(session, status="approval_required")  # ADR-025, from the start
    assert run.status == "approval_required"


async def test_profile_with_runs_cannot_be_deleted(session: AsyncSession) -> None:
    await run_for(session)
    stored = await session.get(AgentProfile, "A02")
    await session.delete(stored)
    with pytest.raises(IntegrityError, match="fk_agent_runs_agent_id_agent_profiles"):
        await session.flush()


async def test_task_delete_cascades_runs(session: AsyncSession) -> None:
    run = await run_for(session)
    task = await session.get(Task, run.task_id)
    await session.delete(task)
    await session.flush()
    assert await session.scalar(select(func.count()).select_from(AgentRun)) == 0


def _agent_table_diff(connection: Connection) -> list[object]:
    tables = {"agent_profiles", "agent_runs"}

    def touches(change: object) -> bool:
        # Table/index/constraint diffs carry objects; column diffs are (lists of) tuples naming the table.
        if isinstance(change, list):
            return any(touches(item) for item in change)
        if isinstance(change, tuple):
            return any(touches(item) for item in change)
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
        assert await connection.run_sync(_agent_table_diff) == []


def test_migration_round_trip(migrated_database: str) -> None:
    config = alembic_config(migrated_database)
    command.downgrade(config, "-1")
    command.upgrade(config, "head")
