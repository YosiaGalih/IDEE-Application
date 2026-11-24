"""request_cancel (WP-3.2f1 #101, #111, #112, R-F8): the API writer of ``cancelled``, under the row lock."""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.rbac import RoleGrant
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.audit import AuditEvent
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task, TaskEvent
from app.sandbox.client import FakeSandboxClient, SandboxError, SandboxUnavailable
from app.services import task_control
from app.services.project_tasks import cancel_open_tasks
from app.services.projects import Actor as ApiActor
from app.services.task_control import CANCEL_UNCONFIRMED_MESSAGE, TaskControlEnv, cancel_task
from app.workflow.control import CancelDecision, request_cancel
from app.workflow.states import Actor
from tests.conftest import reset_data

AUDIT = AuditWriter(Redactor())
ACTOR = Actor("user", "user-1")


@pytest.fixture
async def make_task(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    await reset_data(engine)
    async with make_sessionmaker(engine)() as session:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.commit()
        owner_id, project_id = owner.id, project.id

    async def create(status: str, lease: str | None = None, *, flag: bool = False) -> uuid.UUID:
        async with make_sessionmaker(engine)() as session:
            task = Task(
                project_id=project_id, title="t", prompt="p", workflow_type="create", created_by=owner_id
            )
            session.add(task)
            await session.flush()
            # Test setup: place the task directly (past the writer backstop).
            await session.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
            await session.execute(
                text(
                    "UPDATE tasks SET status = :s, cancel_requested = :c, "
                    "lease_owner = CASE WHEN CAST(:l AS text) IS NULL THEN NULL ELSE 'w-old' END, "
                    "lease_expires_at = now() + CAST(CAST(:l AS text) AS interval) WHERE id = :id"
                ),
                {"s": status, "c": flag, "l": lease, "id": task.id},
            )
            await session.commit()
            return task.id

    create.project_id = project_id  # type: ignore[attr-defined]
    create.owner_id = owner_id  # type: ignore[attr-defined]
    return create


async def cancel(engine: AsyncEngine, task_id: uuid.UUID, reason: str | None = None) -> CancelDecision:
    async with make_sessionmaker(engine)() as session:
        decision = await request_cancel(session, task_id, actor=ACTOR, audit=AUDIT, reason=reason)
        await session.commit()
    return decision


async def row(engine: AsyncEngine, task_id: uuid.UUID) -> tuple[str, bool, int]:
    async with engine.connect() as conn:
        r = (
            await conn.execute(
                text("SELECT status, cancel_requested, version FROM tasks WHERE id = :id"), {"id": task_id}
            )
        ).one()
    return r.status, r.cancel_requested, r.version


async def audits(engine: AsyncEngine, task_id: uuid.UUID, action: str) -> list[AuditEvent]:
    async with make_sessionmaker(engine)() as session:
        return list(
            (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.action == action, AuditEvent.resource_id == str(task_id)
                    )
                )
            ).all()
        )


