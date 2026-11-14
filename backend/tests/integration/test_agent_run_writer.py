"""The ``agent_runs`` writer (plan WP-3.1f2a Task 7; spec WP-3.1f Keputusan Lead #5, #15, #21; 3.1b C4).

One row per run: written ``running`` with its identity and redacted input, finished once with its status, its
code, redacted output and error, and totals from the run's own ledger rows. ``budget_exceeded`` also emits
``usage.budget_exceeded`` and audits ``budget.exceeded``, all in the transaction of the finish.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.runtime import ERROR_TEXT_MAX, BudgetBreach, RunEnd, RunStart
from app.core.audit import AuditWriter
from app.core.redaction import Redactor
from app.llm.base import Usage
from app.llm.pricing import PricingTable
from app.llm.retry import AttemptRecord
from app.models.agents import AgentRun
from app.models.audit import AuditEvent
from app.models.enums import AGENT_RUN_ERROR_CODES
from app.models.tasks import TaskEvent
from app.services.agent_runtime import AgentRunAlreadyFinished, AgentRunWriter
from app.services.agents import sync_registry
from app.services.usage import UsageCallContext, UsageLedger
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.runtime_fakes import REGISTRY, RecordingBus

SECRET = "kk-writer-secret-" + "s" * 24  # built, not written: a test value
AUDIT = AuditWriter(Redactor([SECRET]))


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
        "task_id": task_id,
        "project_id": project_id,
        "agent_id": "B14",
        "agent_version": 1,
        "step": "implement",
        "model_provider": "scripted",
        "model_name": "claude-haiku-5-5",
        "prompt_version": "0123456789ab",
        "input": {"task_id": str(task_id), "gap_feedback": "none"},
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
    run_id = await AgentRunWriter(sessions, bus, AUDIT).start(start(task_id, project_id))
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
    (started,) = bus.of_type("agent.run_started")
    assert started["payload"] == {"agent_run_id": str(run_id), "agent_id": "B14", "status": "running"}
    types = list(await session.scalars(select(TaskEvent.type).where(TaskEvent.task_id == task_id)))
    assert types == ["agent.run_started"]


async def test_a_run_refused_before_a_provider_names_no_model(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    project_id, task_id = await a_task(session)
    run_id = await AgentRunWriter(sessions, None, AUDIT).start(
        start(task_id, project_id, model_provider=None, model_name=None)
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
    run_id = await writer.start(start(task_id, project_id, input=run_input))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
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
    run_id = await writer.start(start(task_id, project_id))
    await writer.fail(run_id)
    row = await stored(session, run_id)
    assert (row.status, row.error_code, row.finished_at is not None) == ("failed", "internal_error", True)
    with pytest.raises(AgentRunAlreadyFinished):
        await writer.finish(run_id, RunEnd("succeeded", 1, output={}))
    await writer.fail(run_id)  # a second time changes nothing
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
    run_id = await writer.start(start(task_id, project_id))
    await writer.fail(run_id)
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
    await writer.fail(run_id)  # nothing matched: no second event
    assert len(bus.of_type("agent.run_finished")) == 1


async def test_the_run_store_keeps_no_project_of_an_ended_run(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Review L1: the run -> project map does not grow when finish raises or the minimal end is used."""
    project_id, task_id = await a_task(session)
    writer = AgentRunWriter(sessions, None, AUDIT)
    first = await writer.start(start(task_id, project_id))
    await writer.fail(first)
    assert first not in writer._projects
    second = await writer.start(start(task_id, project_id))
    await writer.finish(second, RunEnd("cancelled", 0))
    assert second not in writer._projects
    with pytest.raises(AgentRunAlreadyFinished):
        await writer.finish(second, RunEnd("cancelled", 0))
    third = await writer.start(start(task_id, project_id))
    with pytest.raises(AgentRunAlreadyFinished):
        await writer.finish(first, RunEnd("cancelled", 0))
    assert set(writer._projects) == {third}
