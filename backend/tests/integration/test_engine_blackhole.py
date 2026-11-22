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
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.audit import AuditWriter
from app.core.db import make_engine, make_sessionmaker
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task
from app.workflow import engine as engine_module
from app.workflow import lease as lease_module
from app.workflow.engine import StepContext, StepResult, TaskEngine, WorkflowPorts
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