async def test_queued_is_cancelled_now(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("queued")
    decision = await cancel(engine, task_id)
    assert decision == CancelDecision(status_before="queued", cancelled_now=True, kill=False, noop=False)
    assert (await row(engine, task_id))[:2] == ("cancelled", True)
    assert len(await audits(engine, task_id, "task.cancel_requested")) == 1


async def test_running_with_a_live_lease_only_sets_the_flag(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("running", "60 seconds")
    _, _, version = await row(engine, task_id)
    decision = await cancel(engine, task_id)
    assert decision == CancelDecision(status_before="running", cancelled_now=False, kill=True, noop=False)
    assert await row(engine, task_id) == ("running", True, version)  # flag without a version bump (R-F8)
    events = await audits(engine, task_id, "task.cancel_requested")
    assert len(events) == 1 and events[0].actor_id == "user-1" and events[0].result == "success"
    assert events[0].project_id == make_task.project_id


async def test_running_with_a_dead_lease_is_cancelled_now(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("running", "-5 seconds")
    decision = await cancel(engine, task_id)
    assert decision == CancelDecision(status_before="running", cancelled_now=True, kill=True, noop=False)
    assert (await row(engine, task_id))[:2] == ("cancelled", True)


async def test_draft_with_a_live_lease_only_sets_the_flag(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("draft", "60 seconds")
    decision = await cancel(engine, task_id)
    assert decision == CancelDecision(status_before="draft", cancelled_now=False, kill=True, noop=False)
    assert (await row(engine, task_id))[:2] == ("draft", True)


async def test_cancelled_is_a_noop_without_audit(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("cancelled")
    decision = await cancel(engine, task_id)
    assert decision == CancelDecision(status_before="cancelled", cancelled_now=False, kill=False, noop=True)
    assert await audits(engine, task_id, "task.cancel_requested") == []


@pytest.mark.parametrize("status", ["completed", "rejected"])
async def test_completed_or_rejected_is_refused(engine: AsyncEngine, make_task: Any, status: str) -> None:
    task_id = await make_task(status)
    with pytest.raises(AppError) as caught:
        await cancel(engine, task_id)
    assert caught.value.code == "INVALID_TRANSITION"
    assert caught.value.http_status == 409
    assert caught.value.details == [{"from": status, "to": "cancelled"}]
    assert await audits(engine, task_id, "task.cancel_requested") == []


async def test_a_second_call_on_a_leased_task_writes_no_second_audit(
    engine: AsyncEngine, make_task: Any
) -> None:
    task_id = await make_task("running", "60 seconds")
    await cancel(engine, task_id)
    again = await cancel(engine, task_id)
    assert again == CancelDecision(status_before="running", cancelled_now=False, kill=True, noop=False)
    assert len(await audits(engine, task_id, "task.cancel_requested")) == 1


async def test_the_reason_is_redacted_and_bounded(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("queued")
    secret = "sk-test-" + "abc123"
    async with make_sessionmaker(engine)() as session:
        await request_cancel(
            session,
            task_id,
            actor=ACTOR,
            audit=AuditWriter(Redactor([secret])),
            reason=f"leaked {secret} " + "x" * 400,
        )
        await session.commit()
    (event,) = await audits(engine, task_id, "task.cancel_requested")
    reason = event.metadata_["reason"]
    assert secret not in reason and len(reason) <= 200


async def test_no_reason_means_no_metadata_key(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("queued")
    await cancel(engine, task_id)
    (event,) = await audits(engine, task_id, "task.cancel_requested")
    assert "reason" not in event.metadata_


async def test_cancel_open_tasks_sets_the_flag(engine: AsyncEngine, make_task: Any) -> None:
    leased = await make_task("running", "60 seconds")
    queued = await make_task("queued")
    done = await make_task("completed")
    async with make_sessionmaker(engine)() as session:
        count = await cancel_open_tasks(
            session,
            make_task.project_id,
            reason="project deleted",
            actor=Actor("system", "delete"),
            audit=AUDIT,
        )
        await session.commit()
    assert count == 2
    assert (await row(engine, leased))[:2] == ("cancelled", True)
    assert (await row(engine, queued))[:2] == ("cancelled", True)
    assert (await row(engine, done))[:2] == ("completed", False)


async def test_a_flagged_task_whose_lease_died_is_cancelled_now(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("running", "-5 seconds", flag=True)
    decision = await cancel(engine, task_id)
    assert decision == CancelDecision(status_before="running", cancelled_now=True, kill=True, noop=False)
    assert (await row(engine, task_id))[:2] == ("cancelled", True)
    assert await audits(engine, task_id, "task.cancel_requested") == []


# --- cancel_task (Task 5): authorize, request under the lock, commit, then kill outside the DB -------------

SENTINEL = "bo" + "om-secret-" + "text"


def api_actor(user_id: uuid.UUID, *grants: RoleGrant) -> ApiActor:
    return ApiActor(user_id, "o@example.test", "O", grants)


def control_env(engine: AsyncEngine, sandbox: FakeSandboxClient) -> TaskControlEnv:
    return TaskControlEnv(sessions=make_sessionmaker(engine), audit=AUDIT, sandbox=sandbox)


async def events_of(engine: AsyncEngine, task_id: uuid.UUID, type: str) -> list[TaskEvent]:
    async with make_sessionmaker(engine)() as session:
        return list(
            (
                await session.scalars(
                    select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.type == type)
                )
            ).all()
        )


async def test_cancel_task_kills_and_audits_a_killed_run(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("running", "60 seconds")
    sandbox = FakeSandboxClient(kill_returns=1)
    view = await cancel_task(
        control_env(engine, sandbox),
        api_actor(make_task.owner_id, RoleGrant("owner", "global")),
        task_id,
        reason=None,
        iteration_max=3,
    )
    assert sandbox.killed_tasks == [task_id]
    killed = await audits(engine, task_id, "sandbox.run_killed")
    assert len(killed) == 1 and killed[0].metadata_ == {"killed": 1} and killed[0].result == "success"
    assert view.cancel_requested is True and view.task.status == "running"


async def test_zero_killed_writes_no_run_killed(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("running", "60 seconds")
    sandbox = FakeSandboxClient(kill_returns=0)
    await cancel_task(
        control_env(engine, sandbox),
        api_actor(make_task.owner_id, RoleGrant("owner", "global")),
        task_id,
        reason=None,
        iteration_max=3,
    )
    assert sandbox.killed_tasks == [task_id]
    assert await audits(engine, task_id, "sandbox.run_killed") == []
    assert await audits(engine, task_id, "task.cancel_unconfirmed") == []


@pytest.mark.parametrize("error", [SandboxUnavailable, SandboxError])
async def test_a_failed_kill_marks_the_task(
    engine: AsyncEngine, make_task: Any, error: type[SandboxError]
) -> None:
    task_id = await make_task("running", "60 seconds")
    sandbox = FakeSandboxClient(kill_error=error(SENTINEL))
    with capture_logs() as logs:
        view = await cancel_task(
            control_env(engine, sandbox),
            api_actor(make_task.owner_id, RoleGrant("owner", "global")),
            task_id,
            reason=None,
            iteration_max=3,
        )
    assert view.task.status == "running" and view.cancel_requested is True
    logged = await events_of(engine, task_id, "task.log")
    assert [e.payload for e in logged] == [
        {"level": "warning", "code": "cancel_unconfirmed", "message": CANCEL_UNCONFIRMED_MESSAGE}
    ]
    marks = await audits(engine, task_id, "task.cancel_unconfirmed")
    assert len(marks) == 1 and marks[0].result == "error"
    assert marks[0].metadata_ == {"error_type": error.__name__}
    assert await audits(engine, task_id, "sandbox.run_killed") == []
    assert SENTINEL not in repr(logged[0].payload) + repr(marks[0].metadata_) + repr(logs)
    own = [log for log in logs if log["event"] == "task_cancel_unconfirmed"]
    assert len(own) == 1 and own[0]["task_id"] == str(task_id) and own[0]["error_type"] == error.__name__
    assert "exc_info" not in own[0]


async def test_a_hanging_kill_times_out(
    engine: AsyncEngine, make_task: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(task_control, "KILL_TIMEOUT_S", 0.2)
    task_id = await make_task("running", "60 seconds")
    sandbox = FakeSandboxClient(kill_delay_s=5)
    started = time.monotonic()
    view = await cancel_task(
        control_env(engine, sandbox),
        api_actor(make_task.owner_id, RoleGrant("owner", "global")),
        task_id,
        reason=None,
        iteration_max=3,
    )
    assert time.monotonic() - started < 1
    assert view.task.status == "running"
    marks = await audits(engine, task_id, "task.cancel_unconfirmed")
    assert len(marks) == 1 and marks[0].metadata_ == {"error_type": "TimeoutError"}
    assert len(await events_of(engine, task_id, "task.log")) == 1


async def test_no_kill_for_a_queued_task(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("queued")
    sandbox = FakeSandboxClient(kill_returns=1)
    view = await cancel_task(
        control_env(engine, sandbox),
        api_actor(make_task.owner_id, RoleGrant("owner", "global")),
        task_id,
        reason="not needed",
        iteration_max=3,
    )
    assert sandbox.killed_tasks == []
    assert view.task.status == "cancelled" and view.cancel_requested is True
    assert await audits(engine, task_id, "sandbox.run_killed") == []


async def test_out_of_scope_is_not_found(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("queued")
    sandbox = FakeSandboxClient()
    with pytest.raises(AppError) as caught:
        await cancel_task(
            control_env(engine, sandbox), api_actor(uuid.uuid4()), task_id, reason=None, iteration_max=3
        )
    assert caught.value.http_status == 404
    with pytest.raises(AppError) as missing:
        await cancel_task(
            control_env(engine, sandbox),
            api_actor(make_task.owner_id, RoleGrant("owner", "global")),
            uuid.uuid4(),
            reason=None,
            iteration_max=3,
        )
    assert missing.value.http_status == 404
    assert (await row(engine, task_id))[:2] == ("queued", False)
    assert sandbox.killed_tasks == []


async def test_reader_is_forbidden(engine: AsyncEngine, make_task: Any) -> None:
    task_id = await make_task("queued")
    sandbox = FakeSandboxClient()
    reader = api_actor(uuid.uuid4(), RoleGrant("readonly", f"project:{make_task.project_id}"))
    with pytest.raises(AppError) as caught:
        await cancel_task(control_env(engine, sandbox), reader, task_id, reason=None, iteration_max=3)
    assert caught.value.http_status == 403
    assert (await row(engine, task_id))[:2] == ("queued", False)
    assert sandbox.killed_tasks == []
