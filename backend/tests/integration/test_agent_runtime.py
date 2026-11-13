"""The AgentRuntime composed over a real database (plan WP-3.1f2a Task 5; spec WP-3.1f Keputusan Lead
#15-#23, #33; ADR-070).

Real provider gate, budget guard, usage ledger, lease heartbeat, run store and tool gateway; the provider is
scripted.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.llm.pricing import PricingTable
from app.models.agents import AgentRun
from app.models.approvals import Approval
from app.models.audit import AuditEvent
from app.models.tools import ToolCall
from app.models.usage import UsageRecord
from app.services.tool_gateway import build_tool_gateway
from app.tools.base import ToolContext, ToolOutput
from app.workspace.fs import TaskWorkspace
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.runtime_fakes import (
    OUTPUTS,
    REGISTRY,
    Composition,
    RecordingBus,
    compose,
    hold_lease,
    registry_with,
    run_request,
    scripted,
    submit_turn,
    tool_turn,
)
from tests.security.test_runtime_gate import record, worker
from tests.tool_fakes import AUDIT, Recorder, gateway, spec
from tests.worktree_fakes import task_worktree

DONE = submit_turn(OUTPUTS["ImplementOutput"])


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def settings() -> Settings:
    return worker()


async def a_task(
    session: AsyncSession, settings: Settings, *, budget_limit: dict[str, Any] | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    await record(session, settings)
    project = await make_project(session)
    task = await make_task(session, project)
    if budget_limit is not None:
        task.budget_limit = budget_limit
    await session.commit()
    await hold_lease(session, task.id)
    return project.id, task.id


async def count(session: AsyncSession, model: Any, **where: Any) -> int:
    query = select(func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return await session.scalar(query) or 0


async def test_a_run_reads_a_file_and_succeeds(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
    tmp_path: Path,
) -> None:
    project_id, task_id = await a_task(session, settings)
    workspace = await task_worktree(tmp_path, {"src/calc.py": "def add(a, b):\n    return a + b\n"})
    tools = build_tool_gateway(sessions=sessions, audit=AUDIT)
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(tool_turn("repo.read_file", {"path": "src/calc.py"}), DONE),
        tools=tools,
    )
    result = await run.runtime.run(
        run_request("B14", task_id=task_id, project_id=project_id, workspace=workspace)
    )
    assert result.status == "succeeded", result
    stored = await session.get(AgentRun, result.agent_run_id)
    assert stored is not None
    assert (stored.status, stored.iterations, stored.error_code) == ("succeeded", 2, None)
    assert await count(session, UsageRecord, agent_run_id=result.agent_run_id) == 2
    assert await count(session, UsageRecord, task_id=task_id) == 2
    calls = list(await session.scalars(select(ToolCall).where(ToolCall.task_id == task_id)))
    assert [(c.tool_name, c.status, c.agent_run_id) for c in calls] == [
        ("repo.read_file", "ok", result.agent_run_id)
    ]
    sent = run.provider.requests[1].messages[-1].content
    assert "def add" in str(sent)  # the tool result reached the model


def _tool_registry(tool: str) -> Any:
    b14 = REGISTRY.by_id("B14")
    assert b14 is not None
    return registry_with("B14", tools=(*b14.tools, tool))


async def test_a_lost_lease_ends_the_run_lease_lost(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    pricing: PricingTable,
) -> None:
    project_id, task_id = await a_task(session, settings)

    async def steal(ctx: ToolContext, validated: Any) -> ToolOutput:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE tasks SET lease_owner = 'thief' WHERE id = :id"), {"id": task_id})
        return ToolOutput("stolen")

    run = compose(
        sessions,
        settings,
        pricing,
        scripted(tool_turn("test.read", {"path": "README.md"}), DONE),
        tools=gateway(sessions, spec("test.read", steal)),
        registry=_tool_registry("test.read"),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert (result.status, result.error_code) == ("failed", "lease_lost")
    assert len(run.provider.requests) == 1
    stored = await session.get(AgentRun, result.agent_run_id)
    assert stored is not None and (stored.status, stored.error_code) == ("failed", "lease_lost")


async def test_no_task_row_lock_is_held_while_a_tool_runs(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    pricing: PricingTable,
) -> None:
    project_id, task_id = await a_task(session, settings)
    updated: list[int] = []

    async def touch_the_task(ctx: ToolContext, validated: Any) -> ToolOutput:
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '1s'"))
            result = await conn.execute(
                text("UPDATE tasks SET event_seq = event_seq WHERE id = :id"), {"id": task_id}
            )
            updated.append(result.rowcount)
        return ToolOutput("touched")

    run = compose(
        sessions,
        settings,
        pricing,
        scripted(tool_turn("test.read", {"path": "README.md"}), DONE),
        tools=gateway(sessions, spec("test.read", touch_the_task)),
        registry=_tool_registry("test.read"),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert result.status == "succeeded"
    assert updated == [1]


async def test_approval_required_from_a_test_r3_tool(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
) -> None:
    project_id, task_id = await a_task(session, settings)
    handler = Recorder()
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(tool_turn("test.r3", {"target": "prod"}), DONE),
        tools=gateway(sessions, spec("test.r3", handler)),
        registry=_tool_registry("test.r3"),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert (result.status, result.error_code) == ("approval_required", None)
    assert result.pending_tool is not None and result.pending_tool.tool_name == "test.r3"
    approvals = list(await session.scalars(select(Approval).where(Approval.task_id == task_id)))
    assert [a.status for a in approvals] == ["pending"]
    assert handler.calls == []  # nothing ran without a person's decision
    assert len(run.provider.requests) == 1  # no model call after the stop
    stored = await session.get(AgentRun, result.agent_run_id)
    assert stored is not None and stored.output == {
        "pending_tool": {
            "tool_use_id": "toolu_1",
            "tool_name": "test.r3",
            "tool_call_id": str(result.pending_tool.tool_call_id),
        }
    }


async def test_a_tool_budget_refusal_is_audited(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
) -> None:
    """The refusal ends the run ``budget_exceeded`` before the second tool, with the event
    ``usage.budget_exceeded`` and the audit ``budget.exceeded`` written by the run store."""
    from app.llm.base import ToolUseBlock
    from app.llm.scenarios.model import Reply

    project_id, task_id = await a_task(session, settings, budget_limit={"max_tool_iterations": 1})
    two = Reply(
        (
            ToolUseBlock("toolu_a", "test.read", {"path": "a"}),
            ToolUseBlock("toolu_b", "test.read", {"path": "b"}),
        ),
        stop_reason="tool_use",
    )
    handler = Recorder()
    bus = RecordingBus()
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(two, DONE),
        bus=bus,
        tools=gateway(sessions, spec("test.read", handler)),
        registry=_tool_registry("test.read"),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert (result.status, result.error_code) == ("budget_exceeded", None)
    assert result.budget is not None
    assert (result.budget.kind, result.budget.limit, result.budget.used) == ("tool_iterations", 1, 1)
    assert await count(session, ToolCall, task_id=task_id) == 1
    assert len(handler.calls) == 1
    audits = list(
        await session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "budget.exceeded", AuditEvent.resource_id == str(result.agent_run_id)
            )
        )
    )
    assert len(audits) == 1
    assert (audits[0].actor_type, audits[0].actor_id, audits[0].result, audits[0].project_id) == (
        "agent",
        "B14",
        "denied",
        project_id,
    )
    assert audits[0].metadata_ == {
        "task_id": str(task_id),
        "kind": "tool_iterations",
        "limit": 1,
        "used": 1,
        "next_bound": 0,
    }
    (event,) = bus.of_type("usage.budget_exceeded")
    assert event["payload"] == {"kind": "tool_iterations", "limit": 1, "used": 1}
    stored = await session.get(AgentRun, result.agent_run_id)
    assert stored is not None and (stored.status, stored.error_code) == ("budget_exceeded", None)


async def test_the_workspace_reaches_the_tools(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
    tmp_path: Path,
) -> None:
    project_id, task_id = await a_task(session, settings)
    workspace = await task_worktree(tmp_path, {})
    seen: list[TaskWorkspace | None] = []

    async def look(ctx: ToolContext, validated: Any) -> ToolOutput:
        seen.append(ctx.workspace)
        return ToolOutput("ok")

    run: Composition = compose(
        sessions,
        settings,
        pricing,
        scripted(tool_turn("test.read", {}), DONE),
        tools=gateway(sessions, spec("test.read", look)),
        registry=_tool_registry("test.read"),
    )
    await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id, workspace=workspace))
    assert seen == [workspace]


async def test_build_agent_runtime_assembles_the_production_ports(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
) -> None:
    from app.core.audit import AuditWriter
    from app.core.redaction import Redactor
    from app.services.agent_runtime import build_agent_runtime
    from tests.runtime_fakes import WORKER_ID, FakeProviders, FakeTools

    project_id, task_id = await a_task(session, settings)
    bus = RecordingBus()
    runtime = build_agent_runtime(
        sessions=sessions,
        settings=settings,
        registry=REGISTRY,
        pricing=pricing,
        audit=AuditWriter(Redactor(settings.secret_values())),
        bus=bus,
        worker_id=WORKER_ID,
        tools=FakeTools([]),
        providers=FakeProviders(scripted(DONE)),
    )
    result = await runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert result.status == "succeeded"
    assert [m["type"] for m in bus.messages if m["seq"] is not None] == [
        "agent.run_started",
        "usage.updated",
        "agent.run_finished",
    ]
    stored = await session.get(AgentRun, result.agent_run_id)
    assert stored is not None and stored.status == "succeeded" and stored.input_tokens == 100


class ClosingProvider:
    """A scripted provider that may answer slowly and counts its ``aclose``."""

    name = "scripted"

    def __init__(self, *turns: Any, slow: float = 0.0) -> None:
        from tests.runtime_fakes import SlowProvider

        self.inner = scripted(*turns)
        self.requests = self.inner.requests
        self.answer: Any = SlowProvider(self.inner, slow) if slow else self.inner
        self.closed = 0

    async def create_message(self, req: Any, *, on_text_delta: Any = None, cancel: Any = None) -> Any:
        return await self.answer.create_message(req, on_text_delta=on_text_delta, cancel=cancel)

    async def aclose(self) -> None:
        self.closed += 1


@pytest.mark.parametrize(
    "ending", ["succeeded", "model_error", "caller_cancel", "asyncio_cancel", "gate_after_open"]
)
async def test_the_provider_is_closed_after_every_run(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
    ending: str,
) -> None:
    """One provider per run, built by the factory and closed after it, whatever the ending (Keputusan Lead
    #28)."""
    import asyncio

    from app.agents.runtime import ProviderBlocked
    from app.core.cancel import CancelToken
    from app.llm.scenarios.model import Fail
    from app.services.agent_runtime import SettingsProviderFactory
    from tests.runtime_fakes import FakeGate, FakeTools

    project_id, task_id = await a_task(session, settings)
    slow = 5.0 if ending in {"caller_cancel", "asyncio_cancel"} else 0.0
    built: list[ClosingProvider] = []

    def build(_: Settings) -> Any:
        built.append(ClosingProvider(Fail("refusal") if ending == "model_error" else DONE, slow=slow))
        return built[-1]

    gate = None
    if ending == "gate_after_open":
        gate = FakeGate([], refuse={2: ProviderBlocked("PROVIDER_DISABLED", "kill_switch")})
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(DONE),
        tools=FakeTools([]),
        gate=gate,
        providers=SettingsProviderFactory(settings, build=build),
    )
    cancel = CancelToken()
    task = asyncio.create_task(
        run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id, cancel=cancel))
    )
    if ending == "caller_cancel":
        await asyncio.sleep(0.2)
        cancel.cancel()
    if ending == "asyncio_cancel":
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await asyncio.wait_for(task, 5)
        expected = {
            "succeeded": ("succeeded", None),
            "model_error": ("failed", "model_error"),
            "caller_cancel": ("cancelled", None),
            "gate_after_open": ("failed", "PROVIDER_DISABLED"),
        }[ending]
        assert (result.status, result.error_code) == expected
    assert len(built) == 1 and built[0].closed == 1


