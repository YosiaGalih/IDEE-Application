"""``POST /tasks/{task_id}/cancel`` over HTTP with real PostgreSQL (WP-3.2f1, spec §16.7.2 #111, R-F1, R-F7).

The sandbox is a fake installed through ``app.dependency_overrides[task_control_env]``; the sessions and the
audit writer stay the real ones."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents import readiness
from app.api.deps import AuditDep
from app.api.routes.tasks import task_control_env
from app.core.db import make_sessionmaker
from app.models.audit import AuditEvent
from app.sandbox.client import FakeSandboxClient
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
