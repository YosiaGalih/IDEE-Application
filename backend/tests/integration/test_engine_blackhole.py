"""The engine's bounds hold on a real driver when the database stops answering (§16.4 L3, §16.5a #1, #5, #7).

A TCP proxy between the engine and PostgreSQL swallows traffic without closing anything (a partition, not a
refused connection). Each case reads the database on a direct connection, never through the proxy.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

import app.core.db as db_module
from app.core.audit import AuditWriter
from app.core.db import bound_transaction, make_engine, make_sessionmaker
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task
from app.workflow import engine as engine_module
from app.workflow import lease as lease_module
from app.workflow.engine import StepContext, StepResult, TaskEngine, WorkflowPorts
from app.workflow.lease import LeaseGuard
from tests.blackhole_proxy import BlackHole
from tests.conftest import reset_data
from tests.runtime_fakes import RecordingBus

PLANNING = ("intake", "context", "plan")


async def make_draft(engine: AsyncEngine) -> uuid.UUID:
    await reset_data(engine)
    async with make_sessionmaker(engine)() as session:
        owner = User(email="o@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.flush()
        task = Task(project_id=project.id, title="t", prompt="p", workflow_type="create", created_by=owner.id)
        session.add(task)
        await session.commit()
        return task.id


def ports(eng: AsyncEngine, steps: dict[str, object], **overrides: float) -> WorkflowPorts:
    return WorkflowPorts(
        sessions=make_sessionmaker(eng),
        bus=RecordingBus(),
        audit=AuditWriter(Redactor()),
        worker_id="w-blackhole",
        max_active=5,
        steps=steps,  # type: ignore[arg-type]
        **overrides,
    )


async def done(_: StepContext) -> StepResult:
    return StepResult.done()


@pytest.mark.parametrize("pre_ping", [True, False], ids=["prod-make_engine", "no-pre-ping"])
async def test_heartbeat_gives_up_with_lease_to_spare_when_the_database_black_holes(
    engine: AsyncEngine,
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
    pre_ping: bool,
) -> None:
    """Lease 6 s, beat 1 s, safety 3.5 s: H = last_ok + 2.5 s. The close of a cancelled asyncpg connection can
    overrun by up to 2 s (§16.5a #1), so the step is still cancelled with >= 1 s of lease left. The old rule
    (last_ok + lease - beat) left about -2 s."""
    monkeypatch.setattr(lease_module, "_LEASE", timedelta(seconds=6))
    task_id = await make_draft(engine)
    hole = BlackHole(migrated_database)
    proxied = await hole.start()
    eng = make_engine(proxied) if pre_ping else create_async_engine(proxied, hide_parameters=True)
    left: list[float] = []

    async def lease_left() -> float:
        async with engine.connect() as conn:  # direct, not through the proxy
            return float(
                (
                    await conn.execute(
                        text(
                            "SELECT EXTRACT(EPOCH FROM lease_expires_at - clock_timestamp()) "
                            "FROM tasks WHERE id = :id"
                        ),
                        {"id": task_id},
                    )
                ).scalar_one()
            )

    async def long_step(_: StepContext) -> StepResult:
        await asyncio.sleep(1.3)  # at least one good renewal first
        hole.arm()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            left.append(await lease_left())
            raise
        return StepResult.done()

    steps = {"intake": long_step, "context": done, "plan": done}
    task_engine = TaskEngine(ports(eng, steps, heartbeat_s=1.0, lease_safety_s=3.5))
    try:
        outcome = await asyncio.wait_for(task_engine.run(task_id), timeout=30)
    finally:
        hole.release()  # before dispose(): a frozen connection's close would hang it
        await eng.dispose()
        await hole.close()
    print(f"\n[pre_ping={pre_ping}] lease left at cancel={left[0] if left else float('nan'):+.2f}s")

    assert outcome.stopped == "lease_lost"
    assert left, "the step was never cancelled"
    assert left[0] >= 1.0


async def test_a_bounded_transaction_ends_run_when_the_database_black_holes_before_finish(
    engine: AsyncEngine,
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§16.5a #7 on real asyncpg: the step returns, the connection freezes, and the finish write is bounded by
    ENGINE_WRITE_TIMEOUT_S (2 s here) plus the cancelled connection's close, so ``run()`` ends in a few
    seconds with a ``TimeoutError`` instead of hanging."""
    monkeypatch.setattr(engine_module, "ENGINE_WRITE_TIMEOUT_S", 2)
    task_id = await make_draft(engine)
    hole = BlackHole(migrated_database)
    eng = make_engine(await hole.start())

    async def arms_then_done(_: StepContext) -> StepResult:
        hole.arm()
        return StepResult.done()

    steps = {"intake": arms_then_done, "context": done, "plan": done}
    task_engine = TaskEngine(ports(eng, steps, heartbeat_s=30.0))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task_engine.run(task_id), timeout=45)
        ended = time.monotonic()
    finally:
        hole.release()
        await eng.dispose()
        await hole.close()
    assert hole.frozen_at is not None
    print(f"\nfreeze->end={ended - hole.frozen_at:.2f}s")
    assert ended - hole.frozen_at <= 2 * (2 + 2.5)


