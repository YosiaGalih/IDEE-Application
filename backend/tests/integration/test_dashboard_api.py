"""``GET /dashboard`` (WP-2.1e; FR-007..011, API_CONTRACTS §3) on real PostgreSQL: counts and lists from
stored projects and tasks; what the platform does not record yet is marked, never shown as 0."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.models.projects import Project
from app.models.tasks import Task
from app.services.dashboard import current_period
from app.workflow.states import Actor, TaskStatus, transition
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    owner,
    owner_id,
    workspace_root,
)

DASHBOARD = "/api/v1/dashboard"
SYSTEM = Actor("system", "orchestrator")
AUDIT = AuditWriter(Redactor())
S = TaskStatus
# How each status is reached from draft (workflow.states.ALLOWED).
PATHS: dict[str, tuple[TaskStatus, ...]] = {
    "draft": (),
    "running": (S.PLANNED, S.QUEUED, S.RUNNING),
    "validating": (S.PLANNED, S.QUEUED, S.RUNNING, S.VALIDATING),
    "completed": (S.PLANNED, S.QUEUED, S.RUNNING, S.VALIDATING, S.COMPLETED),
    "failed": (S.PLANNED, S.QUEUED, S.RUNNING, S.FAILED),
    "waiting_approval": (S.WAITING_APPROVAL,),
    "interrupted": (S.PLANNED, S.QUEUED, S.RUNNING, S.INTERRUPTED),
    "needs_changes": (S.PLANNED, S.QUEUED, S.RUNNING, S.VALIDATING, S.NEEDS_CHANGES),
}
UNAVAILABLE_USAGE = {
    "input_tokens": None,
    "output_tokens": None,
    "estimated_cost": None,
    "cost_status": "unavailable",
}


async def add_project(
    engine: AsyncEngine, owner_user: uuid.UUID, name: str, status: str = "active"
) -> uuid.UUID:
    project_id = uuid.uuid4()
    async with make_sessionmaker(engine)() as session:
        session.add(
            Project(
                id=project_id,
                owner_id=owner_user,
                name=name,
                slug=f"{name.lower().replace(' ', '-')}-{project_id.hex[:6]}",
                workspace_ref=str(project_id),
            )
        )
        await session.commit()
    if status != "active":
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE projects SET status = :s WHERE id = :id"), {"s": status, "id": project_id}
            )
    return project_id


async def add_task(
    engine: AsyncEngine,
    project_id: uuid.UUID,
    creator: uuid.UUID,
    status: str,
    title: str,
    minutes_ago: int = 0,
) -> uuid.UUID:
    """A task walked to ``status`` through the state machine, last touched ``minutes_ago``."""
    async with make_sessionmaker(engine)() as session:
        task = Task(
            project_id=project_id, title=title, prompt="p", workflow_type="modify", created_by=creator
        )
        session.add(task)
        await session.flush()
        for step in PATHS[status]:
            await transition(session, task, step, reason=f"to {step.value}", actor=SYSTEM, audit=AUDIT)
        await session.commit()
        task_id = task.id
    async with engine.begin() as conn:  # the order of "recent" is updated_at; a test sets it
        await conn.execute(
            text("UPDATE tasks SET updated_at = now() - make_interval(mins => :m) WHERE id = :id"),
            {"m": minutes_ago, "id": task_id},
        )
    return task_id


async def board(client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any], httpx.Response]:
    response = await client.get(DASHBOARD)
    assert response.status_code == 200, response.text
    body = response.json()
    return body["data"], body["meta"], response


async def test_dashboard_counts_tasks_by_status(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id = await add_project(engine, owner_id, "Kalkulator")
    ids = {
        status: await add_task(engine, project_id, owner_id, status, f"Task {status}", minutes_ago=index)
        for index, status in enumerate(PATHS)
    }
    data, meta, _ = await board(owner)
    assert data["metrics"] == {
        "active_projects": 1,
        "running_tasks": 2,  # running + validating
        "completed_tasks": 1,
        "failed_tasks": 1,
        "waiting_approval": 1,
    }
    queue = {item["status"]: item for item in data["failed_queue"]}
    assert set(queue) == {"failed", "interrupted", "needs_changes", "waiting_approval"}
    assert meta["failed_queue_total"] == 4
    assert {item["id"] for item in data["failed_queue"]} == {
        str(ids[status]) for status in ("failed", "interrupted", "needs_changes", "waiting_approval")
    }
    for item in data["recent_tasks"]:
        assert item["project_id"] == str(project_id)
        assert item["project_name"] == "Kalkulator"
        assert item["usage"] == UNAVAILABLE_USAGE
        assert item["has_pending_approval"] is (item["status"] == "waiting_approval")
    failed = queue["failed"]
    assert (failed["status_reason"], failed["workflow_type"], failed["priority"]) == (
        "to failed",
        "modify",
        "normal",
    )
    assert set(failed) == {
        "id", "project_id", "project_name", "title", "status", "status_reason", "current_step",
        "workflow_type", "priority", "iteration", "usage", "has_pending_approval", "created_at", "updated_at",
    }  # fmt: skip


async def test_dashboard_lists_are_ordered_and_capped(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    projects = [await add_project(engine, owner_id, f"Proyek {index}") for index in range(6)]
    shelf = await add_project(engine, owner_id, "Rak", status="archived")
    for index in range(12):
        await add_task(engine, projects[0], owner_id, "failed", f"Gagal {index}", minutes_ago=index + 1)
    await add_task(engine, shelf, owner_id, "completed", "Di arsip", minutes_ago=0)
    data, meta, _ = await board(owner)
    assert [item["title"] for item in data["recent_tasks"]] == ["Di arsip"] + [f"Gagal {i}" for i in range(9)]
    assert [item["title"] for item in data["failed_queue"]] == [f"Gagal {i}" for i in range(10)]
    assert meta["failed_queue_total"] == 12
    # An archived project still counts its tasks, but it is not a "recent project" (GET /projects default).
    assert data["metrics"]["completed_tasks"] == 1
    assert data["metrics"]["active_projects"] == 6
    assert len(data["recent_projects"]) == 5
    assert str(shelf) not in {project["id"] for project in data["recent_projects"]}
    assert all(set(project) >= {"id", "name", "slug", "permissions"} for project in data["recent_projects"])


async def test_dashboard_marks_what_is_not_recorded(
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    data, meta, response = await board(owner)
    assert data["agent_activity"] == []
    assert meta["unavailable"] == ["agent_activity", "usage"]
    start, end = current_period(datetime.now(UTC))
    assert data["usage"] == {
        "period": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        },
        **UNAVAILABLE_USAGE,
    }
    assert response.headers["Cache-Control"] == "no-store"


async def test_dashboard_for_a_new_user_is_empty(
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    data, meta, _ = await board(owner)
    assert data["metrics"] == dict.fromkeys(
        ("active_projects", "running_tasks", "completed_tasks", "failed_tasks", "waiting_approval"), 0
    )
    assert (data["recent_projects"], data["recent_tasks"], data["failed_queue"]) == ([], [], [])
    assert meta["failed_queue_total"] == 0


async def test_dashboard_needs_a_session(app: FastAPI) -> None:  # noqa: F811
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(DASHBOARD)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_the_read_only_snapshot_stays_in_its_session(
    owner_id: uuid.UUID,  # noqa: F811
    tmp_path: Any,
) -> None:
    """One pooled connection: after the dashboard read, the next session is read-write and READ COMMITTED
    again."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.rbac import RoleGrant
    from app.gitops.adapter import GitAdapter
    from app.services import dashboard, projects
    from tests.conftest import make_test_env

    single = create_async_engine(make_test_env()["DATABASE_URL"], pool_size=1, max_overflow=0)
    try:
        sessions = make_sessionmaker(single)
        env = projects.ProjectEnv(sessions, AUDIT, GitAdapter(), tmp_path)
        actor = projects.Actor(owner_id, "o@example.test", "O", (RoleGrant("owner", "global"),))
        async with sessions() as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            inside = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
        assert inside == "off"  # a plain REPEATABLE READ session is writable: the next check means something
        await dashboard.load_dashboard(env, actor)
        async with sessions() as session:
            after = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
            level = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
        assert (after, level) == ("off", "read committed")
    finally:
        await single.dispose()


async def test_the_dashboard_reads_in_a_read_only_snapshot(
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    from app.core.rbac import RoleGrant
    from app.gitops.adapter import GitAdapter
    from app.services import dashboard, projects

    seen: list[tuple[str, str]] = []
    summaries = dashboard._summaries  # pyright: ignore[reportPrivateUsage]

    async def recorded(session: Any, *args: Any, **kwargs: Any) -> Any:
        read_only = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
        level = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
        seen.append((read_only, level))
        return await summaries(session, *args, **kwargs)

    monkeypatch.setattr(dashboard, "_summaries", recorded)
    env = projects.ProjectEnv(make_sessionmaker(engine), AUDIT, GitAdapter(), tmp_path)
    actor = projects.Actor(owner_id, "o@example.test", "O", (RoleGrant("owner", "global"),))
    await dashboard.load_dashboard(env, actor)
    assert seen == [("on", "repeatable read"), ("on", "repeatable read")]
