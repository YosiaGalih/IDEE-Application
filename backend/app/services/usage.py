"""The usage ledger (FR-133; spec WP-3.1f Keputusan Lead #3, #4, #8). The only writer of ``usage_records``
(tests/unit/test_usage_writer_guard.py).

One row per attempt that reached the provider, failed and cancelled ones included, priced from the dated
table: ``estimated`` for a finished attempt, ``partial`` when it was cut short (its usage is a lower bound),
and ``unavailable`` without usage or for a model outside the table: never a zero that reads as a price.

Each row is written in its own transaction, so it outlives a rollback of the caller's work, and writing the
same ``(call_id, attempt)`` again returns the stored row instead of doubling it; different content for the
same attempt raises. A failure is never swallowed: ``create_with_retry`` passes it on, so a paid answer that
was not recorded never reaches its caller (Keputusan Lead #8).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.money import stored_cost
from app.llm.pricing import PricingTable
from app.llm.retry import AttemptHandler, AttemptRecord
from app.models.agents import AgentRun
from app.models.tasks import Task
from app.models.usage import REQUEST_ID_MAX, UsageRecord


@dataclass(frozen=True, slots=True)
class UsageCallContext:
    """What the ledger needs besides the attempt itself, fixed for one logical model call."""

    project_id: uuid.UUID
    call_id: uuid.UUID  # one logical model call; its retries share it
    input_token_bound: int  # >= 0; computed by the caller (3.1f2)
    output_token_cap: int  # > 0; == LLMRequest.max_tokens
    task_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.input_token_bound, bool) or self.input_token_bound < 0:
            raise ValueError("input_token_bound must be a non-negative integer")
        if isinstance(self.output_token_cap, bool) or self.output_token_cap <= 0:
            raise ValueError("output_token_cap must be a positive integer")
        if self.agent_run_id is not None and (self.task_id is None or self.agent_id is None):
            raise ValueError("an agent run needs its task and its agent")


class UsageContextError(Exception):
    """The call context names a task outside its project, or a run outside its task (T6)."""


class UsageRecordConflict(Exception):
    """The attempt is already recorded with other values: never overwritten, never ignored."""


def build_row(ctx: UsageCallContext, attempt: AttemptRecord, pricing: PricingTable) -> dict[str, object]:
    """The ledger row for one attempt (pure). A failed or cancelled attempt is priced as ``partial``."""
    estimate = pricing.estimate(attempt.model, attempt.usage, partial=attempt.status != "ok")
    usage = attempt.usage
    return {
        "project_id": ctx.project_id,
        "task_id": ctx.task_id,
        "agent_run_id": ctx.agent_run_id,
        "agent_id": ctx.agent_id,
        "call_id": ctx.call_id,
        "attempt": attempt.attempt,
        "retry_count": attempt.attempt - 1,
        "provider": attempt.provider,
        "model": attempt.model,
        "request_id": attempt.request_id[:REQUEST_ID_MAX] if attempt.request_id is not None else None,
        "status": attempt.status,
        "error_kind": attempt.error_kind,
        "input_tokens": usage.input_tokens if usage is not None else None,
        "output_tokens": usage.output_tokens if usage is not None else None,
        "cache_creation_tokens": usage.cache_creation_tokens if usage is not None else None,
        "cache_read_tokens": usage.cache_read_tokens if usage is not None else None,
        "input_token_bound": ctx.input_token_bound,
        "output_token_cap": ctx.output_token_cap,
        "estimated_cost": (
            stored_cost(estimate.estimated_cost) if estimate.estimated_cost is not None else None
        ),
        "cost_status": estimate.cost_status,
        "pricing_version": estimate.pricing_version,
        "duration_ms": attempt.duration_ms,
    }


class UsageLedger:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable) -> None:
        self._sessions = sessions
        self._pricing = pricing

    async def record(self, ctx: UsageCallContext, attempt: AttemptRecord) -> uuid.UUID:
        """Store one attempt in its own transaction and return its row id. Never swallows an error."""
        row = build_row(ctx, attempt, self._pricing)
        async with self._sessions.begin() as session:
            await _check_context(session, ctx)
            inserted = await session.scalar(
                pg_insert(UsageRecord)
                .values(**row)
                .on_conflict_do_nothing(index_elements=["call_id", "attempt"])
                .returning(UsageRecord.id)
            )
            if inserted is not None:
                return inserted
            stored = (
                await session.execute(
                    select(UsageRecord.id, *(getattr(UsageRecord, name) for name in row)).where(
                        UsageRecord.call_id == ctx.call_id, UsageRecord.attempt == attempt.attempt
                    )
                )
            ).one()
            values: dict[str, Any] = dict(stored._mapping)
            differing = sorted(name for name, value in row.items() if values[name] != value)
            if differing:
                raise UsageRecordConflict(
                    f"attempt {attempt.attempt} of call {ctx.call_id} is already recorded with other values "
                    f"({', '.join(differing)})"
                )
            return values["id"]

    def recorder(self, ctx: UsageCallContext) -> AttemptHandler:
        """``on_attempt`` for ``create_with_retry``, bound to one call context."""

        async def on_attempt(attempt: AttemptRecord) -> None:
            await self.record(ctx, attempt)

        return on_attempt


async def _check_context(session: AsyncSession, ctx: UsageCallContext) -> None:
    # A row filed under the wrong project would show one project's usage in another's totals (T6).
    if ctx.task_id is not None:
        project_id = await session.scalar(select(Task.project_id).where(Task.id == ctx.task_id))
        if project_id != ctx.project_id:
            raise UsageContextError("the task is not in this project")
    if ctx.agent_run_id is not None:
        task_id = await session.scalar(select(AgentRun.task_id).where(AgentRun.id == ctx.agent_run_id))
        if task_id != ctx.task_id:
            raise UsageContextError("the agent run is not in this task")
