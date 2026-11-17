"""The caller-held ``agent_run_id`` is never a way to run under, or to end, a row that is not this run's
(WP-3.1f2b1 AG-13 MEDIUM-1 and LOW-1; controller rulings R5, R6).

**Scope.** Proofs of concept from the AG-13 reviews, kept as regression tests:
- a spent id (its run ended, same task and agent), sent again through the production composition, runs
  nothing: no model request, no tool, and the ended row stays as it ended;
- a duplicate request whose id names a live (``running``) run of the same task and agent runs nothing and
  ends nothing, on the same worker or on another one (AG-13 delta MEDIUM-2, ruling R10: a first start never
  takes over an existing row);
- a foreign id (another task's running run) whose close attempts both time out still ends nothing: the
  minimal end names this run's task and agent, and the store matches only that identity.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.runtime import AgentRunIdConflict, AgentRunRequest, RunStart
from app.core.audit import AuditWriter
from app.core.redaction import Redactor
from app.llm.pricing import PricingTable
from app.models.agents import AgentRun
from app.models.tasks import TaskEvent
from app.models.tools import ToolCall
from app.services.agent_runtime import AgentRunWriter, build_agent_runtime
from app.services.agents import sync_registry
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.runtime_fakes import (
    OUTPUTS,
    REGISTRY,
    WORKER_ID,
    FakeProviders,
    Fakes,
    RecordingBus,
    config,
    make_runtime,
    run_request,
    scripted,
    submit_turn,
    tool_turn,
)
from tests.security import test_prompt_injection as injection

AUDIT = AuditWriter(Redactor())
settings = injection.settings


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


async def test_a_spent_run_id_runs_nothing_under_the_ended_row(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Any,
    pricing: PricingTable,
    tmp_path: Path,
) -> None:
    """The same request twice (a retried engine job that kept its id): the second run is refused at its first
    start with ``AgentRunIdConflict``; the model is never asked and the side-effecting tool never runs."""
    first_provider = scripted(submit_turn(dict(OUTPUTS["ImplementOutput"])))
    runtime, request = await injection.a_run(session, sessions, settings, pricing, tmp_path, first_provider)
    first = await runtime.run(request)
    assert first.status == "succeeded"
    session.expire_all()
    ended = await session.get(AgentRun, request.agent_run_id)
    assert ended is not None
    ended_output = ended.output

    second_provider = scripted(
        tool_turn("code.write_file", {"path": "src/poc.py", "content": "x = 1\n"}, "toolu_poc"),
        submit_turn(dict(OUTPUTS["ImplementOutput"])),
    )
    runtime2 = build_agent_runtime(
        sessions=sessions,
        settings=settings,
        registry=REGISTRY,
        pricing=pricing,
        audit=AuditWriter(Redactor(settings.secret_values())),
        bus=RecordingBus(),
        worker_id=WORKER_ID,
        providers=FakeProviders(second_provider),
    )
    with pytest.raises(AgentRunIdConflict):
        await runtime2.run(request)

    assert second_provider.requests == []
    assert request.workspace is not None and not (request.workspace.root / "src" / "poc.py").exists()
    session.expire_all()
    calls = (await session.scalars(select(ToolCall).where(ToolCall.task_id == request.task_id))).all()
    assert calls == []
    row = await session.get(AgentRun, request.agent_run_id)
    assert row is not None and (row.status, row.output) == ("succeeded", ended_output)


def run_start_of(request: AgentRunRequest) -> RunStart:
    return RunStart(
        agent_run_id=request.agent_run_id,
        task_id=request.task_id,
        project_id=request.project_id,
        agent_id=request.agent_id,
        agent_version=1,
        step="implement",
        model_provider="scripted",
        model_name="claude-haiku-5-5",
        prompt_version="0123456789ab",
        input={},
    )


def runtime_of(
    sessions: async_sessionmaker[AsyncSession],
    settings: Any,
    pricing: PricingTable,
    provider: Any,
    worker: str,
) -> Any:
    return build_agent_runtime(
        sessions=sessions,
        settings=settings,
        registry=REGISTRY,
        pricing=pricing,
        audit=AuditWriter(Redactor(settings.secret_values())),
        bus=RecordingBus(),
        worker_id=worker,
        providers=FakeProviders(provider),
    )


async def assert_live_row_untouched(session: AsyncSession, request: AgentRunRequest) -> None:
    session.expire_all()
    row = await session.get(AgentRun, request.agent_run_id)
    assert row is not None and (row.status, row.error_code, row.finished_at) == ("running", None, None)
    ends = (
        await session.scalars(
            select(TaskEvent.type).where(
                TaskEvent.task_id == request.task_id, TaskEvent.type == "agent.run_finished"
            )
        )
    ).all()
    assert ends == []
    calls = (await session.scalars(select(ToolCall).where(ToolCall.task_id == request.task_id))).all()
    assert calls == []


@pytest.mark.parametrize("worker", [WORKER_ID, "worker-dup-2"], ids=["same-worker", "other-worker"])
async def test_a_duplicate_request_never_runs_under_or_ends_a_live_row(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Any,
    pricing: PricingTable,
    tmp_path: Path,
    worker: str,
) -> None:
    """AG-13 delta MEDIUM-2: the request's first run is mid-flight (its first start committed a ``running``
    row). The same request again, from the worker that holds the lease (V1: it would run a whole second run
    under the live row) or from another worker (V2: refused at its first beat, its close would end the live
    row ``failed lease_lost``), is refused with ``AgentRunIdConflict`` and touches nothing."""
    _, request = await injection.a_run(session, sessions, settings, pricing, tmp_path, scripted())
    await AgentRunWriter(sessions, None, AUDIT).start(run_start_of(request), retry=False)
    duplicate = scripted(
        tool_turn("code.write_file", {"path": "src/dup.py", "content": "x = 1\n"}, "toolu_dup"),
        submit_turn(dict(OUTPUTS["ImplementOutput"])),
    )
    with pytest.raises(AgentRunIdConflict):
        await runtime_of(sessions, settings, pricing, duplicate, worker).run(request)
    assert duplicate.requests == []
    assert request.workspace is not None and not (request.workspace.root / "src" / "dup.py").exists()
    await assert_live_row_untouched(session, request)


class FlakyThenSlowWriter(AgentRunWriter):
    """The first start fails before its INSERT; the close's start is slow, then the real store finds the id
    another task's and raises ``AgentRunIdConflict``."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.starts = 0
        self.settled = asyncio.Event()

    async def start(self, run: RunStart, *, retry: bool) -> None:
        self.starts += 1
        if self.starts == 1:
            raise ConnectionError("connection reset")
        try:
            await asyncio.sleep(1.0)  # longer than both close attempts
            await super().start(run, retry=retry)
        finally:
            self.settled.set()


