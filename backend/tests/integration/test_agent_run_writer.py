"""The ``agent_runs`` writer (plan WP-3.1f2a Task 7; spec WP-3.1f Keputusan Lead #5, #15, #21; 3.1b C4).

One row per run: written ``running`` with its identity and redacted input, finished once with its status, its
code, redacted output and error, and totals from the run's own ledger rows. ``budget_exceeded`` also emits
``usage.budget_exceeded`` and audits ``budget.exceeded``, all in the transaction of the finish.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.runtime import (
    ERROR_TEXT_MAX,
    AgentRunIdConflict,
    BudgetBreach,
    RunAlreadyFinished,
    RunEnd,
    RunStart,
    RunUsage,
)
from app.core.audit import AuditWriter
from app.core.money import decimal_str
from app.core.redaction import Redactor
from app.llm.base import Usage
from app.llm.pricing import PricingTable
from app.llm.retry import AttemptRecord
from app.models.agents import AgentRun
from app.models.audit import AuditEvent
from app.models.enums import AGENT_RUN_ERROR_CODES
from app.models.projects import Project
from app.models.tasks import TaskEvent
from app.services.agent_runtime import AgentRunAlreadyFinished, AgentRunWriter, RunOutcome, read_run_outcome
from app.services.agents import sync_registry
from app.services.usage import UsageCallContext, UsageLedger
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.runtime_fakes import REGISTRY, RecordingBus, begin

SECRET = "kk-writer-secret-" + "s" * 24  # built, not written: a test value
AUDIT = AuditWriter(Redactor([SECRET]))
TOKEN = (
    uuid.uuid4()
)  # the start token of the runs ``start`` builds; ``fail`` names it (spec WP-3.2 §16.3a #1)


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


async def a_task(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    await sync_registry(session, REGISTRY, AUDIT)
    project = await make_project(session)
    task = await make_task(session, project)
    await session.commit()
    return project.id, task.id


def start(task_id: uuid.UUID, project_id: uuid.UUID, **overrides: Any) -> RunStart:
    values: dict[str, Any] = {
        "agent_run_id": uuid.uuid4(),
        "task_id": task_id,
        "project_id": project_id,
        "agent_id": "B14",
        "agent_version": 1,
        "step": "implement",
        "model_provider": "scripted",
        "model_name": "claude-haiku-5-5",
        "prompt_version": "0123456789ab",
        "input": {"task_id": str(task_id), "gap_feedback": "none"},
        "start_token": TOKEN,
    }
    values.update(overrides)
    return RunStart(**values)


async def ledger_row(
    sessions: async_sessionmaker[AsyncSession],
    pricing: PricingTable,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    run_id: uuid.UUID | None,
    *,
    status: str = "ok",
    usage: Usage | None = Usage(input_tokens=100, output_tokens=20, cache_read_tokens=5),  # noqa: B008
    model: str = "claude-haiku-5-5",
) -> None:
    context = UsageCallContext(
        project_id=project_id,
        call_id=uuid.uuid4(),
        input_token_bound=1_000,
        output_token_cap=8_192,
        task_id=task_id,
        agent_run_id=run_id,
        agent_id="B14" if run_id else None,
    )
    error_kind = None if status == "ok" else "cancelled"
    attempt = AttemptRecord("scripted", model, 1, "req", usage, 7, status, error_kind)  # type: ignore[arg-type]
    await UsageLedger(sessions, pricing).record(context, attempt)


async def stored(session: AsyncSession, run_id: uuid.UUID) -> AgentRun:
    session.expire_all()
    row = await session.get(AgentRun, run_id)
    assert row is not None
    return row


async def test_start_writes_a_running_row_with_its_identity(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    run_id = await begin(AgentRunWriter(sessions, bus, AUDIT), start(task_id, project_id))
    row = await stored(session, run_id)
    assert (row.task_id, row.agent_id, row.agent_version, row.step, row.status) == (
        task_id,
        "B14",
        1,
        "implement",
        "running",
    )
    assert (row.model_provider, row.model_name, row.prompt_version) == (
        "scripted",
        "claude-haiku-5-5",
        "0123456789ab",
    )
    assert row.start_token == TOKEN  # spec WP-3.2 §16.3 #66: the run's start token, never published
    (started,) = bus.of_type("agent.run_started")
    assert started["payload"] == {"agent_run_id": str(run_id), "agent_id": "B14", "status": "running"}
    types = list(await session.scalars(select(TaskEvent.type).where(TaskEvent.task_id == task_id)))
    assert types == ["agent.run_started"]


async def test_a_run_refused_before_a_provider_names_no_model(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    run_id = await begin(
        AgentRunWriter(sessions, None, AUDIT),
        start(task_id, project_id, model_provider=None, model_name=None),
    )
    row = await stored(session, run_id)
    assert (row.model_provider, row.model_name) == (None, None)


@pytest.mark.parametrize("column", ["input", "output", "error"])
async def test_input_output_and_error_are_redacted(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], column: str
) -> None:
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_input = {"gap_feedback": f"token {SECRET}", "api_key": "plain-value-1"} if column == "input" else {}
    run_id = await begin(writer, start(task_id, project_id, input=run_input))
    if column == "output":
        await writer.finish(
            run_id, RunEnd("succeeded", 1, output={"summary": SECRET, "api_key": "plain-value-1"})
        )
    elif column == "error":
        await writer.finish(run_id, RunEnd("failed", 1, "internal_error", f"boom {SECRET} " + "x" * 3000))
    else:
        await writer.finish(run_id, RunEnd("cancelled", 1))
    row = await stored(session, run_id)
    rendered = json.dumps([row.input, row.output, row.error])
    assert SECRET not in rendered and "plain-value-1" not in rendered
    assert "[REDACTED]" in rendered
    if column == "error":
        assert row.error is not None and len(row.error) <= ERROR_TEXT_MAX


async def test_finish_totals_come_from_the_run_ledger_rows(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable
) -> None:
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await ledger_row(sessions, pricing, project_id, task_id, run_id)
    await ledger_row(sessions, pricing, project_id, task_id, run_id)
    await ledger_row(sessions, pricing, project_id, task_id, run_id, status="cancelled", usage=None)
    await ledger_row(sessions, pricing, project_id, task_id, None)  # the task's, not the run's
    usage = await writer.finish(run_id, RunEnd("succeeded", 3, output={}))
    row = await stored(session, run_id)
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens) == (200, 40, 10)
    assert row.cost_status == "partial"  # a row without usage (Keputusan Lead #5)
    assert row.estimated_cost is not None and row.estimated_cost > 0
    assert (usage.input_tokens, usage.cost_status, usage.estimated_cost) == (
        200,
        "partial",
        row.estimated_cost,
    )
    assert row.finished_at is not None and row.duration_ms is not None and row.duration_ms >= 0


async def test_a_run_without_ledger_rows_is_estimated_zero(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    usage = await writer.finish(
        run_id, RunEnd("failed", 0, "PROVIDER_DISABLED", "PROVIDER_DISABLED (kill_switch)")
    )
    row = await stored(session, run_id)
    assert (row.input_tokens, row.output_tokens, row.estimated_cost, row.cost_status) == (
        0,
        0,
        Decimal(0),
        "estimated",
    )
    assert usage.estimated_cost == Decimal(0)


async def test_tiny_run_cost_keeps_ten_places(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable
) -> None:
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await ledger_row(
        sessions, pricing, project_id, task_id, run_id, usage=Usage(input_tokens=1, output_tokens=0)
    )
    await writer.finish(run_id, RunEnd("succeeded", 1, output={}))
    row = await stored(session, run_id)
    assert row.estimated_cost is not None and Decimal(0) < row.estimated_cost < Decimal("0.00001")
    assert row.estimated_cost.as_tuple().exponent == -10


@pytest.mark.parametrize("code", AGENT_RUN_ERROR_CODES)
async def test_finish_writes_the_failed_code(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], code: str
) -> None:
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await writer.finish(run_id, RunEnd("failed", 2, code, "text"))  # type: ignore[arg-type]
    row = await stored(session, run_id)
    assert (row.status, row.error_code, row.iterations) == ("failed", code, 2)


def test_finish_refuses_a_failed_end_without_a_code() -> None:
    with pytest.raises(ValueError):
        RunEnd("failed", 1)
    with pytest.raises(ValueError):
        RunEnd("succeeded", 1, "model_error")


async def test_budget_exceeded_emits_the_event_and_the_audit_once(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    breach = BudgetBreach("cost", Decimal("2"), Decimal("1.9500000001"), Decimal("0.1"))
    await writer.finish(run_id, RunEnd("budget_exceeded", 3, budget=breach))
    (event,) = bus.of_type("usage.budget_exceeded")
    assert event["payload"] == {"kind": "cost", "limit": "2", "used": "1.9500000001"}
    audits = list(
        await session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "budget.exceeded", AuditEvent.resource_id == str(run_id)
            )
        )
    )
    assert len(audits) == 1
    audit = audits[0]
    assert (audit.actor_type, audit.actor_id, audit.result, audit.resource_type, audit.project_id) == (
        "agent",
        "B14",
        "denied",
        "agent_run",
        project_id,
    )
    assert audit.metadata_ == {
        "task_id": str(task_id),
        "kind": "cost",
        "limit": "2",
        "used": "1.9500000001",
        "next_bound": "0.1",
    }
    row = await stored(session, run_id)
    assert (row.status, row.error_code) == ("budget_exceeded", None)


async def test_an_unbounded_cost_is_used_null(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await writer.finish(
        run_id, RunEnd("budget_exceeded", 1, budget=BudgetBreach("cost", Decimal("2"), None, None))
    )
    (event,) = bus.of_type("usage.budget_exceeded")
    assert event["payload"] == {"kind": "cost", "limit": "2", "used": None}


async def test_a_finished_run_is_not_finished_twice(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await writer.finish(run_id, RunEnd("succeeded", 1, output={"summary": "first"}))
    with pytest.raises(AgentRunAlreadyFinished):
        await writer.finish(run_id, RunEnd("failed", 2, "model_error", "second"))
    row = await stored(session, run_id)
    assert (row.status, row.output, row.error_code) == ("succeeded", {"summary": "first"}, None)
    assert len(bus.of_type("agent.run_finished")) == 1


async def test_run_finished_carries_status_code_and_usage(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable
) -> None:
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await ledger_row(sessions, pricing, project_id, task_id, run_id)
    await writer.finish(run_id, RunEnd("failed", 1, "model_error", "LLMError: refusal"))
    (finished,) = bus.of_type("agent.run_finished")
    payload = finished["payload"]
    assert (payload["agent_run_id"], payload["agent_id"], payload["status"], payload["error_code"]) == (
        str(run_id),
        "B14",
        "failed",
        "model_error",
    )
    assert payload["usage"]["input_tokens"] == 100 and payload["usage"]["cost_status"] == "estimated"
    assert isinstance(payload["usage"]["estimated_cost"], str)
    assert finished["seq"] is not None  # stored, then published


# --- usage truth (review H1 of 42bcb2e) ---------------------------------------------------------------------


async def test_an_incomplete_run_without_rows_is_unavailable_never_zero(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    usage = await writer.finish(run_id, RunEnd("failed", 1, "usage_unrecorded", "not recorded"))
    row = await stored(session, run_id)
    assert (row.input_tokens, row.output_tokens, row.estimated_cost, row.cost_status) == (
        None,
        None,
        None,
        "unavailable",
    )
    assert (usage.input_tokens, usage.estimated_cost, usage.cost_status) == (None, None, "unavailable")
    (finished,) = bus.of_type("agent.run_finished")
    assert finished["payload"]["usage"] == {
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost": None,
        "cost_status": "unavailable",
    }


async def test_an_incomplete_run_with_rows_is_the_known_sum_partial(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable
) -> None:
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await ledger_row(sessions, pricing, project_id, task_id, run_id)
    usage = await writer.finish(run_id, RunEnd("cancelled", 1, usage_complete=False))
    row = await stored(session, run_id)
    assert (row.input_tokens, row.cost_status) == (100, "partial")
    assert row.estimated_cost is not None and row.estimated_cost > 0
    assert (usage.input_tokens, usage.cost_status) == (100, "partial")


async def test_fail_is_the_minimal_end_of_a_running_run(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Review M5: when finish could not be written, failed internal_error, once, only for a running row."""
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await writer.fail(run_id, task_id=task_id, agent_id="B14", start_token=TOKEN)
    row = await stored(session, run_id)
    assert (row.status, row.error_code, row.finished_at is not None) == ("failed", "internal_error", True)
    with pytest.raises(AgentRunAlreadyFinished):
        await writer.finish(run_id, RunEnd("succeeded", 1, output={}))
    await writer.fail(
        run_id, task_id=task_id, agent_id="B14", start_token=TOKEN
    )  # a second time changes nothing
    assert (await stored(session, run_id)).error_code == "internal_error"


