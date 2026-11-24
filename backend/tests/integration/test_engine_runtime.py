# ruff: noqa: F811 - make_task, the fixture of test_engine, is imported by name and used as an argument
"""The workflow engine drives the real agent runtime (WP-3.2c2c2 Task 5; spec §16.6.5).

``build_orchestrator`` assembles the real ``AgentRuntime`` over the database; the provider is the production
scripted one (scenario ``generic``). Every status is read from the database (tasks, agent_runs, task_events),
never from model text. C2: the runtime renews the lease under the owner of the engine's claim. The proof is
the pair: these runs succeed, and the same wiring with another runtime owner ends ``lease_lost``
(``test_a_runtime_with_another_owner_loses_the_lease``); the renewal count alone cannot tell the engine's
renewals from the runtime's, since both write the same statement.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.llm.base import ToolUseBlock
from app.llm.pricing import PricingTable
from app.llm.scenarios.model import Fail, Reply
from app.services import agent_runtime as agent_runtime_module
from app.services.agent_runtime import build_agent_runtime
from app.services.agent_step import AgentStep, DbAgentStepReads, bind_agent_steps
from app.services.projects import KILL_SWITCH_KEY
from app.workflow.engine import EngineOutcome, StepContext, TaskEngine, WorkflowPorts
from app.workflow.lease import ClaimResult, claim_lease
from app.workflow.run_outcomes import FAIL_MESSAGES
from tests.engine_runtime_fakes import (
    OWNER,
    Renewals,
    StreamThenFail,
    implement_input,
    intake_input,
    orchestrate,
    plan_input,
    raw_done,
    ready_world,
    to_planned,
    to_validation_unavailable,
)
from tests.integration.test_engine import (
    changed,
    finished,
    kinds,
    make_task,  # noqa: F401 - a fixture, used by name as an argument
    row,
    started,
)
from tests.runtime_fakes import (
    REGISTRY,
    FakeProviders,
    RecordingBus,
    SlowProvider,
    agent_input,
    registry_with,
    scripted,
    text_turn,
    tool_turn,
)
from tests.security.test_runtime_gate import worker
from tests.tool_fakes import Recorder, gateway, spec
from tests.usage_fakes import PRICING

PLANNING_STEPS = {
    "intake": AgentStep("A02", intake_input),
    "plan": AgentStep("A03", plan_input, on_success=to_planned),
}


async def runs(engine: AsyncEngine, task_id: uuid.UUID) -> list[Any]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT id, agent_id, step, status, error_code FROM agent_runs WHERE task_id = :t "
                "ORDER BY started_at, id"
            ),
            {"t": task_id},
        )
        return list(result)


@pytest.fixture
def pricing() -> PricingTable:
    return PRICING


def lease_free(after: Any) -> bool:
    return (after.lease_owner, after.lease_expires_at) == (None, None)


async def test_planning_runs_two_agents_through_the_real_runtime(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    settings = worker()
    task_id = await make_task("draft", {"user_set": ["title"]})
    await ready_world(engine, settings)
    bus = RecordingBus(engine)
    orch = orchestrate(
        engine, settings, pricing, agent_steps=PLANNING_STEPS, steps={"context": raw_done}, bus=bus
    )

    with Renewals(engine) as renewals:
        outcome = await orch.run(task_id)

    assert renewals.calls  # the listener sees renewals (engine's and runtime's alike; C2: see the docstring)
    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake", "context", "plan"], "transitioned")
    after = await row(engine, task_id)
    assert after.status == "planned"
    assert lease_free(after)
    stored = await runs(engine, task_id)
    assert [(r.agent_id, r.step, r.status, r.error_code) for r in stored] == [
        ("A02", "intake", "succeeded", None),
        ("A03", "plan", "succeeded", None),
    ]
    first, second = stored[0].id, stored[1].id
    assert first != second
    assert first.version == 4 and second.version == 4
    seen = await kinds(engine, task_id)
    # usage.updated events sit between the run's own events; the ordered sequence of the rest is the claim.
    ordered = [(k, p) if k.startswith("task.") else (k, None) for k, p in seen if k != "usage.updated"]
    assert ordered == [
        started("intake"),
        ("agent.run_started", None),
        ("agent.run_finished", None),
        finished("intake"),
        started("context"),
        finished("context"),
        started("plan"),
        ("agent.run_started", None),
        ("agent.run_finished", None),
        finished("plan"),
        changed("draft", "planned", None),
    ]
    assert bus.unseen == []


async def test_execution_interrupts_after_the_implement_run(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    settings = worker()
    task_id = await make_task("queued")
    await ready_world(engine, settings)
    orch = orchestrate(
        engine,
        settings,
        pricing,
        agent_steps={"implement": AgentStep("B14", implement_input, on_success=to_validation_unavailable)},
    )

    outcome = await orch.run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["implement"], "transitioned")
    after = await row(engine, task_id)
    assert (after.status, after.status_reason) == ("interrupted", "validation_unavailable")
    assert "implement" in after.checkpoint["completed_steps"]  # R-B1
    assert lease_free(after)
    stored = await runs(engine, task_id)
    assert [(r.agent_id, r.step, r.status) for r in stored] == [("B14", "implement", "succeeded")]
    seen = await kinds(engine, task_id)
    assert seen[0] == changed("queued", "running", None)
    assert seen[-1] == changed("running", "interrupted", "validation_unavailable")
    assert finished("implement") in seen


async def test_a_runtime_with_another_owner_loses_the_lease(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    """C2 negative control: the engine claims as OWNER but its runtime renews as another worker. The run
    ends ``lease_lost`` and the engine stops without writing status; the positive control is the two tests
    above, where ``build_orchestrator`` gives both the same owner."""
    settings = worker()
    task_id = await make_task("queued")
    await ready_world(engine, settings)
    sessions = make_sessionmaker(engine)
    bus = RecordingBus(engine)
    audit = AuditWriter(Redactor(settings.secret_values()))
    runtime = build_agent_runtime(
        sessions=sessions,
        settings=settings,
        registry=REGISTRY,
        pricing=pricing,
        audit=audit,
        bus=bus,
        worker_id="w-other:1",
    )
    bound = bind_agent_steps(
        {"implement": AgentStep("B14", implement_input, on_success=to_validation_unavailable)},
        runtime=runtime,
        reads=DbAgentStepReads(sessions),
        registry=REGISTRY,
    )
    miswired = TaskEngine(
        WorkflowPorts(sessions=sessions, bus=bus, audit=audit, worker_id=OWNER, max_active=5, steps=bound)
    )

    outcome = await miswired.run(task_id)

    assert outcome.stopped == "lease_lost"
    after = await row(engine, task_id)
    assert after.status == "running"
    stored = await runs(engine, task_id)
    assert [(r.status, r.error_code) for r in stored] == [("failed", "lease_lost")]
    seen = await kinds(engine, task_id)
    assert all(kind != "task.step_finished" for kind, _ in seen)


async def test_the_production_registry_has_no_agent_steps(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    """N27: until a later work package registers steps, a draft task ends ``step_unavailable`` and no
    agent run is ever started."""
    settings = worker()
    task_id = await make_task("draft", {"user_set": ["title"]})
    await ready_world(engine, settings)

    outcome = await orchestrate(engine, settings, pricing).run(task_id)

    assert outcome.ran == []
    after = await row(engine, task_id)
    assert (after.status, after.error_code) == ("failed", "step_unavailable")
    assert await runs(engine, task_id) == []


# --- the run outcome rows of the table (spec §16.6.5 #8), one engine run each -----------------------------

INTAKE = {"intake": AgentStep("A02", intake_input)}
IMPLEMENT = {"implement": AgentStep("B14", implement_input)}
PIPELINES = {
    "draft": ("draft", "intake", "A02", INTAKE),
    "running": ("queued", "implement", "B14", IMPLEMENT),
}


def sentence(code: str, agent_id: str) -> str:
    return FAIL_MESSAGES[code].replace("{agent_id}", agent_id)


def with_tool(tool: str) -> Any:
    b14 = REGISTRY.by_id("B14")
    assert b14 is not None
    return registry_with("B14", tools=(*b14.tools, tool))


async def drive(
    engine: AsyncEngine,
    make_task: Any,
    pricing: PricingTable,
    pipeline: str,
    *,
    readiness: bool = True,
    prepare: Callable[[uuid.UUID], Awaitable[None]] | None = None,
    **seams: Any,
) -> tuple[uuid.UUID, EngineOutcome, list[Any], Any]:
    """One engine run of the step of ``pipeline`` through the real runtime; returns the task id, the outcome,
    the captured logs and the engine."""
    status, _, _, agent_steps = PIPELINES[pipeline]
    settings = worker()
    task_id = await make_task(status, {"user_set": ["title"]} if status == "draft" else None)
    await ready_world(engine, settings, readiness=readiness)
    if prepare is not None:
        await prepare(task_id)
    orch = orchestrate(engine, settings, pricing, agent_steps=seams.pop("agent_steps", agent_steps), **seams)
    with capture_logs() as logs:
        outcome = await orch.run(task_id)
    return task_id, outcome, logs, orch


async def run_rows(engine: AsyncEngine, task_id: uuid.UUID) -> list[tuple[str, str | None]]:
    return [(r.status, r.error_code) for r in await runs(engine, task_id)]


async def assert_failed(engine: AsyncEngine, task_id: uuid.UUID, code: str, agent_id: str) -> Any:
    after = await row(engine, task_id)
    assert (after.status, after.error_code) == ("failed", code)
    assert after.error_message == sentence(code, agent_id)
    assert lease_free(after)
    return after


@pytest.mark.parametrize("pipeline", ["draft", "running"])
@pytest.mark.parametrize("code", ["provider_not_ready", "PROVIDER_DISABLED", "provider_auth"])
async def test_a_provider_stop_fails_a_draft_task_and_interrupts_a_running_one(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
    code: str,
    pipeline: str,
) -> None:
    """F31, N21: the task code is the lower-case run code; a draft task cannot be interrupted."""
    _, step, agent_id, _ = PIPELINES[pipeline]
    seams: dict[str, Any] = {}
    if code == "provider_auth":
        seams["providers"] = FakeProviders(scripted(Fail("auth")))
    if code == "provider_not_ready":
        seams["readiness"] = False  # no recorded provider smoke

    if code == "PROVIDER_DISABLED":

        async def switch_off(task_id: uuid.UUID) -> None:
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE projects SET settings = settings || CAST(:v AS jsonb)"),
                    {"v": '{"' + KILL_SWITCH_KEY + '": false}'},
                )

        seams["prepare"] = switch_off

    task_id, outcome, _, _ = await drive(engine, make_task, pricing, pipeline, **seams)

    lowered = code.lower()
    after = await row(engine, task_id)
    seen = await kinds(engine, task_id)
    if pipeline == "draft":
        assert outcome.stopped == "failed"
        await assert_failed(engine, task_id, lowered, agent_id)
        assert finished(step, "failed") in seen
    else:
        assert (outcome.claim, outcome.stopped) == (ClaimResult.ACQUIRED, "interrupted")
        assert (after.status, after.status_reason) == ("interrupted", lowered)
        assert "implement" not in after.checkpoint.get("completed_steps", [])
        assert finished(step, "interrupted") in seen
        assert lease_free(after)
        assert after.error_code is None


@pytest.mark.parametrize(
    "case",
    ["timeout", "iteration_limit", "invalid_output", "model_error"],
)
async def test_a_failed_run_fails_the_task_with_the_fixed_sentence(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
    case: str,
) -> None:
    seams: dict[str, Any]
    if case == "timeout":
        slow = SlowProvider(scripted(text_turn()), 3.0)  # cancellable: it waits on the stop token
        seams = {
            "registry": registry_with("B14", limits={"timeout_seconds": 1}),
            "providers": FakeProviders(slow),
        }
    elif case == "iteration_limit":
        seams = {
            "registry": registry_with("B14", limits={"max_iterations": 1}),
            "providers": FakeProviders(scripted(text_turn())),
        }
    elif case == "invalid_output":
        seams = {"providers": FakeProviders(scripted(text_turn(), text_turn()))}
    else:
        seams = {"providers": FakeProviders(scripted(Fail("refusal")))}

    task_id, outcome, _, _ = await drive(engine, make_task, pricing, "running", **seams)

    assert outcome.stopped == "failed"
    await assert_failed(engine, task_id, case, "B14")
    assert await run_rows(engine, task_id) == [("failed", case)]
    assert finished("implement", "failed") in await kinds(engine, task_id)


TWO_READS = Reply(
    (
        ToolUseBlock("toolu_a", "test.read", {"path": "a"}),
        ToolUseBlock("toolu_b", "test.read", {"path": "b"}),
    ),
    stop_reason="tool_use",
)


async def over_budget(engine: AsyncEngine, make_task: Any, pricing: PricingTable, handler: Recorder) -> Any:
    """B14 asks for two reads under ``max_tool_iterations == 1`` (the task's own budget)."""
    sessions = make_sessionmaker(engine)

    async def limit(task_id: uuid.UUID) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tasks SET budget_limit = CAST(:b AS jsonb) WHERE id = :id"),
                {"b": '{"max_tool_iterations": 1}', "id": task_id},
            )

    return await drive(
        engine,
        make_task,
        pricing,
        "running",
        prepare=limit,
        providers=FakeProviders(scripted(TWO_READS)),
        tools=gateway(sessions, spec("test.read", handler)),
        registry=with_tool("test.read"),
    )


