"""``tool_calls``, the approvals status guard and one open ``tool_r3`` per task (migration 0008; spec WP-3.1d
Goals 4 to 6)."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.approvals import Approval
from app.models.projects import Project
from app.models.registry import Base
from app.models.tasks import Task
from app.models.tools import ToolCall
from tests.conftest import alembic_config, allow_approval_status_writes, reset_data
from tests.integration.test_approval_tables import approval
from tests.integration.test_task_schema import make_project, make_task


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


def call(task: Task, **overrides: object) -> ToolCall:
    values: dict[str, object] = {
        "task_id": task.id,
        "agent_id": "B14",
        "tool_name": "repo.read_file",
        "risk_level": 0,
        "input_hash": "a" * 64,
        "sanitized_input": {"path": "README.md"},
        "status": "ok",
    }
    values.update(overrides)
    return ToolCall(**values)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("status", "done", "ck_tool_calls_status"),
        ("risk_level", 5, "ck_tool_calls_risk_level"),
        ("input_hash", "A" * 64, "ck_tool_calls_input_hash"),
        ("idempotency_key", "z" * 64, "ck_tool_calls_idempotency_key"),
        ("output_summary", "x" * 2049, "ck_tool_calls_output_summary"),
        ("duration_ms", -1, "ck_tool_calls_duration_ms"),
    ],
)
async def test_checks(session: AsyncSession, field: str, value: object, constraint: str) -> None:
    task = await make_task(session, await make_project(session))
    session.add(call(task, **{field: value}))
    with pytest.raises(IntegrityError, match=constraint):
        await session.flush()


async def test_every_status_is_accepted(session: AsyncSession) -> None:
    task = await make_task(session, await make_project(session))
    for status in ("running", "ok", "error", "denied", "timeout", "approval_required", "cancelled"):
        session.add(call(task, status=status))
    await session.flush()


async def test_idempotency_key_is_unique(session: AsyncSession) -> None:
    task = await make_task(session, await make_project(session))
    session.add(call(task, idempotency_key="b" * 64))
    session.add(call(task, idempotency_key=None))
    session.add(call(task, idempotency_key=None))  # rows without a key never collide
    await session.flush()
    session.add(call(task, idempotency_key="b" * 64))
    with pytest.raises(IntegrityError, match="uq_tool_calls_idempotency_key"):
        await session.flush()


async def test_rows_go_with_their_task_and_keep_a_purged_approval(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    pending = approval(project, task_id=task.id, action_type="tool_r3")
    session.add(pending)
    await session.flush()
    session.add(call(task, approval_id=pending.id, status="approval_required"))
    await session.flush()
    await session.execute(delete(Approval).where(Approval.id == pending.id))
    row = await session.scalar(select(ToolCall).execution_options(populate_existing=True))
    assert row is not None and row.approval_id is None
    await session.execute(delete(Project).where(Project.id == project.id))
    assert await session.scalar(select(func.count()).select_from(ToolCall)) == 0


async def test_one_open_tool_r3_per_task(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    other = await make_task(session, project)
    await allow_approval_status_writes(session)
    session.add(approval(project, task_id=task.id, action_type="tool_r3", status="superseded"))
    session.add(approval(project, task_id=task.id, action_type="tool_r3"))
    session.add(approval(project, task_id=other.id, action_type="tool_r3"))
    await session.flush()
    session.add(approval(project, task_id=task.id, action_type="tool_r3", status="approved"))
    with pytest.raises(IntegrityError, match="uq_approvals_open_tool_r3"):
        await session.flush()


# --- migration ---------------------------------------------------------------------------------------------


def _table_diff(connection: Connection) -> list[object]:
    tables = {"tool_calls", "approvals"}

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


def test_migration_round_trip(migrated_database: str) -> None:
    config = alembic_config(migrated_database)
    command.downgrade(config, "-1")
    command.upgrade(config, "head")
