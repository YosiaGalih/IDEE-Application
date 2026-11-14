"""The single model-call path against a real database: BudgetGuard and the usage ledger see the same bound,
built once from the ``ModelCall`` (spec WP-3.1f Keputusan Lead #17; AG-13 3.1f1 L1; plan WP-3.1f2a Task 3)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.runtime import BudgetRefused, ModelCall, RunScope, request_size_bytes
from app.core.cancel import CancelToken
from app.core.config import Settings
from app.llm.pricing import PricingTable
from app.llm.scenarios.model import Fail
from app.models.usage import UsageRecord
from app.services.agent_runtime import GuardBudget, LedgerRecorder
from app.services.usage import UsageLedger
from app.workflow.budget import BudgetDefaults, BudgetGuard
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.runtime_fakes import Fakes, a_model_call, in_order, make_runtime, scripted, submit_turn
from tests.tool_fakes import make_run
from tests.usage_fakes import no_sleep


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


async def a_run_scope(session: AsyncSession, *, budget_limit: dict[str, object] | None = None) -> RunScope:
    project = await make_project(session)
    task = await make_task(session, project)
    if budget_limit is not None:
        task.budget_limit = budget_limit
    run_id = await make_run(session, task.id, "B14")
    await session.commit()
    return RunScope(project_id=project.id, task_id=task.id, agent_run_id=run_id, agent_id="B14")


def with_real_ledger(
    fakes: Fakes,
    sessions: async_sessionmaker[AsyncSession],
    pricing: PricingTable,
    settings: Settings,
) -> Fakes:
    real_budget = GuardBudget(BudgetGuard(sessions, pricing, BudgetDefaults.from_settings(settings)))
    real_ledger = LedgerRecorder(UsageLedger(sessions, pricing))
    fakes.budget = real_budget  # type: ignore[assignment]
    fakes.ledger = real_ledger  # type: ignore[assignment]
    return fakes


async def rows(session: AsyncSession, call: ModelCall) -> list[UsageRecord]:
    found = await session.scalars(
        select(UsageRecord).where(UsageRecord.call_id == call.call_id).order_by(UsageRecord.attempt)
    )
    return list(found)


async def test_each_attempt_leaves_one_ledger_row_with_the_call_bounds(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    pricing: PricingTable,
    settings: Settings,
) -> None:
    scope = await a_run_scope(session)
    fakes = with_real_ledger(
        Fakes(scripted(in_order(Fail("overloaded"), submit_turn({"summary": "s"})))),
        sessions,
        pricing,
        settings,
    )
    call = a_model_call(scope, max_tokens=2048)
    await make_runtime(fakes, sleep=no_sleep)._call_model(
        call, fakes.providers.provider, cancel=CancelToken()
    )
    stored = await rows(session, call)
    assert [(r.attempt, r.status) for r in stored] == [(1, "error"), (2, "ok")]
    for row in stored:
        assert row.input_token_bound == request_size_bytes(call.request) == call.input_token_bound
        assert row.output_token_cap == call.request.max_tokens == 2048
        assert (row.project_id, row.task_id, row.agent_run_id, row.agent_id) == (
            scope.project_id,
            scope.task_id,
            scope.agent_run_id,
            scope.agent_id,
        )


@pytest.mark.parametrize("slack", [0, -1], ids=["exactly-enough", "one-short"])
async def test_the_budget_sees_the_same_bound_the_ledger_records(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    pricing: PricingTable,
    settings: Settings,
    slack: int,
) -> None:
    probe = a_model_call(max_tokens=1024)
    cap = probe.input_token_bound + probe.output_token_cap + slack
    scope = await a_run_scope(session, budget_limit={"max_tokens": cap})
    call = a_model_call(scope, max_tokens=1024)
    assert call.input_token_bound == probe.input_token_bound  # same request shape, same bound
    fakes = with_real_ledger(Fakes(scripted(submit_turn({"summary": "s"}))), sessions, pricing, settings)
    runtime = make_runtime(fakes)
    if slack == 0:
        await runtime._call_model(call, fakes.providers.provider, cancel=CancelToken())
        assert len(await rows(session, call)) == 1
        return
    with pytest.raises(BudgetRefused) as exc:
        await runtime._call_model(call, fakes.providers.provider, cancel=CancelToken())
    assert (exc.value.kind, exc.value.limit, exc.value.used) == ("tokens", cap, 0)
    assert exc.value.next_bound == call.input_token_bound + call.output_token_cap
    assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 0
    assert fakes.provider.requests == []
