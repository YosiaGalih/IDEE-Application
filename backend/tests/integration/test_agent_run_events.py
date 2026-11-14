"""Run events over a real database and a recording bus (plan WP-3.1f2a Task 6; API_CONTRACTS §5.1; spec
WP-3.1f Keputusan Lead #24, #34).

``usage.updated`` follows every model call with the task's ledger totals, stored and published only after its
commit; ``agent.delta`` is redacted across deltas, ephemeral, never stored, and skipped without a bus.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.money import decimal_str
from app.core.redaction import Redactor
from app.llm.base import TextBlock, ToolUseBlock
from app.llm.pricing import PricingTable
from app.llm.scenarios.model import Reply
from app.models.tasks import TaskEvent
from app.services.agent_runtime import TaskRunEvents
from app.services.usage import task_usage
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.runtime_fakes import (
    OUTPUTS,
    FakeTools,
    RecordingBus,
    compose,
    hold_lease,
    run_request,
    scripted,
    submit_turn,
    tool_turn,
)
from tests.security.test_runtime_gate import KEY, record, worker

DONE = submit_turn(OUTPUTS["ImplementOutput"])
FILLER = "The model explains the change it is about to submit, at some length. " * 6


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


def final_turn(text: str) -> Reply:
    return Reply(
        (TextBlock(text), ToolUseBlock("toolu_submit", "submit_result", OUTPUTS["ImplementOutput"])),
        stop_reason="tool_use",
    )


async def stored_types(session: AsyncSession, task_id: uuid.UUID) -> list[str]:
    return list(await session.scalars(select(TaskEvent.type).where(TaskEvent.task_id == task_id)))


async def test_usage_updated_follows_each_model_call_with_task_totals(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    pricing: PricingTable,
) -> None:
    project_id, task_id = await a_task(session, settings)
    bus = RecordingBus(engine)
    redactor = Redactor(settings.secret_values())
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(tool_turn("repo.get_tree"), DONE),
        tools=FakeTools([]),
        events=TaskRunEvents(sessions, bus, redactor),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert result.status == "succeeded"
    updates = bus.of_type("usage.updated")
    assert len(updates) == 2
    totals = (await task_usage(session, [task_id]))[task_id]
    last = updates[-1]["payload"]
    assert last == {
        "input_tokens": totals.input_tokens,
        "output_tokens": totals.output_tokens,
        "estimated_cost": decimal_str(totals.estimated_cost) if totals.estimated_cost is not None else None,
        "cost_status": totals.cost_status,
    }
    assert last["input_tokens"] == 200  # two scripted calls of 100 input tokens each
    assert last["estimated_cost"] is None or isinstance(last["estimated_cost"], str)
    assert (await stored_types(session, task_id)).count("usage.updated") == 2
    assert bus.unseen == []  # published only after commit


async def test_usage_updated_money_is_a_decimal_string(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable
) -> None:
    settings = worker(CLAUDE_MODEL_DEFAULT="claude-haiku-5-5")  # a priced model
    project_id, task_id = await a_task(session, settings)
    bus = RecordingBus()
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(DONE),
        tools=FakeTools([]),
        events=TaskRunEvents(sessions, bus, Redactor()),
    )
    await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    (update,) = bus.of_type("usage.updated")
    cost = update["payload"]["estimated_cost"]
    assert isinstance(cost, str) and Decimal(cost) > 0 and "E" not in cost
    assert update["payload"]["cost_status"] == "estimated"


async def test_deltas_are_redacted_and_never_stored(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
) -> None:
    project_id, task_id = await a_task(session, settings)
    bus = RecordingBus()
    redactor = Redactor(settings.secret_values())
    text = FILLER + f"The key is {KEY} and that is all." + FILLER
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(final_turn(text), delta_size=16),
        tools=FakeTools([]),
        events=TaskRunEvents(sessions, bus, redactor),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert result.status == "succeeded"
    deltas = bus.of_type("agent.delta")
    assert len(deltas) >= 2  # streamed in more than one piece
    assert all(d["seq"] is None and d["payload"]["agent_run_id"] == str(result.agent_run_id) for d in deltas)
    assert all(d["payload"]["reset"] is False for d in deltas)
    sent = "".join(d["payload"]["text"] for d in deltas)
    assert KEY not in sent and KEY[7:-4] not in sent
    assert sent == redactor.redact(text)
    assert "agent.delta" not in await stored_types(session, task_id)
    assert KEY not in json.dumps(bus.messages)


async def test_no_bus_means_no_delta_and_no_failure(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
) -> None:
    project_id, task_id = await a_task(session, settings)
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(final_turn(FILLER)),
        tools=FakeTools([]),
        events=TaskRunEvents(sessions, None, Redactor()),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert result.status == "succeeded"
    types = await stored_types(session, task_id)
    assert "agent.delta" not in types and types.count("usage.updated") == 1


async def test_events_are_published_only_after_commit(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    pricing: PricingTable,
) -> None:
    project_id, task_id = await a_task(session, settings)
    bus = RecordingBus(engine)
    events = TaskRunEvents(sessions, bus, Redactor())
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(tool_turn("repo.get_tree"), tool_turn("repo.get_tree"), DONE),
        tools=FakeTools([]),
        events=events,
    )
    await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    stored = [m for m in bus.messages if m["seq"] is not None]
    assert len(stored) == 3 and bus.unseen == []
    assert [m["seq"] for m in stored] == sorted(m["seq"] for m in stored)


async def test_a_failed_attempt_leaves_no_held_text_behind(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
) -> None:
    """A retried attempt that streamed text: its held text is dropped (Keputusan plan #12), the retry streams
    afresh."""
    from tests.runtime_fakes import in_order

    project_id, task_id = await a_task(session, settings)
    bus = RecordingBus()
    streamed: list[Any] = []

    class StreamThenFail:
        name = "scripted"

        def __init__(self) -> None:
            self.inner = scripted(in_order(final_turn("SECOND " + FILLER)))
            self.calls = 0
            self.requests = self.inner.requests

        async def create_message(self, req: Any, *, on_text_delta: Any = None, cancel: Any = None) -> Any:
            from app.llm.base import LLMError

            self.calls += 1
            if self.calls == 1:
                assert on_text_delta is not None
                await on_text_delta("FIRSTATTEMPT" * 10)
                streamed.append(1)
                raise LLMError("overloaded")
            return await self.inner.create_message(req, on_text_delta=on_text_delta, cancel=cancel)

    provider = StreamThenFail()
    run = compose(
        sessions,
        settings,
        pricing,
        provider,
        tools=FakeTools([]),  # type: ignore[arg-type]
        events=TaskRunEvents(sessions, bus, Redactor()),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert result.status == "succeeded" and streamed == [1]
    events = bus.of_type("agent.delta")
    sent = "".join(d["payload"]["text"] for d in events)
    assert "FIRSTATTEMPT" not in sent  # held (under 256 characters), then dropped on restart
    # M4: the restart voids what the failed attempt showed: an empty reset first, then the retry's text.
    assert (events[0]["payload"]["text"], events[0]["payload"]["reset"]) == ("", True)
    assert sent.startswith("SECOND ") and not any(d["payload"]["reset"] for d in events[1:])


async def test_run_started_is_committed_before_the_first_model_call(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    pricing: PricingTable,
) -> None:
    """Moved here from Task 6: the run store writes ``agent.run_started`` (plan WP-3.1f2a Task 7)."""
    project_id, task_id = await a_task(session, settings)
    seen: list[list[str]] = []

    class Peeking:
        name = "scripted"

        def __init__(self) -> None:
            self.inner = scripted(DONE)
            self.requests = self.inner.requests

        async def create_message(self, req: Any, *, on_text_delta: Any = None, cancel: Any = None) -> Any:
            async with engine.connect() as conn:  # another connection: only committed rows
                found = await conn.execute(
                    select(TaskEvent.type).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
                )
                seen.append([row[0] for row in found])
            return await self.inner.create_message(req, on_text_delta=on_text_delta, cancel=cancel)

    bus = RecordingBus(engine)
    run = compose(sessions, settings, pricing, Peeking(), tools=FakeTools([]), bus=bus)  # type: ignore[arg-type]
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert result.status == "succeeded"
    assert seen == [["agent.run_started"]]
    assert [m["type"] for m in bus.messages if m["seq"] is not None] == [
        "agent.run_started",
        "usage.updated",
        "agent.run_finished",
    ]
    assert bus.unseen == []
