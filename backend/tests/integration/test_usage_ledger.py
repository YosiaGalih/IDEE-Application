"""The usage ledger writer through the retry loop (FR-133; spec WP-3.1f Keputusan Lead #3, #4, #8; plan 3.1f1
Task 2): one row per attempt, partial or unavailable when cut short, idempotent per ``(call_id, attempt)``,
its own transaction, and a failure that fails the call."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.cancel import CancelToken
from app.core.db import make_sessionmaker
from app.llm.base import LLMError, LLMRequest, Message, TextBlock, Usage
from app.llm.pricing import PricingTable, load_pricing
from app.llm.retry import AttemptRecord, create_with_retry
from app.llm.scenarios import SCENARIOS
from app.llm.scenarios.model import Reply, Scenario
from app.llm.scripted_provider import ScriptedProvider
from app.models.projects import Project
from app.models.tasks import Task
from app.models.usage import UsageRecord
from app.services.usage import (
    UsageCallContext,
    UsageContextError,
    UsageLedger,
    UsageRecordConflict,
)
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.tool_fakes import make_run

BACKEND = Path(__file__).resolve().parents[2]
HAIKU = "claude-haiku-5-5"
CAP = 8_192


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def pricing() -> PricingTable:
    return load_pricing(BACKEND / "config" / "pricing.yaml")


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(engine)


@pytest.fixture
def ledger(sessions: async_sessionmaker[AsyncSession], pricing: PricingTable) -> UsageLedger:
    return UsageLedger(sessions, pricing)


@pytest.fixture
async def project_id(session: AsyncSession) -> AsyncIterator[uuid.UUID]:
    project = await make_project(session)
    await session.commit()
    yield project.id


def request(scenario: str, agent_id: str, model: str = HAIKU) -> LLMRequest:
    return LLMRequest(
        model=model,
        system="s",
        messages=(Message("user", "hi"),),
        max_tokens=CAP,
        metadata={"scenario": scenario, "agent_id": agent_id},
    )


def context(project_id: uuid.UUID, **overrides: object) -> UsageCallContext:
    values: dict[str, object] = {
        "project_id": project_id,
        "call_id": uuid.uuid4(),
        "input_token_bound": 1_000,
        "output_token_cap": CAP,
    }
    values.update(overrides)
    return UsageCallContext(**values)  # type: ignore[arg-type]


async def no_sleep(_: float) -> None:
    return None


async def rows(sessions: async_sessionmaker[AsyncSession]) -> list[UsageRecord]:
    async with sessions() as s:
        return list((await s.scalars(select(UsageRecord).order_by(UsageRecord.attempt))).all())


async def test_one_row_per_attempt_through_retry(
    ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID
) -> None:
    ctx = context(project_id)
    with pytest.raises(LLMError) as raised:
        await create_with_retry(
            ScriptedProvider(),
            request("faults", "rate_limited"),
            on_attempt=ledger.recorder(ctx),
            sleep=no_sleep,
            rng=lambda: 0.5,
        )
    assert raised.value.kind == "rate_limited"
    stored = await rows(sessions)
    assert [(r.call_id, r.attempt, r.retry_count) for r in stored] == [
        (ctx.call_id, n, n - 1) for n in range(1, 6)
    ]
    assert {(r.status, r.error_kind, r.cost_status, r.estimated_cost) for r in stored} == {
        ("error", "rate_limited", "unavailable", None)
    }
    assert {r.request_id for r in stored} == {"scripted-faults-0"}
    assert {(r.input_token_bound, r.output_token_cap, r.project_id) for r in stored} == {
        (1_000, CAP, project_id)
    }


async def test_cancelled_stream_row_is_partial(
    ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID
) -> None:
    token = CancelToken()

    async def cancel_after_first(_: str) -> None:
        token.cancel()

    with pytest.raises(LLMError) as raised:
        await create_with_retry(
            ScriptedProvider(),
            request("faults", "slow_stream"),
            on_text_delta=cancel_after_first,
            cancel=token,
            on_attempt=ledger.recorder(context(project_id)),
            sleep=no_sleep,
        )
    assert raised.value.kind == "cancelled"
    [row] = await rows(sessions)
    assert (row.status, row.error_kind) == ("cancelled", "cancelled")
    assert (row.input_tokens, row.output_tokens) == (50, 0)
    assert row.cost_status == "partial"
    assert row.estimated_cost == Decimal("0.000005")  # 50 x 0.10 per million: a lower bound


async def test_outer_cancel_row_is_unavailable(
    ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID
) -> None:
    entered = asyncio.Event()

    async def hang(_: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    caller = asyncio.create_task(
        create_with_retry(
            ScriptedProvider(),
            request("faults", "slow_stream"),
            on_text_delta=hang,
            on_attempt=ledger.recorder(context(project_id)),
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    [row] = await rows(sessions)
    assert (row.status, row.error_kind) == ("cancelled", "cancelled")
    assert row.input_tokens is None
    assert (row.cost_status, row.estimated_cost) == ("unavailable", None)


async def test_unknown_model_row_is_unavailable(
    ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID
) -> None:
    await create_with_retry(
        ScriptedProvider(),
        request("generic", "A02", model="claude-unpriced"),
        on_attempt=ledger.recorder(context(project_id)),
    )
    [row] = await rows(sessions)
    assert (row.status, row.model) == ("ok", "claude-unpriced")
    assert (row.input_tokens, row.output_tokens) == (100, 20)
    assert (row.cost_status, row.estimated_cost) == ("unavailable", None)


async def test_tiny_cost_is_stored_not_zero(
    ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID
) -> None:
    tiny = Scenario(
        name="tiny", default=(Reply((TextBlock("."),), usage=Usage(input_tokens=0, output_tokens=1)),)
    )
    await create_with_retry(
        ScriptedProvider({**SCENARIOS, "tiny": tiny}),
        request("tiny", "A02"),
        on_attempt=ledger.recorder(context(project_id)),
    )
    [row] = await rows(sessions)
    assert row.estimated_cost == Decimal("0.0000005000")
    assert str(row.estimated_cost) == "5.000E-7"  # numeric(20,10) as stored, not 0.000001 nor 0
    assert row.cost_status == "estimated"


def ok_attempt(**overrides: object) -> AttemptRecord:
    values: dict[str, object] = {
        "provider": "scripted",
        "model": HAIKU,
        "attempt": 1,
        "request_id": "req_1",
        "usage": Usage(input_tokens=10, output_tokens=2),
        "duration_ms": 5,
        "status": "ok",
        "error_kind": None,
    }
    values.update(overrides)
    return AttemptRecord(**values)  # type: ignore[arg-type]


async def test_rewriting_an_attempt_keeps_one_row(
    ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID
) -> None:
    ctx = context(project_id)
    first = await ledger.record(ctx, ok_attempt())
    again = await ledger.record(ctx, ok_attempt())
    assert first == again
    assert [r.id for r in await rows(sessions)] == [first]


async def test_conflicting_rewrite_raises(
    ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID
) -> None:
    ctx = context(project_id)
    first = await ledger.record(ctx, ok_attempt())
    with pytest.raises(UsageRecordConflict):
        await ledger.record(ctx, ok_attempt(usage=Usage(input_tokens=10, output_tokens=3)))
    [row] = await rows(sessions)
    assert (row.id, row.output_tokens) == (first, 2)


async def test_a_rewrite_for_another_project_raises(
    ledger: UsageLedger, session: AsyncSession, project_id: uuid.UUID
) -> None:
    other = await make_project(session, slug="other")
    await session.commit()
    call_id = uuid.uuid4()
    await ledger.record(context(project_id, call_id=call_id), ok_attempt())
    with pytest.raises(UsageRecordConflict):
        await ledger.record(context(other.id, call_id=call_id), ok_attempt())


async def test_row_survives_the_callers_rollback(
    ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID
) -> None:
    async with sessions() as caller:
        await caller.begin()
        await make_task(caller, await caller.get_one(Project, project_id))  # the caller's own work
        await create_with_retry(
            ScriptedProvider(), request("generic", "A02"), on_attempt=ledger.recorder(context(project_id))
        )
        await caller.rollback()
    async with sessions() as check:
        assert await check.scalar(select(Task.id)) is None  # the caller's work is gone
    assert len(await rows(sessions)) == 1  # the ledger row is not


@pytest.mark.parametrize(("scenario", "agent_id"), [("generic", "A02"), ("faults", "transient")])
async def test_a_ledger_failure_fails_the_call(
    ledger: UsageLedger, sessions: async_sessionmaker[AsyncSession], scenario: str, agent_id: str
) -> None:
    provider = ScriptedProvider(record_requests=True)
    returned: list[object] = []
    with pytest.raises(IntegrityError, match="fk_usage_records_project_id_projects"):
        returned.append(
            await create_with_retry(
                provider,
                request(scenario, agent_id),
                on_attempt=ledger.recorder(context(uuid.uuid4())),  # no such project
                sleep=no_sleep,
            )
        )
    assert len(provider.requests) == 1  # a failed write is not retried, for an ok or a failed attempt
    assert returned == []  # a paid answer that was not recorded never reaches the caller
    assert await rows(sessions) == []


async def test_writer_refuses_a_task_from_another_project(
    ledger: UsageLedger,
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    other = await make_project(session, slug="other")
    foreign_task = await make_task(session, other)
    await session.commit()
    with pytest.raises(UsageContextError):
        await ledger.record(context(project_id, task_id=foreign_task.id), ok_attempt())
    with pytest.raises(UsageContextError):
        await ledger.record(context(project_id, task_id=uuid.uuid4()), ok_attempt())  # no such task
    assert await rows(sessions) == []


async def test_writer_refuses_a_run_from_another_task(
    ledger: UsageLedger,
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    project = await session.get_one(Project, project_id)
    task = await make_task(session, project)
    other_task = await make_task(session, project)
    foreign_run = await make_run(session, other_task.id)
    await session.commit()
    with pytest.raises(UsageContextError):
        await ledger.record(
            context(project_id, task_id=task.id, agent_run_id=foreign_run, agent_id="B14"), ok_attempt()
        )
    with pytest.raises(UsageContextError):
        await ledger.record(
            context(project_id, task_id=task.id, agent_run_id=uuid.uuid4(), agent_id="B14"), ok_attempt()
        )
    assert await rows(sessions) == []


async def test_writer_accepts_its_own_task_and_run(
    ledger: UsageLedger,
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    project = await session.get_one(Project, project_id)
    task = await make_task(session, project)
    run = await make_run(session, task.id)
    await session.commit()
    await ledger.record(context(project_id, task_id=task.id, agent_run_id=run, agent_id="B14"), ok_attempt())
    [row] = await rows(sessions)
    assert (row.project_id, row.task_id, row.agent_run_id, row.agent_id) == (project_id, task.id, run, "B14")
