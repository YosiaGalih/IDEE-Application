"""The AgentRuntime composed over a real database (plan WP-3.1f2a Task 5; spec WP-3.1f Keputusan Lead
#15-#23, #33; ADR-070).

Real provider gate, budget guard, usage ledger, lease heartbeat and tool gateway; the run store is the test
``DbRunStore`` until the redacted writer of Task 7 and the events are fake until Task 6.
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
    """The refusal ends the run ``budget_exceeded`` before the second tool; the audit row and the
    ``usage.budget_exceeded`` event belong to the run store (Task 7), asserted there."""
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
    run = compose(
        sessions,
        settings,
        pricing,
        scripted(two, DONE),
        tools=gateway(sessions, spec("test.read", handler)),
        registry=_tool_registry("test.read"),
    )
    result = await run.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert (result.status, result.error_code) == ("budget_exceeded", None)
    assert result.budget is not None
    assert (result.budget.kind, result.budget.limit, result.budget.used) == ("tool_iterations", 1, 1)
    assert await count(session, ToolCall, task_id=task_id) == 1
    assert len(handler.calls) == 1


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
