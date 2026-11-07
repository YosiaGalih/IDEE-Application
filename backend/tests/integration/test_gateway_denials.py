"""Every refusal of the ToolGateway is recorded, audited and shown on the task timeline, and the handler never
runs (AC-06; SECURITY_MODEL §3 order, §9, §10; spec WP-3.1d Goal 3, Review Focus 1)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import make_sessionmaker
from app.models.audit import AuditEvent
from app.models.projects import Project
from app.models.tasks import Task, TaskEvent
from app.models.tools import ToolCall
from app.tools.base import ToolContext
from app.tools.gateway import DENIALS, ToolGateway
from app.workflow.states import Actor, TaskStatus, transition
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.tool_fakes import AUDIT, Recorder, context, gateway, spec


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(engine)


@dataclass(frozen=True)
class Scope:
    project_id: uuid.UUID
    task_id: uuid.UUID


@pytest.fixture
async def scope(sessions: async_sessionmaker[AsyncSession]) -> Scope:
    async with sessions() as session:
        project = await make_project(session)
        task = await make_task(session, project)
        await session.commit()
        return Scope(project.id, task.id)


async def move(sessions: async_sessionmaker[AsyncSession], task_id: uuid.UUID, *steps: TaskStatus) -> None:
    async with sessions() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        for step in steps:
            await transition(session, task, step, reason="test", actor=Actor("system", "test"), audit=AUDIT)
        await session.commit()


async def calls(sessions: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> list[ToolCall]:
    async with sessions() as session:
        query = select(ToolCall).where(ToolCall.task_id == task_id).order_by(ToolCall.created_at)
        return list((await session.scalars(query)).all())


async def audits(sessions: async_sessionmaker[AsyncSession], row: ToolCall) -> list[AuditEvent]:
    async with sessions() as session:
        query = select(AuditEvent).where(AuditEvent.resource_id == str(row.id)).order_by(AuditEvent.id)
        return list((await session.scalars(query)).all())


async def timeline(sessions: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> list[TaskEvent]:
    async with sessions() as session:
        query = (
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.type.like("tool.%"))
            .order_by(TaskEvent.seq)
        )
        return list((await session.scalars(query)).all())


async def assert_denied(
    sessions: async_sessionmaker[AsyncSession],
    built: ToolGateway,
    ctx: ToolContext,
    tool: str,
    raw: Any,
    reason: str,
    handler: Recorder,
    *,
    tool_use_id: str | None = "toolu_1",
) -> ToolCall:
    result = await built.execute(ctx, tool, raw, tool_use_id=tool_use_id)
    assert (result.status, result.is_error, result.content) == ("denied", True, DENIALS[reason])
    assert handler.calls == []
    rows = [row for row in await calls(sessions, ctx.task_id) if row.id == result.tool_call_id]
    assert len(rows) == 1
    row = rows[0]
    assert (row.status, row.error_code, row.output_summary) == ("denied", reason, DENIALS[reason])
    assert row.idempotency_key is None
    audited = await audits(sessions, row)
    assert [(a.action, a.result, a.actor_type, a.actor_id) for a in audited] == [
        ("tool.denied", "denied", "agent", "B14")
    ]
    assert audited[0].metadata_["reason"] == reason
    finished = [e for e in await timeline(sessions, ctx.task_id) if e.payload["tool_call_id"] == str(row.id)]
    assert [(e.type, e.payload["status"]) for e in finished] == [("tool.call_finished", "denied")]
    return row


def all_tools() -> Iterable[str]:
    return ["test.read", "test.write", "test.exec", "test.r3"]


async def test_denied_tool_recorded_and_audited(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    # AC-06: the model asks for a tool its agent may not use; the request is offered by mistake.
    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=[], offered=["test.read"])
    await assert_denied(sessions, built, ctx, "test.read", {}, "not_allowed", handler)


@pytest.mark.parametrize(
    ("tool", "offered", "granted", "perms", "reason"),
    [
        ("../etc/passwd", None, ["test.read"], {}, "invalid_name"),
        ("repo__read_file", None, ["test.read"], {}, "invalid_name"),
        ("submit_result", ["submit_result"], ["submit_result"], {}, "terminal_tool"),
        ("test.read", ["test.write"], ["test.read", "test.write"], {}, "not_offered"),
        ("test.read", ["test.read"], [], {}, "not_allowed"),
        ("repo.read_file", None, ["repo.read_file"], {}, "not_registered"),
        ("test.read", None, ["test.read"], {"filesystem": "none"}, "permission"),
        ("test.exec", None, ["test.exec"], {"sandbox": "deny"}, "permission"),
    ],
)
async def test_name_allowlist_and_permission_refusals(
    sessions: async_sessionmaker[AsyncSession],
    scope: Scope,
    tool: str,
    offered: list[str] | None,
    granted: list[str],
    perms: dict[str, str],
    reason: str,
) -> None:
    handler = Recorder()
    built = gateway(sessions, *(spec(name, handler) for name in all_tools()))
    ctx = context(
        task_id=scope.task_id, project_id=scope.project_id, tools=granted, offered=offered, perms=perms
    )
    row = await assert_denied(sessions, built, ctx, tool, {}, reason, handler)
    if reason == "invalid_name":
        assert (row.tool_name, row.risk_level) == ("<invalid>", 4)


async def test_a_permission_refusal_names_the_unmet_preconditions(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.write", handler))
    ctx = context(
        task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"], filesystem="read_only"
    )
    row = await assert_denied(sessions, built, ctx, "test.write", {"path": "a.py"}, "permission", handler)
    assert (await audits(sessions, row))[0].metadata_["unmet"] == ["fs_write"]


@pytest.mark.parametrize(
    ("filesystem", "path"),
    [("docs_only", "src/app.py"), ("test_only", "src/app.py"), ("scoped_write", "../outside.py")],
)
async def test_write_paths_follow_the_filesystem_mode(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, filesystem: str, path: str
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.write", handler))
    ctx = context(
        task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"], filesystem=filesystem
    )
    await assert_denied(sessions, built, ctx, "test.write", {"path": path}, "path_not_allowed", handler)


@pytest.mark.parametrize("tool_use_id", [None, "bad id with spaces", "x" * 129])
async def test_a_side_effect_needs_a_tool_use_id(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tool_use_id: str | None
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.write", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"])
    raw = {"path": "src/app.py"}
    await assert_denied(
        sessions, built, ctx, "test.write", raw, "invalid_tool_use_id", handler, tool_use_id=tool_use_id
    )


async def test_a_task_of_another_project_is_out_of_scope(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    async with sessions() as session:
        other = await make_project(session, slug="other")
        await session.commit()
    ctx = context(task_id=scope.task_id, project_id=other.id, tools=["test.read"])
    await assert_denied(sessions, built, ctx, "test.read", {}, "scope_mismatch", handler)


async def test_a_finished_task_gets_no_tools(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    await move(sessions, scope.task_id, TaskStatus.CANCELLED)
    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])
    await assert_denied(sessions, built, ctx, "test.read", {}, "task_finished", handler)


@pytest.mark.parametrize("status", ["archived", "deleting_pending_approval"])
async def test_a_project_that_is_not_active_gets_no_tools(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, status: str
) -> None:
    async with sessions() as session:
        await session.execute(update(Project).where(Project.id == scope.project_id).values(status=status))
        await session.commit()
    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])
    await assert_denied(sessions, built, ctx, "test.read", {}, "project_not_active", handler)


async def test_the_quota_counts_calls_that_ran(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(
        task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"], quota={"test.read": 2}
    )
    denied = context(task_id=scope.task_id, project_id=scope.project_id, tools=[], offered=["test.read"])
    await built.execute(denied, "test.read", {})  # a refusal does not use the quota
    assert (await built.execute(ctx, "test.read", {})).status == "ok"
    assert (await built.execute(ctx, "test.read", {})).status == "ok"
    handler.calls.clear()
    await assert_denied(sessions, built, ctx, "test.read", {}, "quota_exceeded", handler)


async def test_a_refusal_comes_before_input_validation(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    # Allowlist first (SECURITY_MODEL §3): an invalid input to a tool the agent may not use is a denial.
    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=[], offered=["test.read"])
    await assert_denied(sessions, built, ctx, "test.read", {"bogus": 1}, "not_allowed", handler)


async def test_calls_that_never_started_do_not_use_the_quota(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    # /code-review (spec c1): an invalid input or a call cancelled before it started ran no handler.
    from app.core.cancel import CancelToken

    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(
        task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"], quota={"test.read": 1}
    )
    cancelled = CancelToken()
    cancelled.cancel()
    stopped = context(
        task_id=scope.task_id,
        project_id=scope.project_id,
        tools=["test.read"],
        quota={"test.read": 1},
        cancel=cancelled,
    )
    assert (await built.execute(ctx, "test.read", {"bogus": 1})).status == "error"
    assert (await built.execute(stopped, "test.read", {})).status == "cancelled"
    assert (await built.execute(ctx, "test.read", {})).status == "ok"
    assert len(handler.calls) == 1
