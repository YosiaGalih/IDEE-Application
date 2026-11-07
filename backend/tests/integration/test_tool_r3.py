"""R3 tool calls behind a ``tool_r3`` approval (API §11 #6, §11.1; ADR-021, ADR-024; spec WP-3.1d Goal 5,
Review Focus 4): the server asks, a person decides exactly that input, the task moves accordingly, and the
gateway consumes the approval once when the tool runs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import make_sessionmaker
from app.models.approvals import Approval
from app.models.tasks import Task
from app.services import approval_handlers, approval_store
from app.services.approval_registry import default_handlers
from app.services.tool_r3 import ToolR3Handler
from app.tools.base import RiskLevel, ToolContext
from app.tools.gateway import DENIALS
from app.workflow.states import TaskStatus
from tests.conftest import allow_approval_status_writes
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope, calls, move
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    create,
    headers,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_task_schema import make_project, make_task
from tests.tool_fakes import AUDIT, Recorder, context, gateway, make_run, spec

clean = denials.clean
sessions = denials.sessions


@dataclass(frozen=True)
class RunScope(Scope):
    run_id: uuid.UUID


@pytest.fixture
async def scope(sessions: async_sessionmaker[AsyncSession]) -> RunScope:
    """A project, a task and an agent run of B14 on it (an R3 approval names its run)."""
    async with sessions() as session:
        project = await make_project(session)
        task = await make_task(session, project)
        run_id = await make_run(session, task.id)
        await session.commit()
        return RunScope(project.id, task.id, run_id)


S = TaskStatus
RUNNING = (S.PLANNED, S.QUEUED, S.RUNNING)


def r3_context(scope: Scope, **overrides: object) -> ToolContext:
    run_id = scope.run_id if isinstance(scope, RunScope) else None
    return context(
        task_id=scope.task_id,
        project_id=scope.project_id,
        tools=["test.r3"],
        agent_run_id=run_id,
        **overrides,  # type: ignore[arg-type]
    )


async def approvals_of(sessions: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> list[Approval]:
    async with sessions() as session:
        query = select(Approval).where(Approval.task_id == task_id).order_by(Approval.created_at)
        return list((await session.scalars(query)).all())


async def decide(
    sessions: async_sessionmaker[AsyncSession], approval_id: uuid.UUID, *, approve: bool
) -> None:
    """A person's decision, as services.approvals records it (its route is tested in test_approvals_api)."""
    async with sessions() as session:
        approval = await session.get(Approval, approval_id)
        assert approval is not None
        decided = await approval_store.decide(
            session,
            AUDIT,
            approval,
            approve=approve,
            payload_hash=approval.payload_hash,
            user_id=await owner_of(session, approval),
            note=None,
        )
        assert decided
        await session.commit()


async def owner_of(session: AsyncSession, approval: Approval) -> uuid.UUID:
    from app.models.projects import Project

    project = await session.get(Project, approval.project_id)
    assert project is not None
    return project.owner_id


