"""``GET /dashboard`` (WP-2.1e; FR-007..011, API_CONTRACTS §3) on real PostgreSQL: counts and lists from
stored projects and tasks, usage from the ledger (WP-3.1f1); what the platform does not record yet is marked,
never shown as 0."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.money import MONEY_PATTERN
from app.core.redaction import Redactor
from app.llm.base import Usage
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
from tests.usage_fakes import record

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
# A task or a month without ledger rows: a counted zero (spec WP-3.1f Keputusan Lead #5).
NO_USAGE = {"input_tokens": 0, "output_tokens": 0, "estimated_cost": "0", "cost_status": "estimated"}


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
                # deleted_at exists exactly while a project is deleted (ck_projects_deleted_at, WP-3.1c).
                text(
                    "UPDATE projects SET status = :s, deleted_at = CASE WHEN :gone THEN now() END "
                    "WHERE id = :id"
                ),
                {"s": status, "gone": status == "deleted", "id": project_id},
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
        assert item["usage"] == NO_USAGE
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
    assert meta["unavailable"] == ["agent_activity"]  # usage now comes from the ledger (WP-3.1f1)
    start, end = current_period(datetime.now(UTC))
    assert data["usage"] == {
        "period": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        },
        **NO_USAGE,
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


async def test_counts_and_lists_come_from_one_snapshot(
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    """Backend review SHOULD 1/2: writes that land while the dashboard reads never make a count disagree with
    a list. Another request adds a failed task and archives a project after the counts were taken."""
    from app.core.rbac import RoleGrant
    from app.gitops.adapter import GitAdapter
    from app.services import dashboard, projects

    kept = await add_project(engine, owner_id, "Satu")
    shelved = await add_project(engine, owner_id, "Dua")
    await add_task(engine, kept, owner_id, "failed", "Lama", minutes_ago=5)
    summaries = dashboard._summaries  # pyright: ignore[reportPrivateUsage]
    late: list[uuid.UUID] = []

    async def meanwhile(session: Any, *args: Any, **kwargs: Any) -> Any:
        if not late:
            late.append(await add_task(engine, kept, owner_id, "failed", "Baru"))
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE projects SET status = 'archived' WHERE id = :id"), {"id": shelved}
                )
            await record(engine, kept, task_id=late[0])  # a ledger row lands too (WP-3.1f1)
        return await summaries(session, *args, **kwargs)

    monkeypatch.setattr(dashboard, "_summaries", meanwhile)
    env = projects.ProjectEnv(make_sessionmaker(engine), AUDIT, GitAdapter(), tmp_path)
    actor = projects.Actor(owner_id, "o@example.test", "O", (RoleGrant("owner", "global"),))
    board = await dashboard.load_dashboard(env, actor)
    assert late  # the concurrent write did happen
    assert (board.metrics["failed_tasks"], board.failed_queue_total) == (1, 1)
    assert [summary.title for summary in board.failed_queue] == ["Lama"]
    assert [summary.title for summary in board.recent_tasks] == ["Lama"]
    assert board.metrics["active_projects"] == 2
    assert {view.project.id for view in board.recent_projects} == {kept, shelved}
    assert (board.usage.input_tokens, dict(board.task_usage)) == (0, {})  # usage reads the same snapshot


async def test_a_failed_read_leaves_the_pooled_connection_clean(
    owner_id: uuid.UUID,  # noqa: F811
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.rbac import RoleGrant
    from app.gitops.adapter import GitAdapter
    from app.services import dashboard, projects
    from tests.conftest import make_test_env

    async def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("query failed")

    monkeypatch.setattr(dashboard, "_summaries", broken)
    single = create_async_engine(make_test_env()["DATABASE_URL"], pool_size=1, max_overflow=0)
    try:
        sessions = make_sessionmaker(single)
        env = projects.ProjectEnv(sessions, AUDIT, GitAdapter(), tmp_path)
        actor = projects.Actor(owner_id, "o@example.test", "O", (RoleGrant("owner", "global"),))
        try:
            await dashboard.load_dashboard(env, actor)
        except RuntimeError:
            pass
        else:
            raise AssertionError("the failure must reach the caller")
        async with sessions() as session:
            after = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
            level = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
        assert (after, level) == ("off", "read committed")
    finally:
        await single.dispose()


async def test_ties_in_updated_at_order_by_creation(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """Backend NIT 1: tasks touched in one transaction share updated_at; the newer task comes first."""
    project_id = await add_project(engine, owner_id, "Seri")
    first = await add_task(engine, project_id, owner_id, "failed", "Pertama")
    second = await add_task(engine, project_id, owner_id, "failed", "Kedua")
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE tasks SET updated_at = '2026-11-04T08:00:00Z'"))
        await conn.execute(
            text("UPDATE tasks SET created_at = '2026-11-04T07:00:00Z' WHERE id = :id"), {"id": first}
        )
        await conn.execute(
            text("UPDATE tasks SET created_at = '2026-11-04T07:30:00Z' WHERE id = :id"), {"id": second}
        )
    data, _, _ = await board(owner)
    assert [task["title"] for task in data["failed_queue"]] == ["Kedua", "Pertama"]


# --- usage from the ledger (WP-3.1f1 Task 8a; spec WP-3.1f Goal 7, Keputusan Lead #5, #13) -----------------


async def test_dashboard_usage_is_this_months_ledger(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await add_project(engine, owner_id, "Bulanan")
    start, end = current_period(datetime.now(UTC))
    await record(engine, project, at=start)  # in: the month starts here
    await record(engine, project, model="claude-unpriced", usage=Usage(10, 5))  # in, unavailable
    await record(
        engine, project, usage=Usage(1_000, 1_000), at=start - timedelta(microseconds=1)
    )  # last month
    await record(engine, project, usage=Usage(2_000, 2_000), at=end)  # next month: [start, end)
    data, meta, _ = await board(owner)
    assert data["usage"] == {
        "period": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        },
        "input_tokens": 110,
        "output_tokens": 25,
        "estimated_cost": "0.00002",  # the known sum: 100 x 0.10 + 20 x 0.50 per million
        "cost_status": "partial",  # priced rows mixed with an unavailable one
    }
    assert "usage" not in meta["unavailable"]


async def test_task_summaries_carry_their_ledger_usage(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await add_project(engine, owner_id, "Per Task")
    failed = await add_task(engine, project, owner_id, "failed", "Gagal", minutes_ago=1)
    quiet = await add_task(engine, project, owner_id, "running", "Diam", minutes_ago=2)
    await record(engine, project, task_id=failed)
    await record(engine, project, task_id=failed, status="error", error_kind="rate_limited", usage=None)
    data, _, _ = await board(owner)
    recent = {item["id"]: item["usage"] for item in data["recent_tasks"]}
    assert recent[str(failed)] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "estimated_cost": "0.00002",
        "cost_status": "partial",
    }
    assert recent[str(quiet)] == NO_USAGE
    assert data["failed_queue"][0]["usage"] == recent[str(failed)]  # the queue carries the same numbers


async def test_dashboard_money_is_a_decimal_string(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await add_project(engine, owner_id, "Uang")
    task = await add_task(engine, project, owner_id, "failed", "Kecil")
    await record(engine, project, task_id=task, usage=Usage(0, 1))  # 1 x 0.50 per million
    data, _, _ = await board(owner)
    costs = [data["usage"]["estimated_cost"], data["recent_tasks"][0]["usage"]["estimated_cost"]]
    assert costs == ["0.0000005", "0.0000005"]  # never 5E-7, never a float, never rounded to 0
    assert all(isinstance(cost, str) and re.fullmatch(MONEY_PATTERN, cost) for cost in costs)
