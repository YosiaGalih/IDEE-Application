"""workflow.states.transition: the only writer of tasks.status (CLAUDE.md #4, ARCHITECTURE §5.1)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.audit import AuditEvent
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task, TaskEvent
from app.workflow.states import Actor, TaskStatus, transition
from tests.conftest import reset_data

ACTOR = Actor("system", "orchestrator")
AUDIT = AuditWriter(Redactor())


@pytest.fixture
async def task_id(engine: AsyncEngine) -> uuid.UUID:
    await reset_data(engine)
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
        await transition(session, task, status, reason=None, actor=ACTOR, audit=AUDIT)
    await session.commit()
    return task


async def test_valid_transition_updates_row_audit_and_event(
    session: AsyncSession, task_id: uuid.UUID
) -> None:
    task = await load(session, task_id)
    await transition(session, task, TaskStatus.PLANNED, reason="plan ready", actor=ACTOR, audit=AUDIT)
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
        await transition(session, task, TaskStatus.COMPLETED, reason=None, actor=ACTOR, audit=AUDIT)
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
        await transition(first, a, TaskStatus.PLANNED, reason=None, actor=ACTOR, audit=AUDIT)
        await first.commit()
        with pytest.raises(AppError) as caught:
            await transition(second, b, TaskStatus.CANCELLED, reason=None, actor=ACTOR, audit=AUDIT)
        assert (caught.value.code, caught.value.http_status) == ("VERSION_CONFLICT", 409)
        await second.rollback()
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
            await transition(session, stale, TaskStatus.PLANNED, reason=None, actor=ACTOR, audit=AUDIT)
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
        await transition(session, task, TaskStatus.QUEUED, reason=None, actor=ACTOR, audit=AUDIT)


async def test_events_count_matches_transitions(session: AsyncSession, task_id: uuid.UUID) -> None:
    S = TaskStatus
    await walk(session, task_id, S.PLANNED, S.QUEUED, S.RUNNING)
    count = await session.scalar(
        select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task_id)
    )
    seqs = (await session.scalars(select(TaskEvent.seq).where(TaskEvent.task_id == task_id))).all()
    assert count == 3 and sorted(seqs) == [1, 2, 3]


async def test_failed_audit_leaves_no_status_change(session: AsyncSession, task_id: uuid.UUID) -> None:
    task = await load(session, task_id)
    with pytest.raises(ValueError, match="actor"):
        await transition(
            session, task, TaskStatus.PLANNED, reason=None, actor=Actor("system", "x" * 100), audit=AUDIT
        )
    await session.commit()  # a caller that swallows the error and commits anyway
    assert task.status == "draft" and task.version == 1
    fresh = await load(session, task_id)
    assert (fresh.status, fresh.version, fresh.event_seq) == ("draft", 1, 0)


async def test_reason_is_redacted_everywhere(session: AsyncSession, task_id: uuid.UUID) -> None:
    secret = "Zk9-internal-sandbox-token-0123456789"
    audit = AuditWriter(Redactor([secret]))
    task = await load(session, task_id)
    await transition(
        session, task, TaskStatus.FAILED, reason=f"sandbox token {secret} rejected", actor=ACTOR, audit=audit
    )
    await session.commit()
    async with session.bind.connect() as conn:
        rows = [
            (
                await conn.execute(text("SELECT status_reason FROM tasks WHERE id = :id"), {"id": task_id})
            ).scalar(),
            (
                await conn.execute(
                    text("SELECT payload::text FROM task_events WHERE task_id = :id"), {"id": task_id}
                )
            ).scalar(),
            (
                await conn.execute(
                    text("SELECT metadata::text FROM audit_events WHERE resource_id = :id"),
                    {"id": str(task_id)},
                )
            ).scalar(),
        ]
    for row in rows:
        assert row is not None and secret not in row and "[REDACTED]" in row


async def test_long_reason_is_truncated_in_the_row_only(session: AsyncSession, task_id: uuid.UUID) -> None:
    S = TaskStatus
    task = await walk(session, task_id, S.PLANNED, S.QUEUED, S.RUNNING)
    await transition(session, task, S.FAILED, reason="E" * 300, actor=ACTOR, audit=AUDIT)
    await session.commit()
    fresh = await load(session, task_id)
    assert fresh.status == "failed" and fresh.status_reason == "E" * 200
    payload = await session.scalar(
        select(TaskEvent.payload).where(TaskEvent.task_id == task_id, TaskEvent.seq == 4)
    )
    assert payload is not None and payload["reason"] == "E" * 300


async def test_retry_after_failure_clears_completed_at(session: AsyncSession, task_id: uuid.UUID) -> None:
    S = TaskStatus
    task = await walk(session, task_id, S.PLANNED, S.QUEUED, S.RUNNING, S.FAILED)
    assert task.completed_at is not None
    task = await walk(session, task_id, S.QUEUED, S.RUNNING)
    assert task.completed_at is None and task.iteration == 1


async def test_deleted_task_is_not_found(session: AsyncSession, task_id: uuid.UUID) -> None:
    task = await load(session, task_id)
    async with session.bind.begin() as conn:
        await conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": task_id})
    with pytest.raises(AppError) as caught:
        await transition(session, task, TaskStatus.PLANNED, reason=None, actor=ACTOR, audit=AUDIT)
    assert (caught.value.code, caught.value.http_status) == ("NOT_FOUND", 404)


async def test_version_conflict_reports_current_state(engine: AsyncEngine, task_id: uuid.UUID) -> None:
    sessions = make_sessionmaker(engine)
    async with sessions() as session:
        stale = await load(session, task_id)
    async with sessions() as other:
        await walk(other, task_id, TaskStatus.PLANNED)
    async with sessions() as session:
        session.add(stale)
        with pytest.raises(AppError) as caught:
            await transition(session, stale, TaskStatus.CANCELLED, reason=None, actor=ACTOR, audit=AUDIT)
    assert caught.value.code == "VERSION_CONFLICT"
    assert caught.value.details == [{"current_status": "planned", "current_version": 2}]


async def test_locally_modified_status_is_refused(session: AsyncSession, task_id: uuid.UUID) -> None:
    task = await load(session, task_id)
    task.status = "completed"  # proves the refusal; tests/ is outside the status-writer scan
    with pytest.raises(ValueError, match="modified outside transition"):
        await transition(session, task, TaskStatus.QUEUED, reason=None, actor=ACTOR, audit=AUDIT)


async def test_task_must_belong_to_the_session(engine: AsyncEngine, task_id: uuid.UUID) -> None:
    sessions = make_sessionmaker(engine)
    async with sessions() as owner, sessions() as other:
        task = await load(owner, task_id)
        with pytest.raises(ValueError, match="session"):
            await transition(other, task, TaskStatus.PLANNED, reason=None, actor=ACTOR, audit=AUDIT)


async def test_local_event_seq_is_refreshed(session: AsyncSession, task_id: uuid.UUID) -> None:
    task = await walk(session, task_id, TaskStatus.PLANNED, TaskStatus.QUEUED)
    assert task.event_seq == 2


async def test_database_rejects_status_writes_outside_transition(
    session: AsyncSession, engine: AsyncEngine, task_id: uuid.UUID
) -> None:
    async with engine.connect() as conn:
        with pytest.raises(DBAPIError, match=r"workflow\.states\.transition"):
            await conn.execute(text("UPDATE tasks SET status = 'completed' WHERE id = :id"), {"id": task_id})
    task = await load(session, task_id)
    async with engine.connect() as conn:
        with pytest.raises(DBAPIError, match="start as draft"):
            await conn.execute(
                text(
                    "INSERT INTO tasks (id, project_id, title, prompt, workflow_type, status, created_by) "
                    "VALUES (gen_random_uuid(), :p, 't', 'p', 'create', 'completed', :u)"
                ),
                {"p": task.project_id, "u": task.created_by},
            )
