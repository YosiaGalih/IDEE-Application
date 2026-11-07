"""The approvals_status_guard trigger (migration 0008): CLAUDE.md #5 in the database (3.1c1 AG-13 C3; AG-13
3.1d1 LOW). An approval starts pending; its status changes only through app.services.approval_store and only
along the approval cycle; what it is for never changes after it is requested."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.approvals import Approval
from tests.conftest import allow_approval_status_writes
from tests.integration.test_approval_tables import approval
from tests.integration.test_task_schema import make_project


async def test_an_approval_cannot_be_inserted_decided(session: AsyncSession) -> None:
    project = await make_project(session)
    session.add(approval(project, status="approved"))
    with pytest.raises(DBAPIError, match="approvals start as pending"):
        await session.flush()


async def test_a_status_change_outside_approval_store_is_refused(session: AsyncSession) -> None:
    project = await make_project(session)
    row = approval(project)
    session.add(row)
    await session.flush()
    with pytest.raises(DBAPIError, match="only change through approval_store"):
        await session.execute(update(Approval).where(Approval.id == row.id).values(status="approved"))


async def test_raw_sql_is_refused_too(engine: AsyncEngine, session: AsyncSession) -> None:
    project = await make_project(session)
    row = approval(project)
    session.add(row)
    await session.commit()
    async with engine.connect() as conn:
        with pytest.raises(DBAPIError, match="only change through approval_store"):
            await conn.execute(
                text("UPDATE approvals SET status = 'consumed' WHERE id = :id"), {"id": row.id}
            )


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("pending", "consumed"),  # never without a decision
        ("rejected", "approved"),
        ("expired", "approved"),
        ("consumed", "approved"),  # single use
        ("superseded", "pending"),
        ("approved", "pending"),
        ("approved", "rejected"),
    ],
)
async def test_the_writer_still_follows_the_cycle(session: AsyncSession, start: str, target: str) -> None:
    project = await make_project(session)
    await allow_approval_status_writes(session)
    row = approval(project, status=start)
    session.add(row)
    await session.flush()
    with pytest.raises(DBAPIError, match=f"cannot move from {start} to {target}"):
        await session.execute(update(Approval).where(Approval.id == row.id).values(status=target))


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("payload_hash", "b" * 64),
        ("payload", {"other": "action"}),
        ("action_type", "tool_r3"),
        ("requested_by", "system:someone_else"),
        ("risk_level", 2),
    ],
)
async def test_what_was_approved_never_changes(session: AsyncSession, column: str, value: object) -> None:
    # AG-13 3.1d1: even the writer may not point an approved decision at another action.
    project = await make_project(session)
    await allow_approval_status_writes(session)
    row = approval(project, status="approved")
    session.add(row)
    await session.flush()
    with pytest.raises(DBAPIError, match="never changes after it is requested"):
        await session.execute(update(Approval).where(Approval.id == row.id).values({column: value}))


async def test_other_columns_stay_writable(session: AsyncSession) -> None:
    # The handler bookkeeping (ADR-024) is not a status change.
    project = await make_project(session)
    row = approval(project)
    session.add(row)
    await session.flush()
    await session.execute(
        update(Approval)
        .where(Approval.id == row.id)
        .values(handler_attempts=1, handler_error="x", updated_at=datetime(2026, 11, 7, tzinfo=UTC))
    )
