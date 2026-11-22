"""The caller-held ``agent_run_id`` is never a way to run under, or to end, a row that is not this run's
(WP-3.1f2b1 AG-13 MEDIUM-1 and LOW-1; controller rulings R5, R6).

**Scope.** Proofs of concept from the AG-13 reviews, kept as regression tests:
- a spent id (its run ended, same task and agent), sent again through the production composition, runs
  nothing: no model request, no tool, and the ended row stays as it ended;
- a duplicate request whose id names a live (``running``) run of the same task and agent runs nothing and
  ends nothing, on the same worker or on another one (AG-13 delta MEDIUM-2, ruling R10: a first start never
  takes over an existing row);
- a foreign id (another task's running run) whose close attempts both time out still ends nothing: the
  minimal end names this run's task and agent, and the store matches only that identity;
- a duplicate request of the same task and agent that commits its live row while the original request's
  first start failed before its INSERT is never adopted by the original's retried start, nor ended by its
  minimal end: both carry the original's start token, which the duplicate's row does not hold (AG-13
  delta-2 LOW-1 of 3.1f2b1; spec WP-3.2 §16.3 Keputusan Lead #66, §16.3a #1-#4);
- the store's side of that token: a retried start is the row's only while task, agent and token all match
  (else ``AgentRunIdConflict``, checked before the row's status), and ``fail`` ends only the row of its
  token. The token is never published.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from app.agents.runtime import AgentRunIdConflict, AgentRunRequest, BudgetBreach, RunEnd, RunStart
from app.core.audit import AuditWriter
from app.core.redaction import Redactor
from app.llm.pricing import PricingTable
from app.models.agents import AgentRun
from app.models.audit import AuditEvent
from app.models.tasks import TaskEvent
from app.models.tools import ToolCall
from app.services.agent_runtime import AgentRunAlreadyFinished, AgentRunWriter, build_agent_runtime
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
        start_token=uuid.uuid4(),  # another request's: each call is a request of its own
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
            start_token=uuid.uuid4(),
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


# --- the start token (spec WP-3.2 §16.3 Keputusan Lead #66, §16.3a #1-#4) ----------------------------------


async def a_task_of(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    await sync_registry(session, REGISTRY, AUDIT)
    project = await make_project(session)
    task = await make_task(session, project)
    await session.commit()
    return project.id, task.id


class ResetBeforeInsertWriter(AgentRunWriter):
    """Request A's store (the AG-13 delta-2 probe of 3.1f2b1): the first start fails before its INSERT (a
    connection reset); the close's start, a retry, waits until the duplicate request B has committed its row,
    then reaches the real store. Each ``retry`` flag, start token and ``fail`` is recorded."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.calls: list[bool] = []
        self.tokens: list[uuid.UUID] = []
        self.fails: list[uuid.UUID] = []
        self.retrying = asyncio.Event()
        self.duplicate_in = asyncio.Event()

    async def start(self, run: RunStart, *, retry: bool) -> None:
        self.calls.append(retry)
        self.tokens.append(run.start_token)
        if not retry:
            raise ConnectionError("connection reset before the INSERT")
        self.retrying.set()
        await self.duplicate_in.wait()  # the window: B's first start lands here
        await super().start(run, retry=retry)

    async def fail(
        self,
        agent_run_id: uuid.UUID,
        *,
        task_id: uuid.UUID,
        agent_id: str,
        start_token: uuid.UUID,
        breach: BudgetBreach | None = None,
    ) -> None:
        self.fails.append(start_token)
        await super().fail(
            agent_run_id, task_id=task_id, agent_id=agent_id, start_token=start_token, breach=breach
        )


def no_token_logged(logs: Iterable[Mapping[str, Any]], tokens: list[uuid.UUID]) -> bool:
    return all(str(token) not in repr(log) for log in logs for token in tokens)


