"""The adapter's two database reads (WP-3.2c2c2 Task 2; spec §16.6.5 B, N56, BC-15)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.registry import load_registry
from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.models.agents import AgentProfile, AgentRun
from app.services import agent_step as agent_step_module
from app.services.agent_runtime import RunOutcome
from app.services.agent_step import DbAgentStepReads
from app.services.agents import sync_registry
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task

REGISTRY = Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml"


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(engine)


async def sync(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session:
        await sync_registry(session, load_registry(REGISTRY), AuditWriter(Redactor()))
        await session.commit()


async def add_run(
    sessions: async_sessionmaker[AsyncSession], status: str = "failed", **values: Any
) -> tuple[uuid.UUID, uuid.UUID]:
    async with sessions() as session:
        project = await make_project(session)
        task = await make_task(session, project)
        run = AgentRun(
            task_id=task.id, agent_id="A02", agent_version=1, step="intake", status=status, **values
        )
        session.add(run)
        await session.commit()
        return run.id, task.id


async def test_profile_exists_follows_the_table(sessions: async_sessionmaker[AsyncSession]) -> None:
    reads = DbAgentStepReads(sessions)
    await sync(sessions)
    assert await reads.profile_exists("A02") is True
    assert await reads.profile_exists("Z99") is False
    async with sessions() as session:
        await session.execute(delete(AgentProfile).where(AgentProfile.agent_id == "A02"))
        await session.commit()
    assert await reads.profile_exists("A02") is False


async def test_run_outcome_reads_only_the_matching_row(sessions: async_sessionmaker[AsyncSession]) -> None:
    await sync(sessions)
    run_id, task_id = await add_run(sessions, "failed", error_code="timeout")
    reads = DbAgentStepReads(sessions)
    assert await reads.run_outcome(run_id, task_id=task_id, agent_id="A02") == RunOutcome(
        "finished", "failed", "timeout"
    )
    assert (await reads.run_outcome(run_id, task_id=task_id, agent_id="A03")).state == "missing"
    assert (await reads.run_outcome(run_id, task_id=uuid.uuid4(), agent_id="A02")).state == "missing"
    assert (await reads.run_outcome(uuid.uuid4(), task_id=task_id, agent_id="A02")).state == "missing"


async def captured(engine: AsyncEngine, call: Callable[[], Awaitable[Any]]) -> list[str]:
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        await call()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    return statements


async def test_each_read_starts_with_bound_transaction(
    sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    await sync(sessions)
    run_id, task_id = await add_run(sessions, "failed", error_code="timeout")
    reads = DbAgentStepReads(sessions)
    for call in (
        lambda: reads.profile_exists("A02"),
        lambda: reads.run_outcome(run_id, task_id=task_id, agent_id="A02"),
    ):
        statements = await captured(engine, call)
        assert "set_config(" in statements[0] and statements[0].count("set_config(") == 3
        assert len(statements) == 2


class HangingSession:
    async def __aenter__(self) -> HangingSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(30)

    async def scalar(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(30)


async def test_a_hung_read_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_step_module, "AGENT_STEP_READ_TIMEOUT_S", 0.05)
    reads = DbAgentStepReads(lambda: HangingSession())  # type: ignore[arg-type]
    with pytest.raises(TimeoutError):
        await reads.profile_exists("A02")
    with pytest.raises(TimeoutError):
        await reads.run_outcome(uuid.uuid4(), task_id=uuid.uuid4(), agent_id="A02")