async def task_status(sessions: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> str:
    async with sessions() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task.status


async def test_an_r3_call_asks_for_an_approval_bound_to_its_input(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.r3", handler))
    result = await built.execute(r3_context(scope), "test.r3", {"target": "staging"}, tool_use_id="toolu_1")
    assert (result.status, result.is_error) == ("approval_required", True)
    assert handler.calls == []
    [approval] = await approvals_of(sessions, scope.task_id)
    payload = {
        "task_id": str(scope.task_id),
        "agent_run_id": str(scope.run_id),
        "tool": "test.r3",
        "input": {"target": "staging", "count": 1},
    }
    assert approval.payload == payload
    assert approval.payload_hash == approval_store.payload_hash(
        "tool_r3", project_id=scope.project_id, task_id=scope.task_id, payload=payload
    )
    assert (approval.action_type, approval.status, approval.risk_level) == ("tool_r3", "pending", 3)
    assert approval.requested_by == "system:tool_gateway"
    assert approval.expires_at is not None
    assert abs(approval.expires_at - approval.created_at - timedelta(hours=24)) < timedelta(seconds=5)
    assert str(approval.id) in result.content
    [row] = await calls(sessions, scope.task_id)
    assert (row.status, row.approval_id, row.idempotency_key) == ("approval_required", approval.id, None)


async def test_the_same_action_reuses_the_open_request(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    built = gateway(sessions, spec("test.r3", Recorder()))
    first = await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    again = await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_2")
    assert first.status == again.status == "approval_required"
    assert len(await approvals_of(sessions, scope.task_id)) == 1


async def test_another_input_supersedes_the_open_request(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    built = gateway(sessions, spec("test.r3", Recorder()))
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    await built.execute(r3_context(scope), "test.r3", {"target": "b"}, tool_use_id="toolu_2")
    old, new = await approvals_of(sessions, scope.task_id)
    assert (old.status, old.handler_status) == ("superseded", "not_required")
    assert (new.status, new.payload["input"]["target"]) == ("pending", "b")


async def test_approved_resumes_the_task_and_runs_once(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.r3", handler))
    await move(sessions, scope.task_id, *RUNNING)
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    await move(sessions, scope.task_id, S.WAITING_APPROVAL)  # what the engine does on approval_required
    [approval] = await approvals_of(sessions, scope.task_id)
    await decide(sessions, approval.id, approve=True)
    status = await approval_handlers.dispatch(
        sessions=sessions,
        audit=AUDIT,
        handlers=default_handlers(sessions=sessions, audit=AUDIT),
        approval_id=approval.id,
    )
    assert status == "succeeded"
    assert await task_status(sessions, scope.task_id) == "queued"
    await move(sessions, scope.task_id, S.RUNNING)
    result = await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    assert result.status == "ok" and len(handler.calls) == 1
    [approval] = await approvals_of(sessions, scope.task_id)
    assert approval.status == "consumed" and approval.consumed_at is not None
    rows = await calls(sessions, scope.task_id)
    assert [(r.status, r.approval_id) for r in rows] == [
        ("approval_required", approval.id),
        ("ok", approval.id),
    ]
    # The same tool use again is a replay, not a new action: no new approval.
    replay = await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    assert "already ran" in replay.content and len(handler.calls) == 1
    # A new tool use is a new action: single use, so a person is asked again.
    fresh = await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_3")
    assert fresh.status == "approval_required"
    assert [a.status for a in await approvals_of(sessions, scope.task_id)] == ["consumed", "pending"]


async def test_an_approval_for_another_input_is_never_consumed(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.r3", handler))
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    [approval] = await approvals_of(sessions, scope.task_id)
    await decide(sessions, approval.id, approve=True)
    result = await built.execute(r3_context(scope), "test.r3", {"target": "b"}, tool_use_id="toolu_2")
    assert result.status == "approval_required" and handler.calls == []
    old, new = await approvals_of(sessions, scope.task_id)
    assert (old.status, new.status) == ("superseded", "pending")


async def test_an_approval_past_its_time_is_asked_again(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.r3", handler))
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    [approval] = await approvals_of(sessions, scope.task_id)
    await decide(sessions, approval.id, approve=True)
    async with sessions() as session:
        await session.execute(
            update(Approval).where(Approval.id == approval.id).values(expires_at=Approval.created_at)
        )
        await session.commit()
    result = await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_2")
    assert result.status == "approval_required" and handler.calls == []
    assert [a.status for a in await approvals_of(sessions, scope.task_id)] == ["superseded", "pending"]


@pytest.mark.parametrize(("decision", "path"), [("reject", "rejected"), ("expire", "expired")])
async def test_rejected_or_expired_fails_the_task(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, decision: str, path: str
) -> None:
    built = gateway(sessions, spec("test.r3", Recorder()))
    await move(sessions, scope.task_id, *RUNNING)
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    await move(sessions, scope.task_id, S.WAITING_APPROVAL)
    [approval] = await approvals_of(sessions, scope.task_id)
    if decision == "reject":
        await decide(sessions, approval.id, approve=False)
    else:
        async with sessions() as session:
            row = await session.get(Approval, approval.id)
            assert row is not None
            await approval_store.expire(session, AUDIT, row, actor="system:test")
            await session.commit()
    handler = ToolR3Handler(sessions=sessions, audit=AUDIT)
    await getattr(handler, path)(approval.id, now=None)
    assert await task_status(sessions, scope.task_id) == "failed"


async def test_resume_returns_a_task_parked_in_planning_to_draft(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    # ADR-021: draft -> waiting_approval during planning; approve -> back to draft.
    built = gateway(sessions, spec("test.r3", Recorder()))
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    await move(sessions, scope.task_id, S.WAITING_APPROVAL)
    [approval] = await approvals_of(sessions, scope.task_id)
    await decide(sessions, approval.id, approve=True)
    await ToolR3Handler(sessions=sessions, audit=AUDIT).approved(approval.id, now=None)
    assert await task_status(sessions, scope.task_id) == "draft"


async def test_a_decision_faster_than_parking_fails_visibly_then_resumes(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    built = gateway(sessions, spec("test.r3", Recorder()))
    await move(sessions, scope.task_id, *RUNNING)
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    [approval] = await approvals_of(sessions, scope.task_id)
    await decide(sessions, approval.id, approve=True)
    handlers = default_handlers(sessions=sessions, audit=AUDIT)
    first = await approval_handlers.dispatch(
        sessions=sessions, audit=AUDIT, handlers=handlers, approval_id=approval.id
    )
    assert first == "failed"  # the task is still running: not silently "succeeded" (sweep retries)
    assert await task_status(sessions, scope.task_id) == "running"
    await move(sessions, scope.task_id, S.WAITING_APPROVAL)
    second = await approval_handlers.dispatch(
        sessions=sessions, audit=AUDIT, handlers=handlers, approval_id=approval.id
    )
    assert second == "succeeded"
    assert await task_status(sessions, scope.task_id) == "queued"


async def test_the_handler_is_idempotent(sessions: async_sessionmaker[AsyncSession], scope: Scope) -> None:
    built = gateway(sessions, spec("test.r3", Recorder()))
    await move(sessions, scope.task_id, *RUNNING)
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    await move(sessions, scope.task_id, S.WAITING_APPROVAL)
    [approval] = await approvals_of(sessions, scope.task_id)
    await decide(sessions, approval.id, approve=True)
    handler = ToolR3Handler(sessions=sessions, audit=AUDIT)
    await handler.approved(approval.id, now=None)
    await handler.approved(approval.id, now=None)  # e.g. the inline dispatch and a sweep
    assert await task_status(sessions, scope.task_id) == "queued"


async def test_a_finished_task_needs_nothing(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    built = gateway(sessions, spec("test.r3", Recorder()))
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    await move(sessions, scope.task_id, S.CANCELLED)
    [approval] = await approvals_of(sessions, scope.task_id)
    async with sessions() as session:
        await allow_approval_status_writes(session)
        await session.execute(update(Approval).where(Approval.id == approval.id).values(status="rejected"))
        await session.commit()
    await ToolR3Handler(sessions=sessions, audit=AUDIT).rejected(approval.id, now=None)
    assert await task_status(sessions, scope.task_id) == "cancelled"


async def test_an_input_that_cannot_be_hashed_canonically_is_denied(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    class FloatInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        ratio: float

    handler = Recorder()
    built = gateway(sessions, spec("test.r3", handler, input_model=FloatInput, risk=RiskLevel.R3))
    result = await built.execute(r3_context(scope), "test.r3", {"ratio": 0.5}, tool_use_id="toolu_1")
    assert (result.status, result.content) == ("denied", DENIALS["input_not_canonical"])
    assert await approvals_of(sessions, scope.task_id) == [] and handler.calls == []


def test_requestable_action_types_are_exactly_those_with_a_handler() -> None:
    # 3.1c1 AG-13 LOW-E: nothing can be requested that no handler would ever act on.
    handlers = default_handlers(sessions=async_sessionmaker(), audit=AUDIT)
    assert set(handlers) == set(approval_store.REQUESTABLE)


async def test_a_person_decides_a_tool_r3_through_the_api(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    response = await create(owner, "Gated")
    project_id = uuid.UUID(response.json()["data"]["id"])
    tool_sessions = make_sessionmaker(engine)
    async with tool_sessions() as session:
        from app.models.projects import Project

        project = await session.get(Project, project_id)
        assert project is not None
        task = await make_task(session, project)
        run_id = await make_run(session, task.id)
        await session.commit()
    gated = RunScope(project_id, task.id, run_id)
    built = gateway(tool_sessions, spec("test.r3", Recorder()))
    await move(tool_sessions, task.id, *RUNNING)
    await built.execute(r3_context(gated), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    await move(tool_sessions, task.id, S.WAITING_APPROVAL)
    listed = (await owner.get("/api/v1/approvals?status=pending")).json()["data"]
    [item] = [a for a in listed if a["action_type"] == "tool_r3"]
    assert item["can_decide"] is True
    decision = {"decision": "approve", "payload_hash": item["payload_hash"]}
    answer = await owner.post(
        f"/api/v1/approvals/{item['id']}/decision", json=decision, headers=headers(owner)
    )
    assert answer.status_code == 200, answer.text
    assert await task_status(tool_sessions, task.id) == "queued"


async def test_nul_in_an_r3_input_still_asks_for_an_approval(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    # M2: the approval payload is jsonb too; a NUL from the model must not make the request unrecordable.
    built = gateway(sessions, spec("test.r3", Recorder()))
    result = await built.execute(r3_context(scope), "test.r3", {"target": "a\x00b"}, tool_use_id="toolu_1")
    assert result.status == "approval_required"
    [approval] = await approvals_of(sessions, scope.task_id)
    assert "\x00" not in approval.payload["input"]["target"]


async def test_a_cancelled_run_consumes_no_approval(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    from app.core.cancel import CancelToken

    handler = Recorder()
    built = gateway(sessions, spec("test.r3", handler))
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    [approval] = await approvals_of(sessions, scope.task_id)
    await decide(sessions, approval.id, approve=True)
    token = CancelToken()
    token.cancel()
    result = await built.execute(
        r3_context(scope, cancel=token), "test.r3", {"target": "a"}, tool_use_id="toolu_2"
    )
    assert result.status == "cancelled" and handler.calls == []
    [approval] = await approvals_of(sessions, scope.task_id)
    assert approval.status == "approved"


async def test_the_request_audit_names_the_agent(
    sessions: async_sessionmaker[AsyncSession], scope: RunScope
) -> None:
    # Spec Goal 5: requested_by is system:tool_gateway; the agent is in the audit metadata.
    from app.models.audit import AuditEvent

    built = gateway(sessions, spec("test.r3", Recorder()))
    await built.execute(r3_context(scope), "test.r3", {"target": "a"}, tool_use_id="toolu_1")
    [approval] = await approvals_of(sessions, scope.task_id)
    async with sessions() as session:
        requested = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.resource_id == str(approval.id), AuditEvent.action == "approval.requested"
            )
        )
    assert requested is not None
    assert (requested.actor_id, requested.metadata_["agent_id"]) == ("tool_gateway", "B14")