async def test_a_retried_start_never_adopts_a_duplicates_live_row(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """AG-13 delta-2 LOW-1 of 3.1f2b1, the probe kept as a regression (Keputusan Lead #66). Request A's first
    start fails before its INSERT; a duplicate request B with the same id (a caller bug, ARCHITECTURE §6.1)
    commits its live row in that window; A's close retries the start (``retry=True``). The row holds B's start
    token, not A's, so the retry is ``AgentRunIdConflict``, logged once: A neither adopts B's row nor ends it,
    no minimal end is tried, and A's caller still gets the original error."""
    project_id, task_id = await a_task_of(session)
    request = run_request("B14", task_id=task_id, project_id=project_id, agent_run_id=uuid.uuid4())
    writer_a = ResetBeforeInsertWriter(sessions, None, AUDIT)
    fakes_a = Fakes(scripted(submit_turn(dict(OUTPUTS["ImplementOutput"]))))
    fakes_a.runs = writer_a  # type: ignore[assignment]
    b = run_start_of(request)
    with capture_logs() as logs:
        a = asyncio.create_task(make_runtime(fakes_a).run(request))
        async with asyncio.timeout(5):
            await writer_a.retrying.wait()  # A's close is in its retried start: the window is open
        await AgentRunWriter(sessions, None, AUDIT).start(b, retry=False)  # B: live, mid-run
        writer_a.duplicate_in.set()
        with pytest.raises(ConnectionError):
            await a

    assert writer_a.calls == [False, True]
    assert writer_a.tokens[0] == writer_a.tokens[1] != b.start_token
    assert writer_a.fails == []
    await assert_live_row_untouched(session, request)
    conflicts = [log for log in logs if log["event"] == "agent_run_id_conflict"]
    assert len(conflicts) == 1 and conflicts[0]["agent_run_id"] == str(request.agent_run_id)
    assert no_token_logged(logs, [*writer_a.tokens, b.start_token])


class ResetThenStalledCloseWriter(ResetBeforeInsertWriter):
    """Request A's store: the first start fails before its INSERT; each start of the close is slow (longer
    than both close attempts, then the real store judges it) or fails at once, so both close attempts fail
    and the runtime's minimal end, ``fail``, runs with A's start token."""

    def __init__(self, *args: Any, slow: bool) -> None:
        super().__init__(*args)
        self.slow = slow
        self.settled = asyncio.Event()
        if not slow:
            self.settled.set()  # every start fails at once: nothing runs on after the close
        self.duplicate_in.set()

    async def start(self, run: RunStart, *, retry: bool) -> None:
        if not retry or not self.slow:
            self.calls.append(retry)
            self.tokens.append(run.start_token)
            raise ConnectionError("connection reset before the INSERT")
        try:
            await asyncio.sleep(1.0)  # longer than both close attempts below
            await super().start(run, retry=retry)
        finally:
            self.settled.set()


@pytest.mark.parametrize("slow", [True, False], ids=["close-slow-twice", "close-failing-twice"])
async def test_the_minimal_end_never_ends_a_duplicates_live_row(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], slow: bool
) -> None:
    """Spec WP-3.2 §16.3a #1: ``fail`` is the second door. Both close attempts of request A fail (slow twice,
    or failing twice), so the runtime writes the minimal end with A's task, agent and start token. The live
    row of the duplicate request B (same id, task and agent) holds B's token: it stays ``running`` with no
    event, and the slow start, when it reaches the store at last, does not adopt it either."""
    project_id, task_id = await a_task_of(session)
    request = run_request("B14", task_id=task_id, project_id=project_id, agent_run_id=uuid.uuid4())
    b = run_start_of(request)
    await AgentRunWriter(sessions, None, AUDIT).start(b, retry=False)  # B: live, mid-run
    writer_a = ResetThenStalledCloseWriter(sessions, None, AUDIT, slow=slow)
    fakes_a = Fakes(scripted(submit_turn(dict(OUTPUTS["ImplementOutput"]))))
    fakes_a.runs = writer_a  # type: ignore[assignment]
    with capture_logs() as logs:
        with pytest.raises(ConnectionError):
            await make_runtime(fakes_a, runtime_config=config(close_timeout_s=0.2)).run(request)
        async with asyncio.timeout(5):
            await writer_a.settled.wait()  # the slow start the close gave up on has ended too

    assert writer_a.calls == ([False, True] if slow else [False, True, True])
    assert len(set(writer_a.tokens)) == 1 and writer_a.fails == writer_a.tokens[:1] != [b.start_token]
    await assert_live_row_untouched(session, request)
    assert [log["event"] for log in logs if log["event"] == "agent_run_unmarked"] == []
    assert no_token_logged(logs, [*writer_a.tokens, b.start_token])


async def task_event_types(session: AsyncSession, task_id: uuid.UUID) -> list[str]:
    query = select(TaskEvent.type).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
    return list(await session.scalars(query))


async def stored_token(session: AsyncSession, run_id: uuid.UUID) -> uuid.UUID | None:
    session.expire_all()
    return await session.scalar(select(AgentRun.start_token).where(AgentRun.id == run_id))