async def test_a_worker_without_a_key_ends_provider_not_ready(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable
) -> None:
    """``LLM_PROVIDER=anthropic`` without a key and without required readiness: the gate passes
    (``readiness_not_required``), the factory cannot build a provider, the run ends ``provider_not_ready``
    with no request. With readiness required, the gate refuses first (``key_missing``, Task 4)."""
    from app.services.agent_runtime import SettingsProviderFactory
    from tests.runtime_fakes import FakeTools

    settings = worker(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="", LLM_REQUIRE_READINESS="false")
    project_id, task_id = await a_task(session, settings)
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(DONE),
        tools=FakeTools([]),
        providers=SettingsProviderFactory(settings),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert (result.status, result.error_code) == ("failed", "provider_not_ready")
    assert result.error is not None and "no_provider" in result.error
    stored = await session.get(AgentRun, result.agent_run_id)
    assert stored is not None and (stored.model_provider, stored.model_name) == (None, None)


DISPATCHED = ["A02", "A03", "B14", "C20", "D22", "A07", "B09", "B10", "B13", "G35", "C21"]


@pytest.mark.parametrize("agent_id", DISPATCHED)
async def test_every_runtime_and_profile_succeeds_on_generic(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
    agent_id: str,
) -> None:
    from app.agents.contracts import OUTPUT_CONTRACTS
    from app.llm.scripted_provider import ScriptedProvider
    from tests.runtime_fakes import FakeTools

    spec = REGISTRY.by_id(agent_id)
    assert spec is not None
    runtime_id = agent_id if spec.mvp_mode == "llm" else spec.runtime_agent
    assert runtime_id is not None
    project_id, task_id = await a_task(session, settings)
    provider = ScriptedProvider(record_requests=True)  # the shipped scenarios, generic by default
    run = compose(sessions, settings, pricing, provider, tools=FakeTools([]))
    result = await run.runtime.run(
        run_request(agent_id, runtime_id=runtime_id, task_id=task_id, project_id=project_id)
    )
    assert (result.status, result.error_code) == ("succeeded", None), result.error
    assert type(result.output) is OUTPUT_CONTRACTS[runtime_id]


