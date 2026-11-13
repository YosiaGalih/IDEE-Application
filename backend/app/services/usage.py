"""The usage ledger (FR-133; spec WP-3.1f Keputusan Lead #3, #4, #8). The only writer of ``usage_records``
(tests/unit/test_usage_writer_guard.py).

One row per attempt that reached the provider, failed and cancelled ones included, priced from the dated
table: ``estimated`` for a finished attempt, ``partial`` when it was cut short (its usage is a lower bound),
and ``unavailable`` without usage or for a model outside the table: never a zero that reads as a price.

Each row is written in its own transaction, so it outlives a rollback of the caller's work, and writing the
same ``(call_id, attempt)`` again returns the stored row instead of doubling it; different content for the
same attempt raises. A failure is never swallowed: ``create_with_retry`` passes it on, so a paid answer that
was not recorded never reaches its caller (Keputusan Lead #8).

Reports (``GET /usage``, the dashboard, ``TaskSummary.usage``) add rows up with one aggregate and one rule for
``cost_status`` (Keputusan Lead #5), only over projects the caller can see (Keputusan Lead #7, AC-01):
``usage:read_all`` does not widen it in v1.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Literal

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.core.money import stored_cost
from app.llm.pricing import EstimateStatus, PricingTable
from app.llm.retry import AttemptHandler, AttemptRecord
from app.models.agents import AgentRun
from app.models.projects import Project
from app.models.tasks import Task
from app.models.usage import REQUEST_ID_MAX, UsageRecord
from app.services import projects
from app.services.projects import Actor, ProjectEnv, visible_projects_filter


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


# --- reports (Keputusan Lead #5, #7) ---------------------------------------------------------------------

GroupBy = Literal["day", "model", "agent"]
RANGE_DEFAULT: Final = timedelta(days=30)
RANGE_MAX: Final = timedelta(days=366)
PAGE_SIZE_DEFAULT: Final = 50
PAGE_SIZE_MAX: Final = 200


@dataclass(frozen=True, slots=True)
class UsageTotals:
    # The known sum, a lower bound when some row has no usage; None when rows exist but none reported tokens
    # (silent-failure M1: unknown is never a counted 0).
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: Decimal | None  # None only when no row could be priced
    cost_status: EstimateStatus


EMPTY_TOTALS: Final = UsageTotals(0, 0, Decimal(0), "estimated")


def aggregate(
    *,
    rows: int,
    partial: int,
    unavailable: int,
    counted: int,
    known_cost: Decimal | None,
    input_tokens: int,
    output_tokens: int,
) -> UsageTotals:
    """Keputusan Lead #5: no rows -> estimated "0"; all estimated -> estimated; any partial, or priced rows
    mixed with unavailable -> partial with the known sum; all unavailable -> unavailable, cost None.
    Tokens are the sum over the ``counted`` rows that reported them; None when rows exist and none did."""
    if rows == 0:
        return UsageTotals(input_tokens, output_tokens, Decimal(0), "estimated")
    known_input = input_tokens if counted else None
    known_output = output_tokens if counted else None
    if unavailable == rows:
        return UsageTotals(known_input, known_output, None, "unavailable")
    cost = known_cost if known_cost is not None else Decimal(0)
    status: EstimateStatus = "partial" if partial or unavailable else "estimated"
    return UsageTotals(known_input, known_output, cost, status)


# One aggregate for totals, series, the dashboard and task summaries.
_AGGREGATE = (
    func.count(),
    func.count().filter(UsageRecord.cost_status == "partial"),
    func.count().filter(UsageRecord.cost_status == "unavailable"),
    func.count(UsageRecord.input_tokens),  # rows that reported tokens (all four are null together)
    func.coalesce(func.sum(UsageRecord.input_tokens), 0),
    func.coalesce(func.sum(UsageRecord.output_tokens), 0),
    func.sum(UsageRecord.estimated_cost),
)


def _usage_in_projects(*where: ColumnElement[bool]) -> Select[UsageRecord]:
    """Ledger rows joined to their project, filtered by ``where``: every report checks the project (its
    visibility, that it is not deleted). Totals and series reuse it with ``with_only_columns``."""
    return select(UsageRecord).join(Project, Project.id == UsageRecord.project_id).where(*where)


def _totals(values: Sequence[Any]) -> UsageTotals:
    rows, partial, unavailable, counted, input_tokens, output_tokens, known_cost = values
    return aggregate(
        rows=rows,
        partial=partial,
        unavailable=unavailable,
        counted=counted,
        known_cost=known_cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@dataclass(frozen=True, slots=True)
class UsageQuery:
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    start: datetime | None = None
    end: datetime | None = None
    group_by: GroupBy = "day"
    page: int = 1
    page_size: int = PAGE_SIZE_DEFAULT


@dataclass(frozen=True, slots=True)
class UsagePoint:
    key: str | None
    totals: UsageTotals


@dataclass(frozen=True, slots=True)
class UsageReport:
    totals: UsageTotals
    series: list[UsagePoint]
    records: list[UsageRecord]
    total: int
    start: datetime
    end: datetime
    group_by: GroupBy
    page: int
    page_size: int


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": ["query", field], "msg": message}],
    )


def _require_time_zones(query: UsageQuery) -> None:
    for field, value in (("from", query.start), ("to", query.end)):
        if value is not None and value.utcoffset() is None:
            raise _invalid(field, "must carry a time zone")


def _in_utc(field: str, value: datetime) -> datetime:
    try:
        return value.astimezone(UTC)
    except OverflowError:  # e.g. 0001-01-01T00:30+01:00 or 9999-12-31T23:59-01:00 (AG-13 L2)
        raise _invalid(field, "is outside the supported dates") from None


def _window(query: UsageQuery, now: datetime) -> tuple[datetime, datetime]:
    """``[from, to)`` in UTC: ``to`` defaults to ``now`` (the database clock), ``from`` to 30 days before; at
    most 366 days. A date that cannot be represented is a 422 naming its field, never a 500."""
    end = _in_utc("to", query.end) if query.end is not None else now.astimezone(UTC)
    if query.start is not None:
        start = _in_utc("from", query.start)
    else:
        try:
            start = end - RANGE_DEFAULT
        except OverflowError:  # a `to` within 30 days of year 1
            raise _invalid("to", "is too early for the default 30-day range") from None
    if start >= end:
        raise _invalid("from", "must be before to")
    if end - start > RANGE_MAX:
        raise _invalid("from", f"the range is at most {RANGE_MAX.days} days")
    return start, end


async def _scope(session: AsyncSession, actor: Actor, query: UsageQuery) -> list[ColumnElement[bool]]:
    """The rows the caller may add up. Out of scope, missing, deleted or mismatched -> 404 (never 403)."""
    where: list[ColumnElement[bool]] = []
    project_id = query.project_id
    if query.task_id is not None:
        task_project = await session.scalar(select(Task.project_id).where(Task.id == query.task_id))
        if task_project is None or (project_id is not None and task_project != project_id):
            raise projects.not_found()
        await projects.authorize(session, actor, task_project, "task:read")
        project_id = task_project
        where.append(UsageRecord.task_id == query.task_id)
    if project_id is not None:
        await projects.authorize(session, actor, project_id, "project:read")
        where.append(UsageRecord.project_id == project_id)
    return where


async def usage_report(env: ProjectEnv, actor: Actor, query: UsageQuery) -> UsageReport:
    """``GET /usage``: totals, a series by ``group_by`` and a page of records (API §9, §11 #9).

    One read-only snapshot holds everything: its clock (``now()``) ends the default window (review L5), the
    scope is decided in it, and totals, series, the count and the page agree with each other. Every 422
    comes before the scope, so it tells nothing about what exists."""
    _require_time_zones(query)
    if query.page < 1 or not 1 <= query.page_size <= PAGE_SIZE_MAX:
        raise _invalid("page_size", f"page >= 1 and page_size 1-{PAGE_SIZE_MAX}")
    async with env.sessions() as session:
        await session.connection(
            execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
        )
        start, end = _window(query, (await session.execute(select(func.now()))).scalar_one())
        where = await _scope(session, actor, query)
        # Every query also joins the visible projects: the same rule as the project list.
        where += [
            visible_projects_filter(actor),
            UsageRecord.created_at >= start,
            UsageRecord.created_at < end,
        ]
        key = {
            "day": func.to_char(func.timezone("UTC", UsageRecord.created_at), "YYYY-MM-DD"),
            "model": UsageRecord.model,
            "agent": UsageRecord.agent_id,
        }[query.group_by]
        in_scope = _usage_in_projects(*where)
        totals_row = (await session.execute(in_scope.with_only_columns(*_AGGREGATE))).one()
        series_rows = (
            await session.execute(
                in_scope.with_only_columns(key, *_AGGREGATE).group_by(key).order_by(key.asc().nulls_last())
            )
        ).all()
        records = list(
            (
                await session.scalars(
                    in_scope.order_by(UsageRecord.created_at.desc(), UsageRecord.id)
                    .limit(query.page_size)
                    .offset((query.page - 1) * query.page_size)
                )
            ).all()
        )
    totals = _totals(totals_row)
    return UsageReport(
        totals=totals,
        series=[UsagePoint(row[0], _totals(row[1:])) for row in series_rows],
        records=records,
        total=totals_row[0],
        start=start,
        end=end,
        group_by=query.group_by,
        page=query.page,
        page_size=query.page_size,
    )


async def period_usage(
    session: AsyncSession, visible: ColumnElement[bool], start: datetime, end: datetime
) -> UsageTotals:
    """Totals of ``[start, end)`` over the projects ``visible`` selects, in the caller's session."""
    in_period = _usage_in_projects(visible, UsageRecord.created_at >= start, UsageRecord.created_at < end)
    row = (await session.execute(in_period.with_only_columns(*_AGGREGATE))).one()
    return _totals(row)


async def task_usage(session: AsyncSession, task_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, UsageTotals]:
    """Totals per task, for tasks the caller already selected in scope; a task without rows is absent."""
    if not task_ids:
        return {}
    found = await session.execute(
        select(UsageRecord.task_id, *_AGGREGATE)
        .where(UsageRecord.task_id.in_(task_ids))
        .group_by(UsageRecord.task_id)
    )
    return {task_id: _totals(values) for task_id, *values in found.all() if task_id is not None}
