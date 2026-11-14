"""BudgetGuard against a real database, and end to end with the retry loop and the ledger (SECURITY_MODEL T4;
AG-13 3.1a M2; spec WP-3.1f Keputusan Lead #2, #6, #11; plan 3.1f1 Task 4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.cancel import CancelToken
from app.core.config import Settings
from app.llm.base import LLMError, Usage
from app.llm.pricing import PricingTable
from app.llm.retry import AttemptRecord, create_with_retry
from app.llm.scripted_provider import ScriptedProvider
from app.models.projects import Project
from app.models.tasks import Task
from app.services.usage import UsageCallContext, UsageLedger
from app.workflow import budget
from app.workflow.budget import (
    BudgetConfigError,
    BudgetDefaults,
    BudgetExceeded,
    BudgetGuard,
    BudgetTaskMissing,
    CallBound,
    EffectiveBudget,
)
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.integration.test_tool_tables import call
from tests.usage_fakes import CAP, HAIKU, no_sleep, request

BOUND = 1_000
NEXT = CallBound(HAIKU, BOUND, CAP)


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def guard(
    sessions: async_sessionmaker[AsyncSession], pricing: PricingTable, settings: Settings
) -> BudgetGuard:
    return BudgetGuard(sessions, pricing, BudgetDefaults.from_settings(settings))


async def committed_task(
    session: AsyncSession, *, budget_limit: dict[str, object] | None = None, project: Project | None = None
) -> Task:
    project = project or await make_project(session)
    task = await make_task(session, project)
    if budget_limit is not None:
        task.budget_limit = budget_limit
    await session.commit()
    return task


def context(task: Task) -> UsageCallContext:
    return UsageCallContext(
        project_id=task.project_id,
        call_id=uuid.uuid4(),
        input_token_bound=BOUND,
        output_token_cap=CAP,
        task_id=task.id,
    )


def attempt(n: int = 1, **overrides: object) -> AttemptRecord:
    values: dict[str, object] = {
        "provider": "scripted",
        "model": HAIKU,
        "attempt": n,
        "request_id": None,
        "usage": Usage(input_tokens=100, output_tokens=20),
        "duration_ms": 1,
        "status": "ok",
        "error_kind": None,
    }
    values.update(overrides)
    return AttemptRecord(**values)  # type: ignore[arg-type]


async def test_guard_reads_task_project_and_env_levels(session: AsyncSession, guard: BudgetGuard) -> None:
    project = await make_project(session)
    project.settings = {
        "budget": {
            "max_cost_usd": "0.5",
            "max_tokens": 9_999,
            "max_tool_iterations": 4,
            "max_duration_minutes": None,
        }
    }
    task = await committed_task(session, budget_limit={"max_tokens": 1_234}, project=project)
    snapshot = await guard.check_before_attempt(task.id, CallBound(HAIKU, 10, 10))
    # tokens from the task, cost and tool iterations from the project, duration from the env default.
    assert snapshot.budget == EffectiveBudget(
        max_cost_usd=Decimal("0.5"), max_tokens=1_234, max_duration_minutes=60, max_tool_iterations=4
    )
    assert (snapshot.usage.tokens, snapshot.usage.cost, snapshot.usage.tool_iterations) == (0, Decimal(0), 0)
    assert snapshot.usage.elapsed is None  # never started: duration is not checked (Keputusan Lead #11)


async def test_guard_counts_only_this_tasks_ledger(
    session: AsyncSession, guard: BudgetGuard, ledger: UsageLedger
) -> None:
    project = await make_project(session)
    task = await committed_task(session, project=project)
    other = await committed_task(session, project=project)
    await ledger.record(context(task), attempt())
    await ledger.record(context(other), attempt(usage=Usage(input_tokens=5_000, output_tokens=5_000)))
    await ledger.record(
        UsageCallContext(
            project_id=project.id, call_id=uuid.uuid4(), input_token_bound=1, output_token_cap=1
        ),
        attempt(usage=Usage(input_tokens=7_000, output_tokens=7_000)),  # project-level, no task
    )
    snapshot = await guard.check_before_attempt(task.id, NEXT)
    assert snapshot.usage.tokens == 120
    assert snapshot.usage.cost == Decimal("0.00002")  # 100 x 0.10 + 20 x 0.50 per million


async def test_tool_iterations_count_calls_that_reached_a_handler(
    session: AsyncSession, guard: BudgetGuard
) -> None:
    task = await committed_task(session, budget_limit={"max_tool_iterations": 5})
    for status in ("running", "ok", "error", "denied", "timeout", "approval_required", "cancelled"):
        session.add(call(task, status=status))
    await session.commit()
    snapshot = await guard.check_before_attempt(task.id, NEXT)
    assert snapshot.usage.tool_iterations == 5  # denied and approval_required never reached a handler
    await session.execute(
        update(Task).where(Task.id == task.id).values(budget_limit={"max_tool_iterations": 4})
    )
    await session.commit()
    with pytest.raises(BudgetExceeded) as raised:
        await guard.check_before_attempt(task.id, NEXT)
    assert (raised.value.kind, raised.value.limit, raised.value.used) == ("tool_iterations", 4, 5)


async def test_guard_checks_a_tool_against_the_task_tool_calls(
    session: AsyncSession, guard: BudgetGuard
) -> None:
    """Before a tool (Keputusan Lead #32): ``used + 1`` against the cap; denied and approval_required calls
    never reached a handler and do not count (plan WP-3.1f2a Task 5)."""
    task = await committed_task(session, budget_limit={"max_tool_iterations": 6})
    for status in ("running", "ok", "error", "denied", "timeout", "approval_required", "cancelled"):
        session.add(call(task, status=status))
    await session.commit()
    snapshot = await guard.check_before_tool(task.id)  # 5 counted: the sixth tool may run
    assert snapshot.usage.tool_iterations == 5
    await session.execute(
        update(Task).where(Task.id == task.id).values(budget_limit={"max_tool_iterations": 5})
    )
    await session.commit()
    with pytest.raises(BudgetExceeded) as raised:
        await guard.check_before_tool(task.id)
    assert (raised.value.kind, raised.value.limit, raised.value.used) == ("tool_iterations", 5, 5)
    await guard.check_before_attempt(task.id, NEXT)  # the model may still answer after the last tool


async def test_the_tool_check_refuses_a_missing_task(guard: BudgetGuard) -> None:
    with pytest.raises(BudgetTaskMissing):
        await guard.check_before_tool(uuid.uuid4())


async def test_duration_runs_from_started_at_on_the_db_clock(
    session: AsyncSession, guard: BudgetGuard
) -> None:
    task = await committed_task(session, budget_limit={"max_duration_minutes": 60})
    await session.execute(
        text("UPDATE tasks SET started_at = now() - interval '59 minutes' WHERE id = :id"), {"id": task.id}
    )
    await session.commit()
    snapshot = await guard.check_before_attempt(task.id, NEXT)
    assert snapshot.usage.elapsed is not None and snapshot.usage.elapsed.total_seconds() >= 59 * 60
    await session.execute(
        text("UPDATE tasks SET started_at = now() - interval '61 minutes' WHERE id = :id"), {"id": task.id}
    )
    await session.commit()
    with pytest.raises(BudgetExceeded) as raised:
        await guard.check_before_attempt(task.id, NEXT)
    assert (raised.value.kind, raised.value.limit) == ("duration", 60)
    assert isinstance(raised.value.used, Decimal) and raised.value.used >= 61


async def test_duration_ignores_the_application_clock(
    session: AsyncSession, guard: BudgetGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keputusan plan #5: elapsed time is now() - started_at on the database clock. A worker whose own clock
    runs ahead (or behind) must not stop a task early or keep it running late."""

    class FarFuture(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> FarFuture:
            return cls(2100, 1, 1, tzinfo=tz or UTC)

    # The guard must not consult the application's clock at all, under any of the usual names.
    monkeypatch.setattr(budget, "datetime", FarFuture, raising=False)
    monkeypatch.setattr(budget, "time", None, raising=False)
    task = await committed_task(session, budget_limit={"max_duration_minutes": 60})
    await session.execute(
        text("UPDATE tasks SET started_at = now() - interval '1 minute' WHERE id = :id"), {"id": task.id}
    )
    await session.commit()
    snapshot = await guard.check_before_attempt(task.id, NEXT)
    assert snapshot.usage.elapsed is not None
    assert timedelta(minutes=1) <= snapshot.usage.elapsed < timedelta(minutes=10)


@pytest.mark.parametrize(
    "settings_value",
    [
        {"budget": {"max_cost_usd": 2.5}},  # a JSON float
        {"budget": {"max_tokens": "many"}},
        {"budget": ["max_tokens"]},
    ],
)
async def test_malformed_project_budget_fails_closed(
    session: AsyncSession, guard: BudgetGuard, settings_value: dict[str, object]
) -> None:
    project = await make_project(session)
    project.settings = settings_value
    task = await committed_task(session, project=project)
    with pytest.raises(BudgetConfigError):
        await guard.check_before_attempt(task.id, NEXT)


async def test_malformed_task_budget_fails_closed(session: AsyncSession, guard: BudgetGuard) -> None:
    task = await committed_task(session, budget_limit={"max_cost_usd": 0.5})
    with pytest.raises(BudgetConfigError):
        await guard.check_before_attempt(task.id, NEXT)


async def test_missing_task_is_refused(guard: BudgetGuard) -> None:
    with pytest.raises(BudgetTaskMissing):
        await guard.check_before_attempt(uuid.uuid4(), NEXT)


# --- end to end through create_with_retry (AG-13 3.1a M2) -------------------------------------------------


async def test_retry_storm_stops_at_the_cap(
    session: AsyncSession, guard: BudgetGuard, ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession]
) -> None:
    task = await committed_task(session, budget_limit={"max_tokens": 2 * (BOUND + CAP)})
    provider = ScriptedProvider(record_requests=True)
    with pytest.raises(BudgetExceeded) as raised:
        await create_with_retry(
            provider,
            request("faults", "transient"),  # fails every time, without usage
            before_attempt=guard.before_attempt(task.id, NEXT),
            on_attempt=ledger.recorder(context(task)),
            sleep=no_sleep,
        )
    assert raised.value.kind == "tokens"
    assert (raised.value.used, raised.value.next_bound) == (2 * (BOUND + CAP), BOUND + CAP)
    assert len(provider.requests) == 2
    async with sessions() as s:
        assert (
            await s.scalar(text("SELECT count(*) FROM usage_records WHERE task_id = :id"), {"id": task.id})
            == 2
        )


async def test_stalled_stream_counts_the_output_cap(
    session: AsyncSession, guard: BudgetGuard, ledger: UsageLedger
) -> None:
    task = await committed_task(session)
    token = CancelToken()

    async def cancel_after_first(_: str) -> None:
        token.cancel()

    with pytest.raises(LLMError):
        await create_with_retry(
            ScriptedProvider(),
            request("faults", "slow_stream"),
            on_text_delta=cancel_after_first,
            cancel=token,
            before_attempt=guard.before_attempt(task.id, NEXT),
            on_attempt=ledger.recorder(context(task)),
        )
    snapshot = await guard.check_before_attempt(task.id, NEXT)
    assert snapshot.usage.tokens == 50 + CAP  # not 50 + 0: the cut stream may have billed up to the cap
    # Haiku's ceiling is its long-prompt tier: 50 x 0.50 + 8192 x 2.50 per million.
    assert snapshot.usage.cost == (50 * Decimal("0.50") + CAP * Decimal("2.50")) / 1_000_000


async def test_rate_limited_attempts_do_not_eat_the_budget(
    session: AsyncSession, guard: BudgetGuard, ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession]
) -> None:
    task = await committed_task(session, budget_limit={"max_tokens": BOUND + CAP})  # room for one attempt
    provider = ScriptedProvider(record_requests=True)
    with pytest.raises(LLMError) as raised:
        await create_with_retry(
            provider,
            request("faults", "rate_limited"),
            before_attempt=guard.before_attempt(task.id, NEXT),
            on_attempt=ledger.recorder(context(task)),
            sleep=no_sleep,
        )
    assert raised.value.kind == "rate_limited"
    assert len(provider.requests) == 5
    snapshot = await guard.check_before_attempt(task.id, NEXT)
    assert (snapshot.usage.tokens, snapshot.usage.cost) == (0, Decimal(0))


async def test_guard_sees_a_raised_task_limit_on_the_next_attempt(
    session: AsyncSession, guard: BudgetGuard, ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession]
) -> None:
    task = await committed_task(session, budget_limit={"max_tokens": BOUND + CAP})
    provider = ScriptedProvider(record_requests=True)

    async def raise_limit_while_waiting(_: float) -> None:
        async with sessions.begin() as s:
            await s.execute(
                update(Task).where(Task.id == task.id).values(budget_limit={"max_tokens": 2 * (BOUND + CAP)})
            )

    with pytest.raises(BudgetExceeded) as raised:
        await create_with_retry(
            provider,
            request("faults", "transient"),
            before_attempt=guard.before_attempt(task.id, NEXT),
            on_attempt=ledger.recorder(context(task)),
            sleep=raise_limit_while_waiting,
        )
    # Attempt 2 passed only under the raised limit, read fresh; attempt 3 is over it.
    assert len(provider.requests) == 2
    assert (raised.value.kind, raised.value.limit) == ("tokens", 2 * (BOUND + CAP))
