"""workflow.states.transition: the only writer of tasks.status (CLAUDE.md #4, ARCHITECTURE §5.1)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.events import discard_pending
from app.core.security import hash_password
from app.models.audit import AuditEvent
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task, TaskEvent
from app.workflow.states import Actor, TaskStatus, transition

ACTOR = Actor("system", "orchestrator")


@pytest.fixture
async def task_id(engine: AsyncEngine) -> uuid.UUID:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM projects"))
        await conn.execute(text("DELETE FROM users"))
    async with make_sessionmaker(engine)() as session:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.flush()
        task = Task(project_id=project.id, title="t", prompt="p", workflow_type="create", created_by=owner.id)
        session.add(task)
        await session.commit()
        return task.id


async def load(session: AsyncSession, task_id: uuid.UUID) -> Task:
    task = await session.get(Task, task_id, populate_existing=True)
    assert task is not None
    return task


async def walk(session: AsyncSession, task_id: uuid.UUID, *statuses: TaskStatus) -> Task:
    task = await load(session, task_id)
    for status in statuses:
        await transition(session, task, status, reason=None, actor=ACTOR)
    await session.commit()
    return task


async def test_valid_transition_updates_row_audit_and_event(
    session: AsyncSession, task_id: uuid.UUID
) -> None:
    task = await load(session, task_id)
    await transition(session, task, TaskStatus.PLANNED, reason="plan ready", actor=ACTOR)
    await session.commit()
    assert (task.status, task.status_reason, task.version) == ("planned", "plan ready", 2)
    fresh = await load(session, task_id)
    assert (fresh.status, fresh.version, fresh.event_seq) == ("planned", 2, 1)
    event = await session.scalar(select(TaskEvent).where(TaskEvent.task_id == task_id))
    assert event is not None and (event.seq, event.type) == (1, "task.status_changed")
    assert event.payload == {"from": "draft", "to": "planned", "reason": "plan ready"}
    audit = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "task.status_changed", AuditEvent.resource_id == str(task_id)
        )
    )
    assert audit is not None
    assert (audit.actor_type, audit.actor_id, audit.resource_type) == ("system", "orchestrator", "task")
    assert audit.project_id == task.project_id
    assert audit.metadata_ == {"from": "draft", "to": "planned", "reason": "plan ready"}


async def test_invalid_transition_is_rejected_without_writes(
    session: AsyncSession, task_id: uuid.UUID
) -> None:
    task = await load(session, task_id)
    with pytest.raises(AppError) as caught:
        await transition(session, task, TaskStatus.COMPLETED, reason=None, actor=ACTOR)
    assert (caught.value.code, caught.value.http_status) == ("INVALID_TRANSITION", 409)
    assert caught.value.details == [{"from": "draft", "to": "completed"}]
    await session.rollback()
    fresh = await load(session, task_id)
    assert (fresh.status, fresh.version, fresh.event_seq) == ("draft", 1, 0)


async def test_concurrent_writers_one_wins(engine: AsyncEngine, task_id: uuid.UUID) -> None:
    sessions = make_sessionmaker(engine)
    async with sessions() as first, sessions() as second:
        a = await load(first, task_id)
        b = await load(second, task_id)
        await transition(first, a, TaskStatus.PLANNED, reason=None, actor=ACTOR)
        await first.commit()
        with pytest.raises(AppError) as caught:
            await transition(second, b, TaskStatus.CANCELLED, reason=None, actor=ACTOR)
        assert (caught.value.code, caught.value.http_status) == ("VERSION_CONFLICT", 409)
        await second.rollback()
        discard_pending(second)
    async with sessions() as check:
        fresh = await load(check, task_id)
        assert (fresh.status, fresh.version) == ("planned", 2)


async def test_stale_task_object_conflicts(engine: AsyncEngine, task_id: uuid.UUID) -> None:
    sessions = make_sessionmaker(engine)
    async with sessions() as session:
        stale = await load(session, task_id)
    async with sessions() as other:
        await walk(other, task_id, TaskStatus.PLANNED, TaskStatus.QUEUED)
    async with sessions() as session:
        session.add(stale)
        # Locally still "draft"; draft -> planned is allowed, but the row moved on.
        with pytest.raises(AppError) as caught:
            await transition(session, stale, TaskStatus.PLANNED, reason=None, actor=ACTOR)
        assert caught.value.code == "VERSION_CONFLICT"


async def test_reopen_and_remediation_increment_iteration(session: AsyncSession, task_id: uuid.UUID) -> None:
    S = TaskStatus
    task = await walk(
        session, task_id, S.PLANNED, S.QUEUED, S.RUNNING, S.VALIDATING, S.NEEDS_CHANGES, S.QUEUED
    )
    assert task.iteration == 2
    task = await walk(session, task_id, S.RUNNING, S.VALIDATING, S.COMPLETED)
    assert task.completed_at is not None
    task = await walk(session, task_id, S.QUEUED)
    assert task.iteration == 3 and task.completed_at is None


async def test_timestamps_follow_status(session: AsyncSession, task_id: uuid.UUID) -> None:
    S = TaskStatus
    task = await walk(session, task_id, S.PLANNED, S.QUEUED)
    assert task.started_at is None
    task = await walk(session, task_id, S.RUNNING)
    started = task.started_at
    assert started is not None and task.completed_at is None
    task = await walk(session, task_id, S.INTERRUPTED, S.QUEUED, S.RUNNING)
    assert task.started_at == started  # first start is kept
    task = await walk(session, task_id, S.CANCELLED)
    assert task.completed_at is not None


async def test_terminal_states_cannot_be_left(session: AsyncSession, task_id: uuid.UUID) -> None:
    task = await walk(session, task_id, TaskStatus.CANCELLED)
    with pytest.raises(AppError, match="INVALID_TRANSITION"):
        await transition(session, task, TaskStatus.QUEUED, reason=None, actor=ACTOR)


async def test_events_count_matches_transitions(session: AsyncSession, task_id: uuid.UUID) -> None:
    S = TaskStatus
    await walk(session, task_id, S.PLANNED, S.QUEUED, S.RUNNING)
    count = await session.scalar(
        select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task_id)
    )
    seqs = (await session.scalars(select(TaskEvent.seq).where(TaskEvent.task_id == task_id))).all()
    assert count == 3 and sorted(seqs) == [1, 2, 3]
