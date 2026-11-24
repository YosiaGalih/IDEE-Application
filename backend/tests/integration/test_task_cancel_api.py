"""``POST /tasks/{task_id}/cancel`` over HTTP with real PostgreSQL (WP-3.2f1, spec §16.7.2 #111, R-F1, R-F7).

The sandbox is a fake installed through ``app.dependency_overrides[task_control_env]``; the sessions and the
audit writer stay the real ones."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

import app.core.db as db_module
from app.agents import readiness
from app.api.deps import AuditDep
from app.api.routes import tasks as tasks_route
from app.api.routes.tasks import task_control_env
from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.gitops import adapter as gitops_adapter
from app.models.audit import AuditEvent
from app.models.tasks import TaskEvent
from app.sandbox.client import FakeSandboxClient, SandboxUnavailable
from app.services import task_control
from app.services.task_control import TaskControlEnv
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    make_user,
    owner,
    owner_id,
    signed_in,
    workspace_root,
)
from tests.integration.test_tasks_api import PASSWORD, make_tasks, open_gate, project, sql  # noqa: F401

CANCEL = "/api/v1/tasks/{}/cancel"


@pytest.fixture
def sandbox(app: FastAPI) -> Iterator[FakeSandboxClient]:  # noqa: F811
    fake = FakeSandboxClient(kill_returns=2)

    def env(audit: AuditDep) -> TaskControlEnv:
        return TaskControlEnv(sessions=dependencies_sessions(app), audit=audit, sandbox=fake)

    app.dependency_overrides[task_control_env] = env
    yield fake
    app.dependency_overrides.pop(task_control_env, None)


def dependencies_sessions(app: FastAPI):  # type: ignore[no-untyped-def]  # noqa: F811
    return app.state.dependencies.sessions


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["kk_csrf"]}


async def place(engine: AsyncEngine, task_id: str, status: str, *, lease: bool = False) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
        await conn.execute(
            text(
                "UPDATE tasks SET status = :s, lease_owner = CASE WHEN :l THEN 'w-1' END, "
                "lease_expires_at = CASE WHEN :l THEN now() + interval '60 seconds' END WHERE id = :id"
            ),
            {"s": status, "l": lease, "id": uuid.UUID(task_id)},
        )


async def actions(engine: AsyncEngine, task_id: str) -> list[str]:
    async with make_sessionmaker(engine)() as session:
        rows = await session.scalars(
            select(AuditEvent.action).where(
                AuditEvent.resource_id == task_id, AuditEvent.action.like("task.cancel%")
            )
        )
        return list(rows)


async def one_task(owner: httpx.AsyncClient) -> str:  # noqa: F811
    project_id = await project(owner)
    [task_id] = await make_tasks(owner, project_id, "batalkan")
    return task_id


async def test_queued_is_cancelled(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")
    response = await owner.post(CANCEL.format(task_id), json={"reason": "salah"}, headers=csrf(owner))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "cancelled"
    assert response.json()["data"]["id"] == task_id
    assert await actions(engine, task_id) == ["task.cancel_requested"]


async def test_leased_running_task_gets_the_flag_and_a_kill(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    sandbox: FakeSandboxClient,
) -> None:
    task_id = await one_task(owner)
    await place(engine, task_id, "running", lease=True)
    response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "running" and data["cancel_requested"] is True
    assert sandbox.killed_tasks == [uuid.UUID(task_id)]


async def test_out_of_scope_is_404_like_a_missing_task(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    task_id = await one_task(owner)
    await make_user(engine, "eve@example.test")
    eve = await signed_in(app, "eve@example.test", PASSWORD)
    try:
        refused = await eve.post(CANCEL.format(task_id), headers=csrf(eve))
        missing = await eve.post(CANCEL.format(uuid.uuid4()), headers=csrf(eve))
    finally:
        await eve.aclose()
    assert refused.status_code == missing.status_code == 404
    assert refused.json()["error"]["code"] == missing.json()["error"]["code"]
    assert await actions(engine, task_id) == []


@pytest.mark.parametrize("role", ["reviewer", "readonly"])
async def test_a_role_without_task_control_is_403(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    role: str,
) -> None:
    project_id = await project(owner)
    [task_id] = await make_tasks(owner, project_id, "batalkan")
    await make_user(engine, "rev@example.test", (role, f"project:{project_id}"))
    other = await signed_in(app, "rev@example.test", PASSWORD)
    try:
        response = await other.post(CANCEL.format(task_id), headers=csrf(other))
    finally:
        await other.aclose()
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert await actions(engine, task_id) == []


async def test_completed_is_409_invalid_transition(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    sandbox: FakeSandboxClient,
) -> None:
    task_id = await one_task(owner)
    await place(engine, task_id, "completed")
    response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "INVALID_TRANSITION"
    assert sandbox.killed_tasks == []


async def test_reason_over_200_characters_is_422(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    task_id = await one_task(owner)
    response = await owner.post(CANCEL.format(task_id), json={"reason": "x" * 201}, headers=csrf(owner))
    assert response.status_code == 422, response.text
    assert await actions(engine, task_id) == []


async def test_an_unknown_field_is_422(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    task_id = await one_task(owner)
    response = await owner.post(CANCEL.format(task_id), json={"force": True}, headers=csrf(owner))
    assert response.status_code == 422, response.text
    assert await actions(engine, task_id) == []


async def test_cancel_is_not_gated_by_the_engine_flag(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")
    monkeypatch.setattr(readiness, "RUNTIMES_AVAILABLE", {"agent_runtime": False, "workflow_engine": False})
    response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "cancelled"


async def test_cancel_in_an_archived_project(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")
    await sql(engine, "UPDATE projects SET status = 'archived'")
    response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "cancelled"


async def test_a_second_cancel_is_a_noop(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    sandbox: FakeSandboxClient,
) -> None:
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")
    first = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
    second = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["data"]["status"] == "cancelled"
    assert await actions(engine, task_id) == ["task.cancel_requested"]
    assert sandbox.killed_tasks == []


async def test_a_cancel_needs_the_csrf_token(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")
    response = await owner.post(CANCEL.format(task_id))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert await actions(engine, task_id) == []


# --- reason characters (L1), a busy row (R11), the kill state (R9), bounded records (M2) -------------------

SENTINEL = "bo" + "om-secret-" + "text"


async def state(engine: AsyncEngine, task_id: str) -> tuple[str, bool]:
    async with engine.connect() as conn:
        found = (
            await conn.execute(
                text("SELECT status, cancel_requested FROM tasks WHERE id = :id"), {"id": uuid.UUID(task_id)}
            )
        ).one()
    return found.status, found.cancel_requested


async def logged(engine: AsyncEngine, task_id: str) -> list[dict[str, Any]]:
    """The task's ``task.log`` payloads (the unconfirmed-kill marker's event)."""
    async with make_sessionmaker(engine)() as session:
        rows = await session.scalars(
            select(TaskEvent.payload).where(
                TaskEvent.task_id == uuid.UUID(task_id), TaskEvent.type == "task.log"
            )
        )
        return list(rows)


def test_the_reason_refuses_the_class_the_commit_message_refuses() -> None:
    """L1: one forbidden class for free text that reaches an audit or a log (tab, LF and CR stay)."""
    assert tasks_route._REASON_FORBIDDEN.pattern == gitops_adapter._MESSAGE_FORBIDDEN.pattern


@pytest.mark.parametrize("character", ["\x00", "\x1b"], ids=["NUL", "ESC"])
async def test_a_reason_with_a_control_character_is_422(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    character: str,
) -> None:
    task_id = await one_task(owner)
    response = await owner.post(
        CANCEL.format(task_id), json={"reason": "salah" + character + "ketik"}, headers=csrf(owner)
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [detail["loc"] for detail in error["details"]] == [["body", "reason"]]
    assert await actions(engine, task_id) == []
    assert await state(engine, task_id) == ("draft", False)


@pytest.mark.parametrize("reason", ["kolom\tdua", "baris satu\nbaris dua", "baris\r\ndua"])
async def test_a_reason_with_a_tab_or_a_line_break_is_accepted(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    reason: str,
) -> None:
    task_id = await one_task(owner)
    response = await owner.post(CANCEL.format(task_id), json={"reason": reason}, headers=csrf(owner))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "cancelled"
    assert await actions(engine, task_id) == ["task.cancel_requested"]


async def test_a_task_locked_elsewhere_is_409_busy(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R11: the row lock not available within lock_timeout (55P03) is 409 busy with Retry-After, not a 500."""
    monkeypatch.setattr(db_module, "LOCK_TIMEOUT", "200ms")  # bound_transaction reads it at call time
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")
    async with engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT id FROM tasks WHERE id = :id FOR UPDATE"), {"id": uuid.UUID(task_id)})
        started = time.monotonic()
        response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
        waited = time.monotonic() - started
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "CONFLICT"
    assert error["details"][0]["reason"] == "busy"
    assert response.headers["Retry-After"] == "1"
    assert waited < 3  # the lowered lock_timeout answered, not the default
    assert await actions(engine, task_id) == []
    assert await state(engine, task_id) == ("queued", False)


