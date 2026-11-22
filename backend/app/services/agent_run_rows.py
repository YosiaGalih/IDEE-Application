"""The one closer of ``agent_runs`` rows (spec WP-3.2 §16.3 Keputusan Lead #67, §16.3a #5, #6).

``close_run_row`` holds the only UPDATE of ``agent_runs`` (tests/unit/test_agent_run_writer_guard.py); the row
itself is created only by ``AgentRunWriter.start``. Its callers are ``AgentRunWriter.finish`` and ``fail``
(``app.services.agent_runtime``) and the run recovery's close (``app.services.run_recovery``), and no other
module (tests/unit/test_close_run_row_guard.py).

It writes in the caller's transaction: no ``bound_transaction``, no commit, no publish. Those, the budget
event and audit of a breach, and what an unmatched row means stay with the caller.

This module never holds the Task model (import contract "The runtime ends agent runs; it never moves a task"):
the task side of an event comes from ``app.core.events``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Final

from sqlalchemy import Integer, cast, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import ERROR_TEXT_MAX, RunErrorCode, RunStatus
from app.core.events import EventBus, emit_task_event
from app.core.money import decimal_str, stored_cost
from app.core.redaction import Redactor
from app.models.agents import AgentRun
from app.services.usage import RunTotals, UsageTotals

INT4_MAX: Final = 2_147_483_647  # duration_ms is int4: a run older than ±24.8 days keeps this ceiling
# A run whose usage is not known at all: tokens and cost NULL, ``unavailable`` (``AgentRunWriter.fail``).
UNKNOWN_RUN_TOTALS: Final = RunTotals(UsageTotals(None, None, None, "unavailable"), None, None, rows=0)


class Unwritten(Enum):
    """A column ``close_run_row`` leaves as the running row has it (``output`` SQL NULL, ``iterations`` 0)."""

    UNWRITTEN = "unwritten"


UNWRITTEN: Final = Unwritten.UNWRITTEN


@dataclass(frozen=True, slots=True)
class RowOwner:
    """The run a closer may end besides its id: the row must also be of this task and agent and, when
    ``start_token`` is given, hold that token (migration 0013; spec WP-3.2 §16.3a #1). ``AgentRunWriter.fail``
    always gives it, so its minimal end never ends a duplicate request's row of the same id (nor a row
    written before 0013, whose token is NULL)."""

    task_id: uuid.UUID
    agent_id: str
    start_token: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ClosedRun:
    """The row this call closed: its task and agent, and the cost it stored (ten places, or None)."""

    task_id: uuid.UUID
    agent_id: str
    estimated_cost: Decimal | None


async def close_run_row(
    session: AsyncSession,
    bus: EventBus | None,
    redactor: Redactor,
    agent_run_id: uuid.UUID,
    *,
    status: RunStatus,
    error_code: RunErrorCode | None,
    error: str | None,
    totals: RunTotals,
    output: Mapping[str, Any] | Unwritten | None = UNWRITTEN,
    iterations: int | Unwritten = UNWRITTEN,
    with_duration: bool = True,
    owner: RowOwner | None = None,
) -> ClosedRun | None:
    """UPDATE the row of ``agent_run_id`` while it is still ``running`` (and, with ``owner``, of that task and
    agent, and of its start token when it has one), then ``agent.run_finished`` in ``finish``'s envelope;
    None when no row matched (nothing is written or emitted). The token is only compared: it is never
    written, returned or emitted.

    Written: ``status``, ``error_code``, ``error`` (redacted, cut to ``ERROR_TEXT_MAX``), the four token
    counts, the cost (``stored_cost``) and ``cost_status`` from ``totals``, ``finished_at`` from the DB clock
    and, ``with_duration``, ``duration_ms`` = ``finished_at - started_at`` clamped to ``INT4_MAX``. ``output``
    and ``iterations`` only when given: ``output`` is redacted, and None is stored as the JSON null, as
    ``finish`` always stored it. The event's ``usage`` carries the tokens, the cost as a decimal string and
    ``cost_status``, as the row has them."""
    usage = totals.totals
    cost = stored_cost(usage.estimated_cost) if usage.estimated_cost is not None else None
    values: dict[str, Any] = {
        "status": status,
        "error_code": error_code,
        "error": redactor.redact(error)[:ERROR_TEXT_MAX] if error is not None else None,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": totals.cache_read_tokens,
        "cache_creation_tokens": totals.cache_creation_tokens,
        "estimated_cost": cost,
        "cost_status": usage.cost_status,
        "finished_at": func.now(),
    }
    if output is not UNWRITTEN:
        values["output"] = redactor.redact_obj(dict(output)) if output is not None else None
    if iterations is not UNWRITTEN:
        values["iterations"] = iterations
    if with_duration:
        elapsed_ms = func.extract("epoch", func.now() - AgentRun.started_at) * 1000
        values["duration_ms"] = cast(func.least(elapsed_ms, INT4_MAX), Integer)
    where = [AgentRun.id == agent_run_id, AgentRun.status == "running"]
    if owner is not None:
        where += [AgentRun.task_id == owner.task_id, AgentRun.agent_id == owner.agent_id]
        if owner.start_token is not None:  # NULL (a row written before 0013) never equals a token
            where.append(AgentRun.start_token == owner.start_token)
    closed = (
        await session.execute(
            update(AgentRun)
            .where(*where)
            .values(values)
            .returning(AgentRun.task_id, AgentRun.agent_id)
            .execution_options(synchronize_session=False)
        )
    ).one_or_none()
    if closed is None:
        return None
    task_id, agent_id = closed
    await emit_task_event(
        session,
        bus,
        task_id,
        "agent.run_finished",
        {
            "agent_run_id": str(agent_run_id),
            "agent_id": agent_id,
            "status": status,
            "error_code": error_code,
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "estimated_cost": decimal_str(cost) if cost is not None else None,
                "cost_status": usage.cost_status,
            },
        },
        redactor=redactor,
    )
    return ClosedRun(task_id, agent_id, cost)
