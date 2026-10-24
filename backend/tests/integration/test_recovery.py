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


async def test_expired_lease_marks_interrupted(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    running = await make_task("running", "-5 seconds")
    validating = await make_task("validating", "-5 seconds")
    bus = Recorder()
    result = await recover_expired(make_sessionmaker(engine), bus, AUDIT)
    assert (result.candidates, result.recovered, result.failed, result.skipped) == (2, 2, 0, 0)
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
    fresh_no_lease = await make_task("running", None)  # just moved to running; its worker is claiming
    result = await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    assert result.recovered == 0
    assert (await status_of(engine, live))[0] == "running"
    assert (await status_of(engine, queued))[0] == "queued"
    assert (await status_of(engine, fresh_no_lease))[0] == "running"


async def test_active_task_without_a_lease_is_recovered_after_a_grace_period(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
) -> None:
    """Review WP-1.1f H2: running with lease NULL (released early / crash) was never recovered."""
    task_id = await make_task("running", None)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tasks SET updated_at = now() - interval '2 minutes' WHERE id = :id"), {"id": task_id}
        )
    result = await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    assert result.recovered == 1
    assert (await status_of(engine, task_id))[:2] == ("interrupted", "lease_expired")


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


async def test_one_failing_task_does_not_stop_the_sweep(  # type: ignore[no-untyped-def]
    engine: AsyncEngine,
    make_task,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
    assert "task_recovery_failed" in capsys.readouterr().out
    assert (await status_of(engine, good))[0] == "interrupted"
    assert (await status_of(engine, bad))[0] == "running"


async def seed_orphan(database_url: str) -> uuid.UUID:
    """Own engine: callers are synchronous tests that drive asyncio.run themselves."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)
    try:
        await reset_data(engine)
        async with make_sessionmaker(engine)() as session:
            owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
            session.add(owner)
            await session.flush()
            project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
            session.add(project)
            await session.flush()
            task = Task(
                project_id=project.id, title="t", prompt="p", workflow_type="create", created_by=owner.id
            )
            session.add(task)
            await session.flush()
            await session.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
            await session.execute(
                text(
                    "UPDATE tasks SET status = 'running', lease_owner = 'w', "
                    "lease_expires_at = now() - interval '5 seconds' WHERE id = :id"
                ),
                {"id": task.id},
            )
            await session.commit()
            return task.id
    finally:
        await engine.dispose()


async def read_status(database_url: str, task_id: uuid.UUID) -> str:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(text("SELECT status FROM tasks WHERE id = :id"), {"id": task_id})
            ).scalar_one()
    finally:
        await engine.dispose()


def test_cli_recover_reports_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], migrated_database: str
) -> None:
    from app.cli import main
    from tests.conftest import make_test_env

    task_id = asyncio.run(seed_orphan(migrated_database))
    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    assert main(["recover"]) == 0
    assert "candidates: 1, recovered: 1, failed: 0, skipped: 0" in capsys.readouterr().out
    assert asyncio.run(read_status(migrated_database, task_id)) == "interrupted"


def test_celery_job_runs_the_sweep(monkeypatch: pytest.MonkeyPatch, migrated_database: str) -> None:
    from app.worker.jobs import recovery_sweep
    from tests.conftest import make_test_env

    task_id = asyncio.run(seed_orphan(migrated_database))
    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    assert recovery_sweep.apply().get() == {"candidates": 1, "recovered": 1, "failed": 0, "skipped": 0}
    assert asyncio.run(read_status(migrated_database, task_id)) == "interrupted"


async def test_duplicate_delivery_single_execution(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    """TEST_STRATEGY AC-17: a redelivered job for an already-claimed or finished task does not run it."""
    from app.workflow.lease import ClaimResult, claim_lease

    queued = await make_task("queued", None)
    finished = await make_task("completed", None)
    sessions = make_sessionmaker(engine)

    async def claim(task_id: uuid.UUID, worker: str) -> ClaimResult:
        async with sessions() as session:
            result = await claim_lease(session, task_id, worker, max_active=5)
            await session.commit()
            return result

    first, second = await asyncio.gather(claim(queued, "w1"), claim(queued, "w2"))
    assert sorted([first.value, second.value]) == ["acquired", "held"]
    assert await claim(finished, "w3") is ClaimResult.NOT_CLAIMABLE


def test_failed_recoveries_fail_the_celery_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker import jobs
    from app.workflow.recovery import RecoveryIncomplete, RecoveryResult

    async def partial() -> dict[str, int]:
        raise RecoveryIncomplete(RecoveryResult(candidates=2, recovered=1, failed=1, skipped=0))

    monkeypatch.setattr(jobs, "run_recovery_sweep", partial)
    result = jobs.recovery_sweep.apply()
    assert result.failed()
    assert isinstance(result.result, RecoveryIncomplete)