async def test_either_start_stores_the_runs_token_and_never_publishes_it(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Spec WP-3.2 §16.3a #3, #4: the INSERT of a first start and the INSERT of a retried start that creates
    the row both store the run's token. It leaves the database through no event."""
    project_id, task_id = await a_task_of(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    request = run_request("B14", task_id=task_id, project_id=project_id)
    first, retried = run_start_of(request), replace(run_start_of(request), agent_run_id=uuid.uuid4())
    await writer.start(first, retry=False)
    await writer.start(retried, retry=True)  # its first start's INSERT was never written
    assert await stored_token(session, first.agent_run_id) == first.start_token
    assert await stored_token(session, retried.agent_run_id) == retried.start_token
    assert len(bus.of_type("agent.run_started")) == 2
    assert all(str(token) not in repr(bus.messages) for token in (first.start_token, retried.start_token))


@pytest.mark.parametrize("ended", [False, True], ids=["running", "ended"])
@pytest.mark.parametrize("row_token", ["another-request", "null"])
async def test_a_retried_start_of_another_token_is_a_conflict_before_its_status(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], ended: bool, row_token: str
) -> None:
    """Spec WP-3.2 §16.3a #2: identity first. A retried start (``retry=True``) whose row has the same task
    and agent but another request's token, or none (a row written before migration 0013), is
    ``AgentRunIdConflict``, whether the row is ``running`` or has ended: never ``AgentRunAlreadyFinished``, so
    a caller never reads another request's end as its own. Nothing is written, emitted or cached."""
    project_id, task_id = await a_task_of(session)
    bus = RecordingBus()
    request = run_request("B14", task_id=task_id, project_id=project_id)
    b = run_start_of(request)
    writer_b = AgentRunWriter(sessions, bus, AUDIT)
    await writer_b.start(b, retry=False)
    if ended:
        await writer_b.finish(b.agent_run_id, RunEnd("succeeded", 1, output={"summary": "b"}))
    if row_token == "null":
        await session.execute(
            text("UPDATE agent_runs SET start_token = NULL WHERE id = :id"), {"id": b.agent_run_id}
        )
        await session.commit()
    claim = b if row_token == "null" else replace(b, start_token=uuid.uuid4())
    writer_a = AgentRunWriter(sessions, bus, AUDIT)
    with pytest.raises(AgentRunIdConflict) as raised:
        await writer_a.start(claim, retry=True)
    assert str(claim.start_token) not in str(raised.value)
    session.expire_all()
    row = await session.get(AgentRun, b.agent_run_id)
    assert row is not None and row.status == ("succeeded" if ended else "running")
    expected = ["agent.run_started", "agent.run_finished"] if ended else ["agent.run_started"]
    assert await task_event_types(session, task_id) == expected
    assert [m["type"] for m in bus.messages] == expected
    assert writer_a._projects == {}


async def test_a_retried_start_of_the_same_token_is_idempotent_until_the_run_ends(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Spec WP-3.2 §16.3 #66, §16.3a #2, #3: the same task, agent and token is the request's own row. While it
    is ``running`` a retried start leaves it as it is (no second event); once it has ended, the retry is
    ``AgentRunAlreadyFinished`` (the id is spent). A first start (``retry=False``) is refused whatever the
    token (ruling R10, unchanged)."""
    project_id, task_id = await a_task_of(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run = run_start_of(run_request("B14", task_id=task_id, project_id=project_id))
    await writer.start(run, retry=False)
    writer._projects.clear()  # the first start's ack was lost
    await writer.start(run, retry=True)
    assert writer._projects == {run.agent_run_id: project_id}
    assert await task_event_types(session, task_id) == ["agent.run_started"]
    with pytest.raises(AgentRunIdConflict):
        await writer.start(run, retry=False)
    await writer.finish(run.agent_run_id, RunEnd("cancelled", 0))
    with pytest.raises(AgentRunAlreadyFinished):
        await writer.start(run, retry=True)
    assert await task_event_types(session, task_id) == ["agent.run_started", "agent.run_finished"]
    assert [m["type"] for m in bus.messages] == ["agent.run_started", "agent.run_finished"]


async def test_fail_ends_only_the_row_of_its_start_token(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Spec WP-3.2 §16.3a #1: the minimal end of another request's token, or for a row that holds no token
    (written before migration 0013), ends nothing: no status, no ``agent.run_finished``, no breach event or
    audit. With the row's own token it ends the row."""
    project_id, task_id = await a_task_of(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    request = run_request("B14", task_id=task_id, project_id=project_id)
    live, legacy = run_start_of(request), replace(run_start_of(request), agent_run_id=uuid.uuid4())
    await writer.start(live, retry=False)
    await writer.start(legacy, retry=False)
    await session.execute(
        text("UPDATE agent_runs SET start_token = NULL WHERE id = :id"), {"id": legacy.agent_run_id}
    )
    await session.commit()
    breach = BudgetBreach("tokens", 1, 1, 0)
    await writer.fail(
        live.agent_run_id, task_id=task_id, agent_id="B14", start_token=uuid.uuid4(), breach=breach
    )
    await writer.fail(
        legacy.agent_run_id, task_id=task_id, agent_id="B14", start_token=legacy.start_token, breach=breach
    )
    session.expire_all()
    for run_id in (live.agent_run_id, legacy.agent_run_id):
        row = await session.get(AgentRun, run_id)
        assert row is not None and (row.status, row.error_code, row.finished_at) == ("running", None, None)
    assert await task_event_types(session, task_id) == ["agent.run_started", "agent.run_started"]
    assert bus.of_type("agent.run_finished") == [] and bus.of_type("usage.budget_exceeded") == []
    runs = [str(live.agent_run_id), str(legacy.agent_run_id)]
    audits = await session.scalars(
        select(AuditEvent).where(AuditEvent.action == "budget.exceeded", AuditEvent.resource_id.in_(runs))
    )
    assert list(audits) == []  # the audit log is append-only, never reset: only these runs' rows count

    await writer.fail(live.agent_run_id, task_id=task_id, agent_id="B14", start_token=live.start_token)
    session.expire_all()
    row = await session.get(AgentRun, live.agent_run_id)
    assert row is not None and (row.status, row.error_code) == ("failed", "internal_error")
