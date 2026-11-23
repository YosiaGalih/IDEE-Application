# ruff: noqa: F401, F811  (make_task is the fixture of test_engine, imported by name)
"""The workflow engine drives the real agent runtime (WP-3.2c2c2 Task 5; spec §16.6.5).

``build_orchestrator`` assembles the real ``AgentRuntime`` over the database; the provider is the production
scripted one (scenario ``generic``). Every status is read from the database (tasks, agent_runs, task_events),
never from model text. C2: the runtime renews the lease under the owner of the engine's claim.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.llm.pricing import PricingTable
from app.services.agent_runtime import build_agent_runtime
from app.services.agent_step import AgentStep, DbAgentStepReads, bind_agent_steps
from app.workflow.engine import EngineOutcome, TaskEngine, WorkflowPorts
from app.workflow.lease import ClaimResult
from tests.engine_runtime_fakes import (
    OWNER,
    Renewals,
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
    make_task,
    row,
    started,
)
from tests.runtime_fakes import REGISTRY, RecordingBus
from tests.security.test_runtime_gate import worker
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

    assert renewals.calls  # the runtime renewed the lease (C2: as OWNER, or the runs would be lease_lost)
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