@pytest.mark.parametrize(
    ("how", "expected"),
    [
        ("queued", "not_needed"),
        ("killed", "confirmed"),
        ("none_running", "confirmed"),
        ("raised", "unconfirmed"),
        ("timed_out", "unconfirmed"),
    ],
)
async def test_the_response_says_what_the_kill_did(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    sandbox: FakeSandboxClient,
    monkeypatch: pytest.MonkeyPatch,
    how: str,
    expected: str,
) -> None:
    """R9: ``meta.kill``. ``confirmed`` whenever the kill returned (with nothing to stop too)."""
    task_id = await one_task(owner)
    await place(engine, task_id, "queued" if how == "queued" else "running", lease=how != "queued")
    if how == "none_running":
        sandbox.kill_returns = 0
    elif how == "raised":
        sandbox.kill_error = SandboxUnavailable(SENTINEL)
    elif how == "timed_out":
        monkeypatch.setattr(task_control, "KILL_TIMEOUT_S", 0.2)
        sandbox.kill_delay_s = 5
    response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
    assert response.status_code == 200, response.text
    assert response.json()["meta"] == {"kill": expected}
    assert SENTINEL not in response.text


async def test_a_lost_marker_still_answers_unconfirmed(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    sandbox: FakeSandboxClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silent-failure H1: the marker's write fails. The answer still says ``unconfirmed``; neither half of the
    marker is kept (one transaction, R-F10); an error log names both error types and nothing else."""
    task_id = await one_task(owner)
    await place(engine, task_id, "running", lease=True)
    sandbox.kill_error = SandboxUnavailable(SENTINEL)
    real_write = AuditWriter.write

    async def failing_write(self: AuditWriter, session: Any, **fields: Any) -> int:
        if fields["action"] == "task.cancel_unconfirmed":
            raise ConnectionError(SENTINEL)
        return await real_write(self, session, **fields)

    monkeypatch.setattr(AuditWriter, "write", failing_write)
    with capture_logs() as logs:
        response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
    assert response.status_code == 200, response.text
    assert response.json()["meta"] == {"kill": "unconfirmed"}
    assert await logged(engine, task_id) == []
    assert await actions(engine, task_id) == ["task.cancel_requested"]
    lost = [log for log in logs if log["event"] == "task_cancel_record_failed"]
    assert [
        (log["log_level"], log["task_id"], log["record"], log["error_type"], log["kill_error_type"])
        for log in lost
    ] == [("error", task_id, "cancel_unconfirmed", "ConnectionError", "SandboxUnavailable")]
    assert "exc_info" not in lost[0]
    assert SENTINEL not in repr(logs)


@pytest.mark.parametrize(
    ("action", "record", "kill_error", "expected"),
    [
        ("sandbox.run_killed", "run_killed", None, "confirmed"),
        ("task.cancel_unconfirmed", "cancel_unconfirmed", SandboxUnavailable("down"), "unconfirmed"),
    ],
)
async def test_a_hung_record_write_is_bounded(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,  # noqa: F811
    engine: AsyncEngine,
    sandbox: FakeSandboxClient,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    record: str,
    kill_error: BaseException | None,
    expected: str,
) -> None:
    """Backend M2: a post-commit record write that hangs ends at RECORD_TIMEOUT_S, like any record failure."""
    monkeypatch.setattr(task_control, "RECORD_TIMEOUT_S", 0.3)
    task_id = await one_task(owner)
    await place(engine, task_id, "running", lease=True)
    sandbox.kill_error = kill_error
    real_write = AuditWriter.write

    async def hanging_write(self: AuditWriter, session: Any, **fields: Any) -> int:
        if fields["action"] == action:
            await asyncio.sleep(30)
        return await real_write(self, session, **fields)

    monkeypatch.setattr(AuditWriter, "write", hanging_write)
    with capture_logs() as logs:
        started = time.monotonic()
        response = await asyncio.wait_for(owner.post(CANCEL.format(task_id), headers=csrf(owner)), timeout=10)
        took = time.monotonic() - started
    assert response.status_code == 200, response.text
    assert response.json()["meta"] == {"kill": expected}
    assert took < 0.3 + 2
    lost = [log for log in logs if log["event"] == "task_cancel_record_failed"]
    assert [(log["log_level"], log["record"], log["error_type"]) for log in lost] == [
        ("error", record, "TimeoutError")
    ]
    assert await logged(engine, task_id) == []  # a timed-out marker leaves no half either
