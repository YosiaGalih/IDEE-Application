"""Recovery sweep (ARCHITECTURE §5.3, RUNBOOK): orphaned running|validating tasks are interrupted."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.audit import AuditEvent
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task, TaskEvent
from app.workflow.recovery import recover_expired
from tests.conftest import reset_data

AUDIT = AuditWriter(Redactor())


class Recorder:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        self.sent.append(message)


@pytest.fixture
async def make_task(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    await reset_data(engine)
    async with make_sessionmaker(engine)() as session:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.commit()
        owner_id, project_id = owner.id, project.id

    async def create(status: str, lease: str | None, *, cancel: bool = False) -> uuid.UUID:
        async with make_sessionmaker(engine)() as session:
            task = Task(
                project_id=project_id, title="t", prompt="p", workflow_type="create", created_by=owner_id
            )
            session.add(task)
            await session.flush()
            # Test setup: place the task directly (past the writer backstop).
            await session.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
            await session.execute(
                text(
                    "UPDATE tasks SET status = :s, cancel_requested = :c, lease_owner = 'w-old', "
                    "lease_expires_at = now() + CAST(CAST(:l AS text) AS interval) "
                    "WHERE id = :id"
                ),
                {"s": status, "c": cancel, "l": lease, "id": task.id},
            )
            await session.commit()
            return task.id

    return create


async def status_of(engine: AsyncEngine, task_id: uuid.UUID) -> tuple[str, str | None, Any]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, status_reason, lease_owner FROM tasks WHERE id = :id"), {"id": task_id}
            )
        ).one()
    return row.status, row.status_reason, row.lease_owner


async def test_expired_running_becomes_interrupted(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    running = await make_task("running", "-5 seconds")
    validating = await make_task("validating", "-5 seconds")
    bus = Recorder()
    result = await recover_expired(make_sessionmaker(engine), bus, AUDIT)
    assert (result.recovered, result.failed) == (2, 0)
    for task_id in (running, validating):
        assert await status_of(engine, task_id) == ("interrupted", "lease_expired", None)
    assert {m["payload"]["to"] for m in bus.sent} == {"interrupted"}
    async with make_sessionmaker(engine)() as session:
        actors = (
            await session.scalars(
                select(AuditEvent.actor_id).where(
                    AuditEvent.action == "task.status_changed", AuditEvent.resource_id == str(running)
                )
            )
        ).all()
    assert actors == ["recovery"]


async def test_cancel_requested_becomes_cancelled(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("running", "-5 seconds", cancel=True)
    await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    assert await status_of(engine, task_id) == ("cancelled", "cancel_requested", None)


async def test_live_leases_and_other_statuses_are_untouched(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    live = await make_task("running", "30 seconds")
    queued = await make_task("queued", "-5 seconds")
    no_lease = await make_task("running", None)
    result = await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    assert result.recovered == 0
    assert (await status_of(engine, live))[0] == "running"
    assert (await status_of(engine, queued))[0] == "queued"
    assert (await status_of(engine, no_lease))[0] == "running"


async def test_parallel_sweeps_transition_once(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    ids = [await make_task("running", "-5 seconds") for _ in range(5)]
    sessions = make_sessionmaker(engine)
    results = await asyncio.gather(*(recover_expired(sessions, Recorder(), AUDIT) for _ in range(3)))
    assert sum(r.recovered for r in results) == 5
    assert sum(r.failed for r in results) == 0  # SKIP LOCKED: losers skip, they do not hit VERSION_CONFLICT
    async with sessions() as session:
        per_task = (
            await session.execute(
                select(TaskEvent.task_id, func.count())
                .where(TaskEvent.task_id.in_(ids), TaskEvent.type == "task.status_changed")
                .group_by(TaskEvent.task_id)
            )
        ).all()
    assert sorted(count for _, count in per_task) == [1] * 5


async def test_one_failing_task_does_not_stop_the_sweep(
    engine: AsyncEngine,
    make_task,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    from app.workflow import recovery

    bad = await make_task("running", "-5 seconds")
    good = await make_task("running", "-5 seconds")
    real = recovery.transition

    async def flaky(db, task, to, **kwargs):  # type: ignore[no-untyped-def]
        if task.id == bad:
            raise RuntimeError("boom")
        await real(db, task, to, **kwargs)

    monkeypatch.setattr(recovery, "transition", flaky)
    result = await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    assert (result.recovered, result.failed) == (1, 1)
    assert (await status_of(engine, good))[0] == "interrupted"
    assert (await status_of(engine, bad))[0] == "running"
