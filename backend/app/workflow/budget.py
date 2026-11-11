"""BudgetGuard: the task budget, checked before every model attempt (SECURITY_MODEL T4, ARCHITECTURE §5.4;
spec WP-3.1f Keputusan Lead #2, #6, #11; AG-13 3.1a M2).

**Caps.** Per field, the task's ``budget_limit`` wins, then the project's default (``settings["budget"]``),
then the env default (``TASK_MAX_COST_USD``, ``TASK_MAX_TOKENS``, ``TASK_MAX_DURATION_MINUTES``).
``max_tool_iterations`` has no env default: without a value the task has no tool cap. A stored budget that is
malformed (a float, a negative, an unknown key) fails closed with ``BudgetConfigError``, never falls through
to a default.

**Accounting.** Conservative, from the ledger rows of this task only: a finished attempt counts what it
recorded; an attempt cut short counts its output cap, priced at the dearest rates the model (or, for an
unknown model, the whole table) can be billed at. Only a request refused before it was processed (rate
limited, overloaded, bad request, auth) and without usage counts nothing. Tokens are input + output + cache
writes; cache reads are left to the cost cap, since an agent loop resends its cached context every turn.

**Check.** Before each attempt: duration (``elapsed >= limit``, on the DB clock from ``tasks.started_at``;
not checked before the task first runs), tool iterations (``used > limit``), then tokens and cost
(``used + bound(next) > limit``). The first breach raises ``BudgetExceeded``; approvals and task transitions
belong to the caller (3.1f2/3.2). Every check reads afresh in a short session of its own, without a row lock
(ADR-070). It never calls a provider (import-linter contract).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Final, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.money import decimal_str
from app.llm.base import ErrorKind
from app.llm.pricing import PricingTable
from app.models.projects import Project
from app.models.tasks import Task
from app.models.tools import ToolCall
from app.models.usage import UsageRecord

BudgetKind = Literal["duration", "tool_iterations", "tokens", "cost"]
BUDGET_KEYS: Final = ("max_cost_usd", "max_tokens", "max_tool_iterations", "max_duration_minutes")
# Keputusan Lead #6: a request refused before it was processed costs nothing.
REJECTED_BEFORE_PROCESSING: Final[frozenset[ErrorKind]] = frozenset(
    {"rate_limited", "overloaded", "bad_request", "auth"}
)
# Keputusan Lead #6: iterations are tool calls that reached a handler.
UNCOUNTED_TOOL_STATUSES: Final = ("denied", "approval_required")

_MILLION: Final = Decimal(1_000_000)
_MINUTES_QUANTUM: Final = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class CallBound:
    """The most the next attempt can use: its prompt bound and its output cap (``LLMRequest.max_tokens``)."""

    model: str
    input_token_bound: int  # >= 0
    output_token_cap: int  # > 0

    def __post_init__(self) -> None:
        if isinstance(self.input_token_bound, bool) or self.input_token_bound < 0:
            raise ValueError("input_token_bound must be a non-negative integer")
        if isinstance(self.output_token_cap, bool) or self.output_token_cap <= 0:
            raise ValueError("output_token_cap must be a positive integer")


class BudgetExceeded(Exception):
    """The next attempt would go over a cap.

    ``used`` is what the ledger already counts (None: its cost cannot be bounded, the table has no prices);
    ``next_bound`` is the bound of the next attempt that pushed it over (0 for duration and tool iterations;
    None: the cost cannot be bounded).
    """

    def __init__(
        self,
        kind: BudgetKind,
        limit: int | Decimal,
        used: int | Decimal | None,
        *,
        next_bound: int | Decimal | None = 0,
    ) -> None:
        bound = "cannot be bounded" if next_bound is None else str(next_bound)
        super().__init__(f"budget exceeded: {kind} (limit {limit}, used {used}, next attempt {bound})")
        self.kind: BudgetKind = kind
        self.limit = limit
        self.used = used
        self.next_bound = next_bound


class BudgetConfigError(ValueError):
    """A stored budget is malformed: the check fails closed instead of using a default."""


class BudgetTaskMissing(LookupError):
    """The task to check no longer exists."""


# --- caps ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetFields:
    """One level: the task's ``budget_limit`` or the project's default. ``None`` inherits the next level."""

    max_cost_usd: Decimal | None = None
    max_tokens: int | None = None
    max_tool_iterations: int | None = None
    max_duration_minutes: int | None = None


def _cost_value(value: object) -> Decimal | None:
    if value is None:
        return None
    # JSONB gives a number back as a float, which cannot hold money exactly: costs are stored as strings.
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise BudgetConfigError("max_cost_usd must be a decimal string or an integer")
    try:
        cost = Decimal(value)
    except InvalidOperation:
        raise BudgetConfigError("max_cost_usd is not a decimal number") from None
    if not cost.is_finite() or cost < 0:
        raise BudgetConfigError("max_cost_usd must be finite and not negative")
    return cost


def _count_value(name: str, value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BudgetConfigError(f"{name} must be a non-negative integer")
    return value


def parse_budget(raw: object) -> BudgetFields:
    """``None``/``{}``: every field inherits. Otherwise a mapping with only ``BUDGET_KEYS``: ``max_cost_usd``
    a decimal string or an integer, the others integers, all finite and not negative. Anything else (a float,
    a bool, an unknown key, a non-mapping) raises ``BudgetConfigError``."""
    if raw is None:
        return BudgetFields()
    if not isinstance(raw, Mapping):
        raise BudgetConfigError("a budget must be a mapping")
    fields: Mapping[object, object] = raw  # pyright: ignore[reportUnknownVariableType]
    unknown = [str(key) for key in fields if key not in BUDGET_KEYS]
    if unknown:
        raise BudgetConfigError(f"unknown budget keys: {', '.join(sorted(unknown))}")
    return BudgetFields(
        max_cost_usd=_cost_value(fields.get("max_cost_usd")),
        max_tokens=_count_value("max_tokens", fields.get("max_tokens")),
        max_tool_iterations=_count_value("max_tool_iterations", fields.get("max_tool_iterations")),
        max_duration_minutes=_count_value("max_duration_minutes", fields.get("max_duration_minutes")),
    )


def dump_budget(fields: BudgetFields) -> dict[str, str | int | None]:
    """All four keys, the cost as a decimal string, for JSONB (``parse_budget`` reads it back exactly)."""
    return {
        "max_cost_usd": decimal_str(fields.max_cost_usd) if fields.max_cost_usd is not None else None,
        "max_tokens": fields.max_tokens,
        "max_tool_iterations": fields.max_tool_iterations,
        "max_duration_minutes": fields.max_duration_minutes,
    }


@dataclass(frozen=True, slots=True)
class BudgetDefaults:
    max_cost_usd: Decimal
    max_tokens: int
    max_duration_minutes: int

    @classmethod
    def from_settings(cls, settings: Settings) -> BudgetDefaults:
        return cls(
            max_cost_usd=settings.task_max_cost_usd,
            max_tokens=settings.task_max_tokens,
            max_duration_minutes=settings.task_max_duration_minutes,
        )


@dataclass(frozen=True, slots=True)
class EffectiveBudget:
    max_cost_usd: Decimal
    max_tokens: int
    max_duration_minutes: int
    max_tool_iterations: int | None  # None = no task cap (Keputusan Lead #2)


def _inherit[T](task: T | None, project: T | None) -> T | None:
    # Explicit None checks: a stored 0 is a cap, not a missing value.
    return task if task is not None else project


def effective_budget(task: BudgetFields, project: BudgetFields, defaults: BudgetDefaults) -> EffectiveBudget:
    """Per field: task, then project, then env (``max_tool_iterations`` has no env default)."""
    cost = _inherit(task.max_cost_usd, project.max_cost_usd)
    tokens = _inherit(task.max_tokens, project.max_tokens)
    minutes = _inherit(task.max_duration_minutes, project.max_duration_minutes)
    return EffectiveBudget(
        max_cost_usd=cost if cost is not None else defaults.max_cost_usd,
        max_tokens=tokens if tokens is not None else defaults.max_tokens,
        max_duration_minutes=minutes if minutes is not None else defaults.max_duration_minutes,
        max_tool_iterations=_inherit(task.max_tool_iterations, project.max_tool_iterations),
    )


# --- accounting ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedgerRow:
    status: str
    error_kind: str | None
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_tokens: int | None
    cache_read_tokens: int | None
    estimated_cost: Decimal | None
    input_token_bound: int
    output_token_cap: int


@dataclass(frozen=True, slots=True)
class PriceCeiling:
    """USD per million tokens, the highest of every tier considered."""

    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal

    def cost(self, *, input_tokens: int, output_tokens: int, cache_write: int, cache_read: int) -> Decimal:
        return (
            input_tokens * self.input
            + output_tokens * self.output
            + cache_write * self.cache_write
            + cache_read * self.cache_read
        ) / _MILLION


def price_ceiling(pricing: PricingTable, model: str) -> PriceCeiling | None:
    """The dearest rates ``model`` can be billed at, long-prompt tier included; for a model outside the
    table, the dearest rates of the whole table. None only for a table without models."""
    price = pricing.models.get(model)
    candidates = [price] if price is not None else list(pricing.models.values())
    tiers = [t for candidate in candidates for t in (candidate, candidate.long_prompt) if t is not None]
    if not tiers:
        return None
    return PriceCeiling(
        input=max(tier.input for tier in tiers),
        output=max(tier.output for tier in tiers),
        cache_write=max(tier.cache_write for tier in tiers),
        cache_read=max(tier.cache_read for tier in tiers),
    )


@dataclass(frozen=True, slots=True)
class Charge:
    tokens: int  # input + output + cache_creation (cache_read excluded)
    cost: Decimal | None  # None = cannot be bounded


def bound_charge(call: CallBound, pricing: PricingTable) -> Charge:
    """The worst case of an attempt: its whole prompt bound, priced as a cache write when that is dearer,
    and its whole output cap."""
    tokens = call.input_token_bound + call.output_token_cap
    ceiling = price_ceiling(pricing, call.model)
    if ceiling is None:
        return Charge(tokens, None)
    worst_input = max(ceiling.input, ceiling.cache_write)
    return Charge(
        tokens, (call.input_token_bound * worst_input + call.output_token_cap * ceiling.output) / _MILLION
    )


def charge(row: LedgerRow, pricing: PricingTable) -> Charge:
    """What one ledger row counts against the budget (Keputusan Lead #6, Keputusan plan #4)."""
    if row.input_tokens is None:
        if row.status != "ok" and row.error_kind in REJECTED_BEFORE_PROCESSING:
            return Charge(0, Decimal(0))
        # Failed, cancelled or finished without a usage report: the whole bound, at worst-case prices.
        return bound_charge(CallBound(row.model, row.input_token_bound, row.output_token_cap), pricing)
    input_tokens = row.input_tokens
    output_tokens = row.output_tokens or 0
    cache_write = row.cache_creation_tokens or 0
    cache_read = row.cache_read_tokens or 0
    if row.status == "ok":
        tokens = input_tokens + output_tokens + cache_write
        if row.estimated_cost is not None:
            return Charge(tokens, row.estimated_cost)
    else:
        # Cut short: the provider reports output at the end of the stream, so up to the cap may be billed.
        output_tokens = max(output_tokens, row.output_token_cap)
        tokens = input_tokens + cache_write + output_tokens
    ceiling = price_ceiling(pricing, row.model)
    if ceiling is None:
        return Charge(tokens, None)
    cost = ceiling.cost(
        input_tokens=input_tokens, output_tokens=output_tokens, cache_write=cache_write, cache_read=cache_read
    )
    return Charge(tokens, cost)


# --- check --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    tokens: int
    cost: Decimal | None  # None = cannot be bounded
    tool_iterations: int
    elapsed: timedelta | None  # None while tasks.started_at is NULL


def _minutes(elapsed: timedelta) -> Decimal:
    microseconds = (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
    return (Decimal(microseconds) / 60_000_000).quantize(_MINUTES_QUANTUM, rounding=ROUND_FLOOR)


def evaluate(
    budget: EffectiveBudget, usage: BudgetUsage, next_call: CallBound, pricing: PricingTable
) -> None:
    """Order: duration (``elapsed >= limit``), tool iterations (``used > limit``), then tokens and cost
    (``used + bound(next) > limit``). Raises the first ``BudgetExceeded``."""
    minutes = budget.max_duration_minutes
    if usage.elapsed is not None and usage.elapsed >= timedelta(minutes=minutes):
        raise BudgetExceeded("duration", minutes, _minutes(usage.elapsed))
    tool_cap = budget.max_tool_iterations
    if tool_cap is not None and usage.tool_iterations > tool_cap:
        raise BudgetExceeded("tool_iterations", tool_cap, usage.tool_iterations)
    bound = bound_charge(next_call, pricing)
    if usage.tokens + bound.tokens > budget.max_tokens:
        raise BudgetExceeded("tokens", budget.max_tokens, usage.tokens, next_bound=bound.tokens)
    if usage.cost is None or bound.cost is None:
        raise BudgetExceeded("cost", budget.max_cost_usd, usage.cost, next_bound=None)
    if usage.cost + bound.cost > budget.max_cost_usd:
        raise BudgetExceeded("cost", budget.max_cost_usd, usage.cost, next_bound=bound.cost)


def _ledger_usage(rows: Iterable[LedgerRow], pricing: PricingTable) -> tuple[int, Decimal | None]:
    tokens = 0
    cost: Decimal | None = Decimal(0)
    for row in rows:
        counted = charge(row, pricing)
        tokens += counted.tokens
        cost = None if cost is None or counted.cost is None else cost + counted.cost
    return tokens, cost


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    budget: EffectiveBudget
    usage: BudgetUsage


_LEDGER_COLUMNS = (
    UsageRecord.status,
    UsageRecord.error_kind,
    UsageRecord.model,
    UsageRecord.input_tokens,
    UsageRecord.output_tokens,
    UsageRecord.cache_creation_tokens,
    UsageRecord.cache_read_tokens,
    UsageRecord.estimated_cost,
    UsageRecord.input_token_bound,
    UsageRecord.output_token_cap,
)


class BudgetGuard:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable, defaults: BudgetDefaults
    ) -> None:
        self._sessions = sessions
        self._pricing = pricing
        self._defaults = defaults

    async def check_before_attempt(self, task_id: uuid.UUID, next_call: CallBound) -> BudgetSnapshot:
        """Read the caps and the usage of ``task_id`` afresh, then ``evaluate``. One short session that only
        reads; no row lock (ADR-070). ``BudgetTaskMissing`` when the task is gone."""
        async with self._sessions() as session:
            found = (
                await session.execute(
                    select(func.now(), Task.started_at, Task.budget_limit, Project.settings)
                    .join(Project, Project.id == Task.project_id)
                    .where(Task.id == task_id)
                )
            ).one_or_none()
            if found is None:
                raise BudgetTaskMissing(f"task {task_id} does not exist")
            now, started_at, budget_limit, project_settings = found
            tool_iterations = await session.scalar(
                select(func.count())
                .select_from(ToolCall)
                .where(ToolCall.task_id == task_id, ToolCall.status.not_in(UNCOUNTED_TOOL_STATUSES))
            )
            ledger = (
                await session.execute(select(*_LEDGER_COLUMNS).where(UsageRecord.task_id == task_id))
            ).all()
        rows = [LedgerRow(*values) for values in ledger]
        if not isinstance(project_settings, Mapping):
            raise BudgetConfigError("project settings must be a mapping")
        budget = effective_budget(
            parse_budget(budget_limit),
            parse_budget(project_settings.get("budget")),  # pyright: ignore[reportUnknownMemberType]
            self._defaults,
        )
        tokens, cost = _ledger_usage(rows, self._pricing)
        usage = BudgetUsage(
            tokens=tokens,
            cost=cost,
            tool_iterations=tool_iterations or 0,
            elapsed=now - started_at if started_at is not None else None,
        )
        evaluate(budget, usage, next_call, self._pricing)
        return BudgetSnapshot(budget, usage)

    def before_attempt(self, task_id: uuid.UUID, next_call: CallBound) -> Callable[[int], Awaitable[None]]:
        """Adapter for ``create_with_retry(before_attempt=...)``; the type is spelled here so this module
        never imports ``app.llm.retry``."""

        async def check(attempt: int) -> None:
            await self.check_before_attempt(task_id, next_call)

        return check
