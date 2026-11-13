"""A held task row lock never stalls a run (reviews of 42bcb2e: silent H2, AG-13 LOW-2, backend lock note).

Every runtime port that writes the task row (the lease heartbeat, the run store, the usage event, and the
ledger's usage row, whose foreign key locks the task row: review N1 of 073e68d) gives up on a lock after
``lock_timeout`` 5 s and on a statement after ``statement_timeout``, as ``claim_lease`` does; the
runtime bounds each port by the run's deadline and the close by ``close_timeout_s``. AG-13's PoC (a lock held
across the run, run timeout 1 s) returned after 3.03 s, when the lock was released; now the run ends at its
deadline.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.runtime import AgentRunUnfinished, RunEnd, RunScope, RunStart
from app.core.audit import AuditWriter
from app.core.config import Settings
from app.core.redaction import Redactor
from app.llm.base import Usage
from app.llm.pricing import PricingTable
from app.llm.retry import AttemptRecord
from app.models.agents import AgentRun
from app.services import usage as usage_module
from app.services.agent_runtime import AgentRunWriter, LeaseHeartbeat, TaskRunEvents
from app.services.usage import UsageCallContext, UsageLedger
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.runtime_fakes import (
    OUTPUTS,
    WORKER_ID,
    FakeTools,
    compose,
    config,
    hold_lease,
    registry_with,
    run_request,
    scripted,
    submit_turn,
)
from tests.security.test_runtime_gate import record, worker

DONE = submit_turn(OUTPUTS["ImplementOutput"])
AUDIT = AuditWriter(Redactor())


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def settings() -> Settings:
    return worker()


async def a_task(session: AsyncSession, settings: Settings) -> tuple[uuid.UUID, uuid.UUID]:
    await record(session, settings)
    project = await make_project(session)
    task = await make_task(session, project)
    await session.commit()
    await hold_lease(session, task.id)
    return project.id, task.id


async def hold_task_row(
    engine: AsyncEngine,
    task_id: uuid.UUID,
    seconds: float,
    locked: asyncio.Event,
    mode: str = "NO KEY UPDATE",
) -> None:
    statement = {
        "NO KEY UPDATE": "SELECT 1 FROM tasks WHERE id = :id FOR NO KEY UPDATE",
        "UPDATE": "SELECT 1 FROM tasks WHERE id = :id FOR UPDATE",  # also blocks a foreign key's KEY SHARE
    }[mode]
    async with engine.begin() as conn:
        await conn.execute(text(statement), {"id": task_id})
        locked.set()
        await asyncio.sleep(seconds)


async def test_poc_a_held_task_lock_no_longer_stalls_the_run_past_its_deadline(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    pricing: PricingTable,
) -> None:
    """The AG-13 PoC, with the lock held 6 s: the run (timeout 1 s) ends at its deadline and gives up its
    bounded close, saying the run is unfinished, instead of waiting for the lock."""
    project_id, task_id = await a_task(session, settings)
    run = compose(sessions, settings, pricing, scripted(DONE), tools=FakeTools([]),
                  registry=registry_with("B14", limits={"timeout_seconds": 1}),
                  runtime_config=config(close_timeout_s=0.5))  # fmt: skip
    locked = asyncio.Event()
    holder = asyncio.create_task(hold_task_row(engine, task_id, 6.0, locked))
    await locked.wait()
    started = time.monotonic()
    with pytest.raises(AgentRunUnfinished):
        await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    elapsed = time.monotonic() - started
    # The deadline (1 s), then the close bounded three times (wait, retry, minimal end): 1.5 s; not the 6 s
    # the lock is held.
    assert elapsed < 3.0, elapsed
    await holder
    assert run.provider.requests == []


async def test_a_lock_released_after_the_deadline_ends_the_run_timeout(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    pricing: PricingTable,
) -> None:
    project_id, task_id = await a_task(session, settings)
    run = compose(sessions, settings, pricing, scripted(DONE), tools=FakeTools([]),
                  registry=registry_with("B14", limits={"timeout_seconds": 1}))  # fmt: skip
    locked = asyncio.Event()
    holder = asyncio.create_task(hold_task_row(engine, task_id, 2.0, locked))
    await locked.wait()
    started = time.monotonic()
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    elapsed = time.monotonic() - started
    await holder
    assert (result.status, result.error_code) == ("failed", "timeout")
    assert elapsed < 4.0, elapsed  # the deadline, then the close as soon as the lock is free
    stored = await session.get(AgentRun, result.agent_run_id)
    assert stored is not None and (stored.status, stored.error_code) == ("failed", "timeout")


def _start(task_id: uuid.UUID, project_id: uuid.UUID) -> RunStart:
    return RunStart(task_id, project_id, "B14", 1, "implement", "scripted", "m", "0" * 12, {})


async def _beat(sessions: async_sessionmaker[AsyncSession], scope: RunScope) -> None:
    await LeaseHeartbeat(sessions, WORKER_ID).beat(scope.task_id)


async def _start_run(sessions: async_sessionmaker[AsyncSession], scope: RunScope) -> None:
    await AgentRunWriter(sessions, None, AUDIT).start(_start(scope.task_id, scope.project_id))


async def _usage_event(sessions: async_sessionmaker[AsyncSession], scope: RunScope) -> None:
    await TaskRunEvents(sessions, None, Redactor()).usage_updated(scope)


async def _finish_run(sessions: async_sessionmaker[AsyncSession], scope: RunScope) -> None:
    await AgentRunWriter(sessions, None, AUDIT).finish(scope.agent_run_id, RunEnd("cancelled", 0))


PORTS: dict[str, Callable[[async_sessionmaker[AsyncSession], RunScope], Awaitable[None]]] = {
    "heartbeat": _beat,
    "run_start": _start_run,
    "usage_updated": _usage_event,
    "run_finish": _finish_run,
}


@pytest.mark.parametrize("port", list(PORTS))
async def test_a_port_that_writes_the_task_row_gives_up_on_a_held_lock(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    port: str,
) -> None:
    project_id, task_id = await a_task(session, settings)
    run_id = await AgentRunWriter(sessions, None, AUDIT).start(_start(task_id, project_id))
    scope = RunScope(project_id, task_id, run_id, "B14")
    locked = asyncio.Event()
    holder = asyncio.create_task(hold_task_row(engine, task_id, 9.0, locked))
    await locked.wait()
    started = time.monotonic()
    with pytest.raises(DBAPIError) as raised:
        await PORTS[port](sessions, scope)
    elapsed = time.monotonic() - started
    holder.cancel()
    await asyncio.gather(holder, return_exceptions=True)
    assert 4.0 < elapsed < 8.0, elapsed  # lock_timeout 5 s
    assert "lock" in str(raised.value.orig).lower()
    if port == "run_finish":
        stored = await session.get(AgentRun, run_id)
        assert stored is not None and stored.status == "running"  # nothing half-written


async def test_ports_bound_their_statements(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """``statement_timeout`` is set with ``lock_timeout`` in the heartbeat's transaction (seen from it)."""
    _, task_id = await a_task(session, settings)
    seen: dict[str, Any] = {}

    from app.services import agent_runtime as module

    original = module.renew_lease

    async def spy(db: AsyncSession, task: uuid.UUID, worker_id: str) -> None:
        seen["lock"] = await db.scalar(text("SELECT current_setting('lock_timeout')"))
        seen["statement"] = await db.scalar(text("SELECT current_setting('statement_timeout')"))
        await original(db, task, worker_id)

    module.renew_lease = spy  # type: ignore[assignment]
    try:
        await LeaseHeartbeat(sessions, WORKER_ID).beat(task_id)
    finally:
        module.renew_lease = original  # type: ignore[assignment]
    assert seen == {"lock": "5s", "statement": "10s"}