async def test_the_minimal_end_publishes_run_finished_once(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    """Review N6 of 073e68d: ``fail`` emits ``agent.run_finished`` in its UPDATE's transaction, only when the
    UPDATE matched a running row: ``failed internal_error``, usage unknown (tokens and cost null,
    ``unavailable``), in the envelope ``finish`` uses."""
    project_id, task_id = await a_task(session)
    bus = RecordingBus(engine)
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await writer.fail(run_id, task_id=task_id, agent_id="B14", start_token=TOKEN)
    (finished,) = bus.of_type("agent.run_finished")
    assert finished["payload"] == {
        "agent_run_id": str(run_id),
        "agent_id": "B14",
        "status": "failed",
        "error_code": "internal_error",
        "usage": {
            "input_tokens": None,
            "output_tokens": None,
            "estimated_cost": None,
            "cost_status": "unavailable",
        },
    }
    assert finished["seq"] is not None and bus.unseen == []  # stored, committed, then published
    row = await stored(session, run_id)
    assert (row.input_tokens, row.estimated_cost, row.cost_status) == (None, None, "unavailable")
    await writer.fail(
        run_id, task_id=task_id, agent_id="B14", start_token=TOKEN
    )  # nothing matched: no second event
    assert len(bus.of_type("agent.run_finished")) == 1


async def test_the_run_store_keeps_no_project_of_an_ended_run(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Review L1: the run -> project map does not grow when finish raises or the minimal end is used."""
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    first = await begin(writer, start(task_id, project_id))
    await writer.fail(first, task_id=task_id, agent_id="B14", start_token=TOKEN)
    assert first not in writer._projects
    second = await begin(writer, start(task_id, project_id))
    await writer.finish(second, RunEnd("cancelled", 0))
    assert second not in writer._projects
    with pytest.raises(AgentRunAlreadyFinished):
        await writer.finish(second, RunEnd("cancelled", 0))
    third = await begin(writer, start(task_id, project_id))
    with pytest.raises(AgentRunAlreadyFinished):
        await writer.finish(first, RunEnd("cancelled", 0))
    assert set(writer._projects) == {third}


async def test_a_retried_start_with_one_id_writes_one_row_and_one_started_event(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A retry after a lost ack (``retry=True``) finds its own ``running`` row: a no-op."""
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    first = start(task_id, project_id)
    await writer.start(first, retry=False)
    await writer.start(first, retry=True)
    rows = list(await session.scalars(select(AgentRun.id).where(AgentRun.task_id == task_id)))
    assert rows == [first.agent_run_id]
    assert len(bus.of_type("agent.run_started")) == 1
    types = list(await session.scalars(select(TaskEvent.type).where(TaskEvent.task_id == task_id)))
    assert types == ["agent.run_started"]


async def test_start_with_a_known_id_for_another_task_is_refused(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    project = await session.get(Project, project_id)
    assert project is not None
    other = await make_task(session, project)
    await session.commit()
    writer = AgentRunWriter(sessions, None, AUDIT)
    first = start(task_id, project_id)
    await writer.start(first, retry=False)
    for retry in (False, True):
        with pytest.raises(AgentRunIdConflict):
            await writer.start(replace(first, task_id=other.id), retry=retry)
        with pytest.raises(AgentRunIdConflict):
            await writer.start(replace(first, agent_id="B15"), retry=retry)
    row = await stored(session, first.agent_run_id)
    assert (row.task_id, row.agent_id) == (task_id, "B14")


async def test_read_run_outcome_reports_missing_running_and_finished(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Keputusan Lead #11: the caller of a run that returned no result reads the row, never the exception."""
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    running_id = await begin(writer, start(task_id, project_id))
    failed_id = await begin(writer, start(task_id, project_id))
    await writer.finish(failed_id, RunEnd("failed", 1, "interrupted", "text"))
    done_id = await begin(writer, start(task_id, project_id))
    await writer.finish(done_id, RunEnd("succeeded", 1, output={}))
    assert await read_run_outcome(session, uuid.uuid4(), task_id=task_id, agent_id="B14") == RunOutcome(
        "missing", None, None
    )
    assert await read_run_outcome(session, running_id, task_id=task_id, agent_id="B14") == RunOutcome(
        "running", "running", None
    )
    assert await read_run_outcome(session, failed_id, task_id=task_id, agent_id="B14") == RunOutcome(
        "finished", "failed", "interrupted"
    )
    assert await read_run_outcome(session, done_id, task_id=task_id, agent_id="B14") == RunOutcome(
        "finished", "succeeded", None
    )


async def test_fail_with_a_breach_audits_budget_exceeded_in_its_transaction(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """D-I5 (#12): the minimal end of a ``budget_exceeded`` run keeps the audit and the event; the status
    stays ``failed internal_error``. The project comes from the task when the cache is empty."""
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    writer._projects.clear()  # a start that found its row leaves no cache entry
    breach = BudgetBreach("cost", Decimal("2"), Decimal("1.95"), Decimal("0.1"))
    await writer.fail(run_id, task_id=task_id, agent_id="B14", start_token=TOKEN, breach=breach)
    row = await stored(session, run_id)
    assert (row.status, row.error_code) == ("failed", "internal_error")
    audit = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "budget.exceeded", AuditEvent.resource_id == str(run_id)
        )
    )
    assert audit is not None and audit.project_id == project_id and audit.resource_id == str(run_id)
    kinds = [event.type for event in await session.scalars(select(TaskEvent).order_by(TaskEvent.seq))]
    assert [k for k in kinds if k in ("agent.run_finished", "usage.budget_exceeded")] == [
        "agent.run_finished",
        "usage.budget_exceeded",
    ]
    (event,) = bus.of_type("usage.budget_exceeded")
    assert event["payload"] == {"kind": "cost", "limit": "2", "used": "1.95"}


async def test_fail_on_a_finished_row_writes_no_audit(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await writer.finish(run_id, RunEnd("cancelled", 0))
    breach = BudgetBreach("tokens", 1, 1, 0)
    await writer.fail(run_id, task_id=task_id, agent_id="B14", start_token=TOKEN, breach=breach)
    audits = list(
        await session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "budget.exceeded", AuditEvent.resource_id == str(run_id)
            )
        )
    )
    assert audits == [] and bus.of_type("usage.budget_exceeded") == []


async def test_start_of_an_ended_run_is_refused_and_changes_nothing(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """AG-13 MEDIUM-1 (ruling R5): an id whose run has ended is spent. A retried ``start`` with it, for the
    same task and agent, raises the port's ``RunAlreadyFinished`` (a first start: ``AgentRunIdConflict``,
    ruling R10); the row stays as it ended and nothing is emitted."""
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    first = start(task_id, project_id)
    await writer.start(first, retry=False)
    await writer.finish(first.agent_run_id, RunEnd("succeeded", 1, output={"summary": "first"}))
    with pytest.raises(RunAlreadyFinished) as raised:
        await writer.start(first, retry=True)
    assert isinstance(raised.value, AgentRunAlreadyFinished)
    with pytest.raises(AgentRunIdConflict):
        await writer.start(first, retry=False)
    row = await stored(session, first.agent_run_id)
    assert (row.status, row.output, row.error_code) == ("succeeded", {"summary": "first"}, None)
    types = list(await session.scalars(select(TaskEvent.type).where(TaskEvent.task_id == task_id)))
    assert types == ["agent.run_started", "agent.run_finished"]
    assert len(bus.of_type("agent.run_started")) == 1
    assert first.agent_run_id not in writer._projects
    with pytest.raises(AgentRunIdConflict):  # another agent's claim on the id is still the conflict
        await writer.start(replace(first, agent_id="B15"), retry=True)


async def test_fail_ends_only_the_row_of_its_task_and_agent(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """AG-13 LOW-1 (ruling R6): the minimal end is bound to the run's identity. With another task's or
    another agent's identity the id names a foreign row, which stays ``running`` with no event."""
    project_id, task_id = await a_task(session)
    project = await session.get(Project, project_id)
    assert project is not None
    other_id = (await make_task(session, project)).id
    await session.commit()
    bus = RecordingBus()
    writer = AgentRunWriter(sessions, bus, AUDIT)
    foreign = await begin(writer, start(other_id, project_id))
    breach = BudgetBreach("tokens", 1, 1, 0)
    await writer.fail(foreign, task_id=task_id, agent_id="B14", start_token=TOKEN, breach=breach)
    await writer.fail(foreign, task_id=other_id, agent_id="B15", start_token=TOKEN)
    row = await stored(session, foreign)
    assert (row.status, row.error_code, row.finished_at) == ("running", None, None)
    assert bus.of_type("agent.run_finished") == [] and bus.of_type("usage.budget_exceeded") == []
    await writer.fail(foreign, task_id=other_id, agent_id="B14", start_token=TOKEN)  # its own identity: ended
    assert (await stored(session, foreign)).error_code == "internal_error"


async def test_read_run_outcome_of_another_task_is_missing(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Ruling R6: the caller reads the row of its own task; a row of another task reads ``missing``."""
    project_id, task_id = await a_task(session)
    project = await session.get(Project, project_id)
    assert project is not None
    other = await make_task(session, project)
    await session.commit()
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(other.id, project_id))
    assert await read_run_outcome(session, run_id, task_id=task_id, agent_id="B14") == RunOutcome(
        "missing", None, None
    )
    assert await read_run_outcome(session, run_id, task_id=other.id, agent_id="B14") == RunOutcome(
        "running", "running", None
    )


async def audits_of(session: AsyncSession, run_id: uuid.UUID) -> list[AuditEvent]:
    session.expire_all()
    query = select(AuditEvent).where(
        AuditEvent.action == "budget.exceeded", AuditEvent.resource_id == str(run_id)
    )
    return list(await session.scalars(query))


async def test_an_idempotent_start_fills_the_project_cache(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Backend M1: a start that found its row still ``running`` caches the run's project like a first one."""
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run = start(task_id, project_id)
    await writer.start(run, retry=False)
    writer._projects.clear()  # the first start's ack was lost; its retry found the row
    await writer.start(run, retry=True)
    assert writer._projects == {run.agent_run_id: project_id}


async def test_finish_with_an_empty_project_cache_audits_the_tasks_project(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Backend M1 / silent L2: ``finish`` and ``fail`` resolve the audit's project the same way, the cache,
    else the task's project (``app.core.events.task_project_id``)."""
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    writer._projects.clear()
    breach = BudgetBreach("tokens", 10, 12, 4)
    await writer.finish(run_id, RunEnd("budget_exceeded", 2, budget=breach))
    [audit] = await audits_of(session, run_id)
    assert audit.project_id == project_id


async def test_a_budget_audit_whose_project_is_unknown_is_logged(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from structlog.testing import capture_logs

    from app.services import agent_runtime

    async def unknown(_session: AsyncSession, _task_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(agent_runtime, "task_project_id", unknown)
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    writer._projects.clear()
    with capture_logs() as logs:
        await writer.finish(run_id, RunEnd("budget_exceeded", 1, budget=BudgetBreach("tokens", 1, 1, 0)))
    [missing] = [log for log in logs if log["event"] == "budget_audit_project_missing"]
    assert (missing["log_level"], missing["agent_run_id"], missing["task_id"]) == (
        "error",
        str(run_id),
        str(task_id),
    )
    [audit] = await audits_of(session, run_id)
    assert audit.project_id is None  # still audited: the trail is kept, its project is not invented


async def test_a_failing_breach_audit_never_rolls_back_the_minimal_end(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    """Silent M4 (ruling R7): the breach's event and audit are written in a savepoint of ``fail``'s
    transaction. A database error there rolls back the savepoint only: the minimal end and its
    ``agent.run_finished`` are committed, and the lost audit is logged with the run's ids and error type."""
    from sqlalchemy import text
    from structlog.testing import capture_logs

    audit = AuditWriter(Redactor([SECRET]))
    real_write = audit.write

    async def broken_write(db: AsyncSession, **fields: Any) -> int:
        if fields.get("action") == "budget.exceeded":
            await db.execute(text("SELECT 1 / 0"))  # a database error: the transaction is aborted
        return await real_write(db, **fields)

    project_id, task_id = await a_task(session)
    bus = RecordingBus(engine)
    writer = AgentRunWriter(sessions, bus, audit)
    run_id = await begin(writer, start(task_id, project_id))
    audit.write = broken_write  # type: ignore[method-assign]
    with capture_logs() as logs:
        await writer.fail(
            run_id, task_id=task_id, agent_id="B14", start_token=TOKEN, breach=BudgetBreach("cost", 2, 3, 1)
        )
    row = await stored(session, run_id)
    assert (row.status, row.error_code) == ("failed", "internal_error")
    types = list(await session.scalars(select(TaskEvent.type).where(TaskEvent.task_id == task_id)))
    assert types == ["agent.run_started", "agent.run_finished"]  # the savepoint's event is gone
    assert [m["type"] for m in bus.messages] == ["agent.run_started", "agent.run_finished"]
    assert bus.unseen == []
    assert await audits_of(session, run_id) == []
    [lost] = [log for log in logs if log["event"] == "budget_audit_unrecorded"]
    assert (lost["log_level"], lost["agent_run_id"], lost["task_id"]) == ("error", str(run_id), str(task_id))
    assert lost["error_type"] == "DBAPIError"  # the type only: an exception text may carry row values
    assert lost["breach_kind"] == "cost"  # which limit the lost audit was about (AG-13 delta INFO-2)


async def test_read_run_outcome_reports_a_paused_run(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Silent L1: ``approval_required`` is a pause waiting for a human, not an end: ``paused``."""
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    pending = {"pending_tool": {"tool_use_id": "toolu_1", "tool_name": "git.push"}}
    await writer.finish(run_id, RunEnd("approval_required", 2, output=pending))
    outcome = await read_run_outcome(session, run_id, task_id=task_id, agent_id="B14")
    assert outcome == RunOutcome("paused", "approval_required", None)


async def test_a_first_start_with_a_used_id_is_refused_whatever_its_row(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """AG-13 MEDIUM-2 (ruling R10): a first start (``retry=False``) whose INSERT creates no row is a conflict,
    whatever the row's task, agent or status: a duplicate request never takes over a live run's row. Nothing
    is written and nothing is emitted."""
    project_id, task_id = await a_task(session)
    bus = RecordingBus()
    live = start(task_id, project_id)
    await AgentRunWriter(sessions, bus, AUDIT).start(live, retry=False)
    duplicate = AgentRunWriter(sessions, bus, AUDIT)  # another request, possibly on another worker
    with pytest.raises(AgentRunIdConflict):
        await duplicate.start(
            replace(live, input={"task_id": str(task_id), "gap_feedback": "dup"}), retry=False
        )
    row = await stored(session, live.agent_run_id)
    assert (row.status, row.input, row.finished_at) == ("running", dict(live.input), None)
    assert duplicate._projects == {}
    types = list(await session.scalars(select(TaskEvent.type).where(TaskEvent.task_id == task_id)))
    assert types == ["agent.run_started"] and len(bus.of_type("agent.run_started")) == 1


async def test_read_run_outcome_of_another_agent_is_missing(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """AG-13 delta LOW-2: the caller reads the row of its own task *and* agent; the same task's row of
    another agent reads ``missing``."""
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id, agent_id="B15"))
    assert await read_run_outcome(session, run_id, task_id=task_id, agent_id="B14") == RunOutcome(
        "missing", None, None
    )
    assert await read_run_outcome(session, run_id, task_id=task_id, agent_id="B15") == RunOutcome(
        "running", "running", None
    )


async def test_a_breach_audit_failing_at_flush_still_publishes_the_minimal_end(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    """Re-review N1 (G3): the breach audit fails at *flush* time inside fail()'s savepoint (an ORM add, as
    AuditWriter.write itself does). SQLAlchemy then rolls back a subtransaction whose boundary is the
    savepoint; the event hook drops only the savepoint's events, so the committed ``agent.run_finished`` is
    still published."""
    from datetime import UTC, datetime

    from structlog.testing import capture_logs

    audit = AuditWriter(Redactor([SECRET]))
    real_write = audit.write

    async def broken_write(db: AsyncSession, **fields: Any) -> int:
        if fields.get("action") == "budget.exceeded":  # an event row of an unknown task: a FK error at flush
            db.add(TaskEvent(task_id=uuid.uuid4(), seq=1, type="x", payload={}, created_at=datetime.now(UTC)))
            await db.flush()
        return await real_write(db, **fields)

    project_id, task_id = await a_task(session)
    bus = RecordingBus(engine)
    writer = AgentRunWriter(sessions, bus, audit)
    run_id = await begin(writer, start(task_id, project_id))
    audit.write = broken_write  # type: ignore[method-assign]
    with capture_logs() as logs:
        await writer.fail(
            run_id, task_id=task_id, agent_id="B14", start_token=TOKEN, breach=BudgetBreach("cost", 2, 3, 1)
        )
    row = await stored(session, run_id)
    assert (row.status, row.error_code) == ("failed", "internal_error")
    types = list(await session.scalars(select(TaskEvent.type).where(TaskEvent.task_id == task_id)))
    assert types == ["agent.run_started", "agent.run_finished"]
    assert [m["type"] for m in bus.messages] == ["agent.run_started", "agent.run_finished"]
    assert bus.unseen == []
    [lost] = [log for log in logs if log["event"] == "budget_audit_unrecorded"]
    assert (lost["error_type"], lost["breach_kind"]) == ("IntegrityError", "cost")


# --- the three closers of agent_runs rows, characterised before they share one helper (WP-3.2c2a Task 1) ----

CLOSE_CLOCK = "now() of the closing transaction"
MEASURED = "finished_at - started_at in ms, as PostgreSQL rounds it"
CACHED = Usage(input_tokens=100, output_tokens=20, cache_creation_tokens=3, cache_read_tokens=5)


def db_milliseconds(started_at: datetime, finished_at: datetime) -> int:
    """``CAST(extract(epoch FROM finished_at - started_at) * 1000 AS integer)``: PostgreSQL rounds a numeric
    half away from zero (the durations here are positive)."""
    micros = (finished_at - started_at) // timedelta(microseconds=1)
    return int((Decimal(micros) / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


async def closed_row(engine: AsyncEngine, run_id: uuid.UUID) -> dict[str, Any]:
    """Every column a closer writes, plus the run's identity and input, as the database holds them.
    ``output_type`` tells the SQL NULL (None) from the JSON null ('null'). ``finished_at`` reads CLOSE_CLOCK
    when it is the closing UPDATE's ``now()`` (the ``updated_at`` the same UPDATE sets, after ``started_at``);
    ``duration_ms`` reads MEASURED when it is ``finished_at - started_at`` as PostgreSQL rounds it. Anything
    else is shown as stored."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT task_id, agent_id, input, status, error_code, error, output, "
                    "jsonb_typeof(output) AS output_type, iterations, input_tokens, output_tokens, "
                    "cache_read_tokens, cache_creation_tokens, estimated_cost, cost_status, started_at, "
                    "updated_at, finished_at, duration_ms FROM agent_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
        ).one()
    found = dict(row._mapping)
    started, updated, finished = found.pop("started_at"), found.pop("updated_at"), found["finished_at"]
    if finished is not None and finished == updated and finished > started:
        found["finished_at"] = CLOSE_CLOCK
    if finished is not None and found["duration_ms"] == db_milliseconds(started, finished):
        found["duration_ms"] = MEASURED
    return found


async def age(engine: AsyncEngine, run_id: uuid.UUID, seconds: str) -> None:
    """Back-dates ``started_at``, so ``duration_ms`` is a real measure, not 0."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE agent_runs SET started_at = started_at - CAST(CAST(:s AS text) AS interval) "
                "WHERE id = :id"
            ),
            {"s": f"{seconds} seconds", "id": run_id},
        )


async def ledger_sum(engine: AsyncEngine, run_id: uuid.UUID) -> Decimal:
    async with engine.connect() as conn:
        query = text("SELECT sum(estimated_cost) FROM usage_records WHERE agent_run_id = :id")
        total = (await conn.execute(query, {"id": run_id})).scalar_one()
    assert isinstance(total, Decimal)
    return total


async def task_events(engine: AsyncEngine, task_id: uuid.UUID) -> list[tuple[str, Any]]:
    """The task's stored events, in seq order: (type, payload)."""
    async with engine.connect() as conn:
        query = text("SELECT type, payload FROM task_events WHERE task_id = :t ORDER BY seq")
        return [(r.type, r.payload) for r in await conn.execute(query, {"t": task_id})]


def started_payload(run_id: uuid.UUID) -> dict[str, Any]:
    return {"agent_run_id": str(run_id), "agent_id": "B14", "status": "running"}


PENDING = {"pending_tool": {"tool_use_id": "toolu_1", "tool_name": "git.push"}}
FINISH_ENDS = [
    pytest.param(
        RunEnd("succeeded", 3, output={"summary": "done " + SECRET, "api_key": "plain-value-1"}),
        2,
        id="succeeded-with-output",
    ),
    pytest.param(
        RunEnd("failed", 2, "model_error", "boom " + SECRET + " " + "x" * 3000, usage_complete=False),
        1,
        id="failed-usage-incomplete",
    ),
    pytest.param(RunEnd("failed", 1, "usage_unrecorded", "not recorded"), 0, id="usage-unrecorded-no-rows"),
    pytest.param(RunEnd("approval_required", 2, output=PENDING), 0, id="paused-no-rows"),
    pytest.param(RunEnd("cancelled", 1), 1, id="cancelled-no-output"),
    pytest.param(
        RunEnd("budget_exceeded", 4, budget=BudgetBreach("cost", Decimal("2"), Decimal("1.5"), None)),
        1,
        id="budget-exceeded",
    ),
]


@pytest.mark.parametrize(("end", "rows"), FINISH_ENDS)
async def test_finish_writes_this_row_and_this_event(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    pricing: PricingTable,
    end: RunEnd,
    rows: int,
) -> None:
    """Characterisation (WP-3.2c2a Task 1): ``AgentRunWriter.finish``, the row and ``agent.run_finished`` it
    writes, stored and published after the commit, before the three closers share ``close_run_row``. A run
    with no output stores the JSON null in ``output`` (not the SQL NULL that ``fail`` and recovery leave)."""
    project_id, task_id = await a_task(session)
    bus = RecordingBus(engine)
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    for _ in range(rows):
        await ledger_row(sessions, pricing, project_id, task_id, run_id, usage=CACHED)
    await age(engine, run_id, "7.2345675")

    usage = await writer.finish(run_id, end)

    unknown = end.error_code == "usage_unrecorded"
    cost = None if unknown else (await ledger_sum(engine, run_id) if rows else Decimal(0))
    status = "unavailable" if unknown else ("partial" if not end.usage_complete else "estimated")
    tokens = (None,) * 4 if unknown else (100 * rows, 20 * rows, 5 * rows, 3 * rows)
    outputs = {
        "succeeded": ({"summary": "done [REDACTED]", "api_key": "[REDACTED]"}, "object"),
        "approval_required": (PENDING, "object"),
    }
    output, output_type = outputs.get(end.status, (None, "null"))
    error = (
        ("boom [REDACTED] " + "x" * 3000)[:ERROR_TEXT_MAX] if end.error_code == "model_error" else end.error
    )
    assert await closed_row(engine, run_id) == {
        "task_id": task_id,
        "agent_id": "B14",
        "input": {"task_id": str(task_id), "gap_feedback": "none"},
        "status": end.status,
        "error_code": end.error_code,
        "error": error,
        "output": output,
        "output_type": output_type,
        "iterations": end.iterations,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "cache_read_tokens": tokens[2],
        "cache_creation_tokens": tokens[3],
        "estimated_cost": cost,
        "cost_status": status,
        "finished_at": CLOSE_CLOCK,
        "duration_ms": MEASURED,
    }
    assert usage == RunUsage(
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cache_read_tokens=tokens[2],
        cache_creation_tokens=tokens[3],
        estimated_cost=cost,
        cost_status=status,
    )
    finished = {
        "agent_run_id": str(run_id),
        "agent_id": "B14",
        "status": end.status,
        "error_code": end.error_code,
        "usage": {
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            "estimated_cost": decimal_str(cost) if cost is not None else None,
            "cost_status": status,
        },
    }
    events = [("agent.run_started", started_payload(run_id)), ("agent.run_finished", finished)]
    if end.budget is not None:
        events.append(("usage.budget_exceeded", {"kind": "cost", "limit": "2", "used": "1.5"}))
    assert await task_events(engine, task_id) == events
    assert [(m["type"], m["payload"]) for m in bus.messages] == events
    assert all(m["seq"] is not None for m in bus.messages) and bus.unseen == []  # committed, then published
    assert len(await audits_of(session, run_id)) == (1 if end.budget is not None else 0)
    assert writer._projects == {}


@pytest.mark.parametrize(
    "breach", [None, BudgetBreach("cost", Decimal("2"), Decimal("2.5"), None)], ids=["plain", "with-breach"]
)
async def test_fail_writes_this_row_and_this_event(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    pricing: PricingTable,
    breach: BudgetBreach | None,
) -> None:
    """Characterisation (WP-3.2c2a Task 1): ``AgentRunWriter.fail``, the minimal end. It reads no ledger
    (tokens and cost stay unknown even when the run has rows), writes no ``output``, ``iterations`` or
    ``duration_ms`` (SQL NULL, 0, NULL as the running row has them) and the fixed error text."""
    project_id, task_id = await a_task(session)
    bus = RecordingBus(engine)
    writer = AgentRunWriter(sessions, bus, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    for _ in range(2):
        await ledger_row(sessions, pricing, project_id, task_id, run_id, usage=CACHED)
    await age(engine, run_id, "7.2345675")

    assert (
        await writer.fail(run_id, task_id=task_id, agent_id="B14", start_token=TOKEN, breach=breach) is None
    )

    assert await closed_row(engine, run_id) == {
        "task_id": task_id,
        "agent_id": "B14",
        "input": {"task_id": str(task_id), "gap_feedback": "none"},
        "status": "failed",
        "error_code": "internal_error",
        "error": "the end of the run could not be recorded",
        "output": None,
        "output_type": None,
        "iterations": 0,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_creation_tokens": None,
        "estimated_cost": None,
        "cost_status": "unavailable",
        "finished_at": CLOSE_CLOCK,
        "duration_ms": None,
    }
    finished = {
        "agent_run_id": str(run_id),
        "agent_id": "B14",
        "status": "failed",
        "error_code": "internal_error",
        "usage": {
            "input_tokens": None,
            "output_tokens": None,
            "estimated_cost": None,
            "cost_status": "unavailable",
        },
    }
    events = [("agent.run_started", started_payload(run_id)), ("agent.run_finished", finished)]
    if breach is not None:
        events.append(("usage.budget_exceeded", {"kind": "cost", "limit": "2", "used": "2.5"}))
    assert await task_events(engine, task_id) == events
    assert [(m["type"], m["payload"]) for m in bus.messages] == events
    assert all(m["seq"] is not None for m in bus.messages) and bus.unseen == []
    assert len(await audits_of(session, run_id)) == (1 if breach is not None else 0)
    assert writer._projects == {}


async def test_finish_of_a_run_older_than_the_int4_milliseconds_keeps_the_ceiling(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    """The one deliberate uniformity of ``close_run_row`` (spec WP-3.2 §16.3a #5): ``duration_ms`` (int4) is
    clamped for ``finish`` as recovery always clamped it. Before, a run older than ±24.8 days made ``finish``
    fail on the cast (such a run is out of reach: its timeout is at most an hour)."""
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    run_id = await begin(writer, start(task_id, project_id))
    await age(engine, run_id, str(25 * 24 * 3600))
    await writer.finish(run_id, RunEnd("cancelled", 1))
    row = await stored(session, run_id)
    assert (row.status, row.duration_ms) == ("cancelled", 2_147_483_647)