async def row_is_free(engine: AsyncEngine, task_id: uuid.UUID) -> bool:
    """On a direct connection: can another transaction take the task row (``FOR UPDATE SKIP LOCKED``)?"""
    async with engine.connect() as conn:
        found = (
            await conn.execute(
                text("SELECT id FROM tasks WHERE id = :id FOR UPDATE SKIP LOCKED"), {"id": task_id}
            )
        ).first()
    return found is not None


async def wait_until_free(engine: AsyncEngine, task_id: uuid.UUID, hole: BlackHole, within: float) -> float:
    """Seconds from the freeze until the row is free; fails after ``within`` seconds."""
    assert hole.frozen_at is not None
    while time.monotonic() - hole.frozen_at < within:
        if await row_is_free(engine, task_id):
            return time.monotonic() - hole.frozen_at
        await asyncio.sleep(0.1)
    raise AssertionError(f"the row was still locked {within}s after the freeze")


@pytest.mark.parametrize("idle", ["2s", "0"], ids=["bounded", "control"])
async def test_the_server_ends_a_session_that_goes_silent_inside_a_lease_guarded_transaction(
    engine: AsyncEngine,
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
    idle: str,
) -> None:
    """Spec §16.6.2 #86 (BC-15): the client freezes after ``hold`` and never sends COMMIT. With
    idle_in_transaction_session_timeout the server drops the session and the row lock goes; with 0 (control)
    the lock stays for as long as the connection lives."""
    monkeypatch.setattr(db_module, "IDLE_IN_TRANSACTION_TIMEOUT", idle)
    task_id = await make_draft(engine)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tasks SET lease_owner = 'w-blackhole', "
                "lease_expires_at = clock_timestamp() + interval '60 seconds' WHERE id = :id"
            ),
            {"id": task_id},
        )
    hole = BlackHole(migrated_database)
    eng = make_engine(await hole.start())
    holder: asyncio.Task[None] | None = None

    async def hold_then_freeze() -> None:
        async with asyncio.timeout(10):
            async with make_sessionmaker(eng)() as db:
                await bound_transaction(db)
                await LeaseGuard(task_id, "w-blackhole").hold(db)
                hole.arm()
                await db.execute(text("SELECT 1"))  # held by the proxy

    try:
        holder = asyncio.create_task(hold_then_freeze())
        while hole.frozen_at is None:  # noqa: ASYNC110 - a plain float the proxy sets
            await asyncio.sleep(0.02)
        assert not await row_is_free(engine, task_id)  # right after the freeze the lock is held
        if idle == "0":
            await asyncio.sleep(4.5)
            assert not await row_is_free(engine, task_id)
        else:
            freed = await wait_until_free(engine, task_id, hole, within=4.5)
            print(f"\nfreeze->lock released={freed:.2f}s (idle={idle})")
            assert 1.0 <= freed <= 4.5
    finally:
        hole.release()  # before dispose(): a frozen connection's close would hang it
        if holder is not None:
            await asyncio.gather(holder, return_exceptions=True)
        await eng.dispose()
        await hole.close()


async def test_a_black_holed_finish_releases_the_row_lock_before_the_client_gives_up(
    engine: AsyncEngine,
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The freeze comes after ``_locked`` of the finish write (lease renewed, row locked). The server drops
    silent session after 2 s, and the row is free while ``run()`` is still waiting on its 6 s client bound,
    which then ends it with a ``TimeoutError``."""
    monkeypatch.setattr(db_module, "IDLE_IN_TRANSACTION_TIMEOUT", "2s")
    monkeypatch.setattr(engine_module, "ENGINE_WRITE_TIMEOUT_S", 6)
    task_id = await make_draft(engine)
    hole = BlackHole(migrated_database)
    eng = make_engine(await hole.start())
    flagged = False
    original = TaskEngine._locked

    async def locked_then_arm(self: TaskEngine, db: AsyncSession, task: uuid.UUID, status: str) -> Task:
        found = await original(self, db, task, status)
        if flagged:
            hole.arm()
        return found

    monkeypatch.setattr(TaskEngine, "_locked", locked_then_arm)

    async def intake(_: StepContext) -> StepResult:
        nonlocal flagged
        flagged = True  # the next _locked is the finish write's
        return StepResult.done()

    steps = {"intake": intake, "context": done, "plan": done}
    task_engine = TaskEngine(ports(eng, steps, heartbeat_s=30.0))
    runner = asyncio.create_task(task_engine.run(task_id))
    try:
        while hole.frozen_at is None:  # noqa: ASYNC110 - a plain float the proxy sets
            await asyncio.sleep(0.02)
        freed = await wait_until_free(engine, task_id, hole, within=2 + 2.5)
        print(f"\nfreeze->lock released={freed:.2f}s")
        assert not runner.done()  # the client is still stuck: the server ended the lock, not the client
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(runner, timeout=45)
        assert time.monotonic() - hole.frozen_at <= 2 * (6 + 2.5)
    finally:
        hole.release()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await eng.dispose()
        await hole.close()