async def budget_audits(engine: AsyncEngine, task_id: uuid.UUID) -> list[Any]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT resource_id, result, metadata FROM audit_events "
                "WHERE action = 'budget.exceeded' AND metadata ->> 'task_id' = :t"
            ),
            {"t": str(task_id)},
        )
        return list(result)


async def test_a_budget_stop_fails_the_task_budget_exceeded(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    handler = Recorder()
    task_id, outcome, _, _ = await over_budget(engine, make_task, pricing, handler)

    assert outcome.stopped == "failed"
    await assert_failed(engine, task_id, "budget_exceeded", "B14")
    assert await run_rows(engine, task_id) == [("budget_exceeded", None)]
    (audit,) = await budget_audits(engine, task_id)
    assert audit.result == "denied"
    assert len(handler.calls) == 1


async def test_a_run_that_cannot_be_closed_is_internal_error_not_budget_exceeded(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S20 (BC-25): the budget stop is real (audit written) but the row could not be closed as such, so the
    run ends ``failed internal_error`` and the task must follow the row, not the stop."""

    async def broken(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise OSError("disk gone")

    monkeypatch.setattr(agent_runtime_module.AgentRunWriter, "finish", broken)

    task_id, outcome, _, _ = await over_budget(engine, make_task, pricing, Recorder())

    assert outcome.stopped == "failed"
    await assert_failed(engine, task_id, "internal_error", "B14")
    assert await run_rows(engine, task_id) == [("failed", "internal_error")]
    assert len(await budget_audits(engine, task_id)) == 1


async def test_an_approval_request_fails_the_task_and_leaves_the_approval_pending(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    """N33, N62: the human decision is never skipped; no run resumes, so the task fails
    ``approval_unsupported`` while the server-made approval stays pending."""
    handler = Recorder()
    sessions = make_sessionmaker(engine)

    task_id, outcome, _, _ = await drive(
        engine,
        make_task,
        pricing,
        "running",
        providers=FakeProviders(scripted(tool_turn("test.r3", {"target": "prod"}))),
        tools=gateway(sessions, spec("test.r3", handler)),
        registry=with_tool("test.r3"),
    )

    assert outcome.stopped == "failed"
    await assert_failed(engine, task_id, "approval_unsupported", "B14")
    assert await run_rows(engine, task_id) == [("approval_required", None)]
    async with engine.connect() as conn:
        approvals = [
            r.status
            for r in await conn.execute(
                text("SELECT status FROM approvals WHERE task_id = :t"), {"t": task_id}
            )
        ]
    assert approvals == ["pending"]
    assert handler.calls == []


async def test_a_disabled_agent_fails_the_task_agent_unavailable(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    registry = registry_with("A02", enabled=False)

    task_id, outcome, _, _ = await drive(engine, make_task, pricing, "draft", registry=registry)

    assert outcome.stopped == "failed"
    await assert_failed(engine, task_id, "agent_unavailable", "A02")
    assert await runs(engine, task_id) == []


async def test_an_input_for_another_task_fails_the_task_agent_input_invalid(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    async def foreign(ctx: StepContext) -> Any:
        return agent_input("A02", uuid.uuid4())  # names another task

    task_id, outcome, _, _ = await drive(
        engine, make_task, pricing, "draft", agent_steps={"intake": AgentStep("A02", foreign)}
    )

    assert outcome.stopped == "failed"
    await assert_failed(engine, task_id, "agent_input_invalid", "A02")
    assert await runs(engine, task_id) == []


async def test_a_missing_profile_abandons_the_step_and_frees_the_lease(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    """N56: the profile row is read before any run; a missing one is a deployment gap, not a task failure."""
    settings = worker()
    task_id = await make_task("draft", {"user_set": ["title"]})
    await ready_world(engine, settings)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM agent_profiles WHERE agent_id = 'A02'"))
    orch = orchestrate(engine, settings, pricing, agent_steps=INTAKE)

    with capture_logs() as logs:
        outcome = await orch.run(task_id)

    assert outcome.stopped == "abandoned"
    assert [e["event"] for e in logs if e["event"] == "agent_profile_missing"] == ["agent_profile_missing"]
    assert await runs(engine, task_id) == []
    after = await row(engine, task_id)
    assert after.status == "draft"
    assert lease_free(after)
    async with make_sessionmaker(engine)() as db:
        assert await claim_lease(db, task_id, OWNER, max_active=5, claimable=frozenset({"draft"})) is (
            ClaimResult.ACQUIRED
        )


async def test_a_lease_taken_over_during_the_step_is_returned_as_lease_lost(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    async def take_over(ctx: StepContext) -> Any:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE tasks SET lease_owner = 'w-other', "
                    "lease_expires_at = now() + interval '60 seconds' WHERE id = :id"
                ),
                {"id": ctx.task_id},
            )
        return agent_input("A02", ctx.task_id)

    task_id, outcome, _, _ = await drive(
        engine, make_task, pricing, "draft", agent_steps={"intake": AgentStep("A02", take_over)}
    )

    assert outcome.stopped == "lease_lost"
    assert await run_rows(engine, task_id) == [("failed", "lease_lost")]
    after = await row(engine, task_id)
    assert (after.status, after.error_code) == ("draft", None)
    assert after.lease_owner == "w-other"
    assert all(kind != "task.step_finished" for kind, _ in await kinds(engine, task_id))


async def test_run_text_never_reaches_a_log_the_task_or_its_events(
    engine: AsyncEngine,
    make_task,
    pricing: PricingTable,  # type: ignore[no-untyped-def]
) -> None:
    """RD-6 (BC-26): the model's error text and what it streamed are held; only the fixed sentence shows."""
    marker = "rd6-" + "held-0123"
    provider = StreamThenFail(marker)
    task_id, _, logs, _ = await drive(
        engine, make_task, pricing, "running", providers=FakeProviders(provider)
    )

    # Positive controls: the marker really went through the stream, and the delivery's logs were captured.
    assert provider.streamed == [marker]
    ended = [e for e in logs if e["event"] == "agent_step_run_ended"]
    assert [(e["run_status"], e["run_error_code"]) for e in ended] == [("failed", "model_error")]

    after = await assert_failed(engine, task_id, "model_error", "B14")
    assert after.error_message == "Agent B14 stopped: the model call failed"
    assert "the model call failed (refusal)" not in after.error_message
    events_text = repr(await kinds(engine, task_id))
    for held in (marker, "the model call failed (refusal)"):
        assert held not in repr(logs)
        assert held not in after.error_message
        assert held not in events_text