async def test_a_foreign_run_is_never_ended_when_both_close_attempts_time_out(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await sync_registry(session, REGISTRY, AUDIT)
    project = await make_project(session)
    mine, other = await make_task(session, project), await make_task(session, project)
    await session.commit()
    mine_id, other_id, project_id = mine.id, other.id, project.id
    foreign = uuid.uuid4()
    await AgentRunWriter(sessions, None, AUDIT).start(
        RunStart(
            agent_run_id=foreign,
            task_id=other_id,
            project_id=project_id,
            agent_id="B14",
            agent_version=1,
            step="implement",
            model_provider="scripted",
            model_name="claude-haiku-5-5",
            prompt_version="0123456789ab",
            input={},
        ),
        retry=False,
    )
    writer = FlakyThenSlowWriter(sessions, None, AUDIT)
    fakes = Fakes(scripted(submit_turn(dict(OUTPUTS["ImplementOutput"]))))
    fakes.runs = writer  # type: ignore[assignment]
    request = run_request("B14", task_id=mine_id, project_id=project_id, agent_run_id=foreign)
    with pytest.raises(ConnectionError):
        await make_runtime(fakes, runtime_config=config(close_timeout_s=0.2)).run(request)
    async with asyncio.timeout(5):
        await writer.settled.wait()  # the slow start the close gave up on has ended too

    session.expire_all()
    row = await session.get(AgentRun, foreign)
    assert row is not None and (row.status, row.error_code, row.finished_at) == ("running", None, None)
    ends = (
        await session.scalars(
            select(TaskEvent.type).where(
                TaskEvent.task_id.in_([mine_id, other_id]), TaskEvent.type == "agent.run_finished"
            )
        )
    ).all()
    assert ends == []