def _paid_attempt(scope: RunScope) -> tuple[UsageCallContext, AttemptRecord]:
    ctx = UsageCallContext(
        project_id=scope.project_id,
        call_id=uuid.uuid4(),
        input_token_bound=1000,
        output_token_cap=100,
        task_id=scope.task_id,
        agent_run_id=scope.agent_run_id,
        agent_id="B14",
    )
    attempt = AttemptRecord(
        "scripted", "claude-haiku-5-5", 1, "r", Usage(input_tokens=1, output_tokens=1), 5, "ok", None
    )
    return ctx, attempt


async def test_the_ledger_gives_up_on_a_held_task_lock(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    pricing: PricingTable,
) -> None:
    """Review N1 (the rest of H2, AG-13 LOW-2): a usage row's INSERT takes a key-share lock on the task row
    (its foreign key). Behind a ``FOR UPDATE`` it gives up after ``lock_timeout`` 5 s; the PoC waited 7 s,
    until the lock was released."""
    project_id, task_id = await a_task(session, settings)
    run_id = await AgentRunWriter(sessions, None, AUDIT).start(_start(task_id, project_id))
    ctx, attempt = _paid_attempt(RunScope(project_id, task_id, run_id, "B14"))
    locked = asyncio.Event()
    holder = asyncio.create_task(hold_task_row(engine, task_id, 9.0, locked, mode="UPDATE"))
    await locked.wait()
    started = time.monotonic()
    with pytest.raises(DBAPIError) as raised:
        await UsageLedger(sessions, pricing).record(ctx, attempt)
    elapsed = time.monotonic() - started
    holder.cancel()
    await asyncio.gather(holder, return_exceptions=True)
    assert 4.0 < elapsed < 8.0, elapsed  # lock_timeout 5 s
    assert "lock" in str(raised.value.orig).lower()


async def test_the_ledger_bounds_its_statements(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
) -> None:
    project_id, task_id = await a_task(session, settings)
    run_id = await AgentRunWriter(sessions, None, AUDIT).start(_start(task_id, project_id))
    ctx, attempt = _paid_attempt(RunScope(project_id, task_id, run_id, "B14"))
    seen: dict[str, Any] = {}
    original = usage_module._check_context

    async def spy(db: AsyncSession, call: UsageCallContext) -> None:
        seen["lock"] = await db.scalar(text("SELECT current_setting('lock_timeout')"))
        seen["statement"] = await db.scalar(text("SELECT current_setting('statement_timeout')"))
        await original(db, call)

    usage_module._check_context = spy  # type: ignore[assignment]
    try:
        await UsageLedger(sessions, pricing).record(ctx, attempt)
    finally:
        usage_module._check_context = original  # type: ignore[assignment]
    assert seen == {"lock": "5s", "statement": "10s"}