async def test_the_scenario_argument_picks_the_scripted_scenario(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
) -> None:
    from app.llm.scenarios import SCENARIOS
    from app.llm.scenarios.model import Scenario
    from app.llm.scripted_provider import ScriptedProvider
    from tests.runtime_fakes import FakeTools

    project_id, task_id = await a_task(session, settings)
    test_one = Scenario(
        name="test_one", default=(submit_turn({**OUTPUTS["ImplementOutput"], "summary": "From test_one."}),)
    )
    provider = ScriptedProvider({**SCENARIOS, "test_one": test_one}, record_requests=True)
    run = compose(sessions, settings, pricing, provider, tools=FakeTools([]))
    result = await run.runtime.run(
        run_request("B14", task_id=task_id, project_id=project_id, scenario="test_one")
    )
    assert result.status == "succeeded"
    assert result.output is not None and result.output.model_dump()["summary"] == "From test_one."
    assert provider.requests[0].metadata["scenario"] == "test_one"


async def test_no_scenario_means_generic(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
) -> None:
    from app.llm.scripted_provider import ScriptedProvider
    from tests.runtime_fakes import FakeTools

    project_id, task_id = await a_task(session, settings)
    provider = ScriptedProvider(record_requests=True)
    run = compose(sessions, settings, pricing, provider, tools=FakeTools([]))
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert result.status == "succeeded"
    assert "scenario" not in provider.requests[0].metadata
    assert provider.requests[0].metadata["output_contract"] == "ImplementOutput"


async def test_a_scenario_never_reaches_a_provider_that_is_not_scripted() -> None:
    """W65: the scenario goes into the metadata only for the scripted provider, even when the runtime is
    configured scripted (a test double named otherwise stands for the adapter)."""
    from tests.runtime_fakes import Fakes, make_runtime
    from tests.runtime_fakes import run_request as unit_request

    fakes = Fakes(scripted(DONE))
    fakes.providers.provider.name = "anthropic"  # type: ignore[misc]
    await make_runtime(fakes).run(unit_request("B14", scenario="calc"))
    assert "scenario" not in fakes.provider.requests[0].metadata
