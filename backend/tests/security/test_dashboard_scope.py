"""Dashboard scope (WP-2.1e; AC-01, T6): the dashboard adds up every project a user can see, so it must see
exactly what ``GET /projects`` sees. One visibility rule serves both."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.rbac import RoleGrant
from app.core.redaction import Redactor
from app.gitops.adapter import GitAdapter
from app.models.projects import Project
from app.services import projects
from tests.integration.test_dashboard_api import DASHBOARD, add_project, add_task
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    grant,
    make_user,
    signed_in,
    workspace_root,
)

PASSWORD = "user-long-passphrase"  # make_user's


async def _project(engine: AsyncEngine, owner: uuid.UUID, name: str, status: str = "active") -> uuid.UUID:
    project_id = uuid.uuid4()
    async with make_sessionmaker(engine)() as session:
        session.add(
            Project(
                id=project_id, owner_id=owner, name=name, slug=name.lower(), workspace_ref=str(project_id)
            )
        )
        await session.commit()
    if status != "active":
        async with engine.begin() as conn:  # projects writes its own status; a test may set any value
            await conn.execute(
                text("UPDATE projects SET status = :status WHERE id = :id"),
                {"status": status, "id": project_id},
            )
    return project_id


def _actor(user_id: uuid.UUID, *grants: RoleGrant) -> projects.Actor:
    return projects.Actor(user_id, f"{user_id.hex[:6]}@example.test", "U", grants)


async def test_visible_filter_matches_project_list(engine: AsyncEngine, tmp_path: Path) -> None:
    alice = await make_user(engine, "alice@example.test")
    bob = await make_user(engine, "bob@example.test")
    shared = await _project(engine, alice, "Shared")
    await _project(engine, alice, "Shelf", status="archived")
    gone = await _project(engine, alice, "Gone", status="deleted")
    await _project(engine, bob, "Bobs")
    env = projects.ProjectEnv(make_sessionmaker(engine), AuditWriter(Redactor()), GitAdapter(), tmp_path)
    actors = {
        "alice": _actor(alice, RoleGrant("owner", "global")),
        # A grant on a deleted project, and one on a project that is visible: only the second counts.
        "bob": _actor(
            bob,
            RoleGrant("developer", "global"),
            RoleGrant("readonly", f"project:{shared}"),
            RoleGrant("readonly", f"project:{gone}"),
        ),
    }
    for actor in actors.values():
        async with make_sessionmaker(engine)() as session:
            seen = set(
                await session.scalars(select(Project.id).where(projects.visible_projects_filter(actor)))
            )
        page = await projects.list_projects(env, actor, status="all", page_size=100)
        assert seen == {view.project.id for view in page.items}
        assert gone not in seen
    async with make_sessionmaker(engine)() as session:
        names = set(
            await session.scalars(select(Project.name).where(projects.visible_projects_filter(actors["bob"])))
        )
    assert names == {"Shared", "Bobs"}


# --- through HTTP ------------------------------------------------------------------------------------------


@pytest.fixture
async def people(app: FastAPI, engine: AsyncEngine) -> AsyncIterator[dict[str, Any]]:  # noqa: F811
    """Alice owns two projects, Bob owns one; Carl is a global admin who owns nothing."""
    ids = {
        name: await make_user(engine, f"{name}@example.test", *roles)
        for name, roles in (("alice", ()), ("bob", ()), ("carl", (("admin", "global"),)))
    }
    projects_of = {
        "alice_main": await add_project(engine, ids["alice"], "Alice Main"),
        "alice_side": await add_project(engine, ids["alice"], "Alice Side"),
        "bob_main": await add_project(engine, ids["bob"], "Bob Main"),
    }
    tasks = {
        "alice_main": await add_task(engine, projects_of["alice_main"], ids["alice"], "failed", "A gagal"),
        "alice_side": await add_task(engine, projects_of["alice_side"], ids["alice"], "running", "A jalan"),
        "bob_main": await add_task(engine, projects_of["bob_main"], ids["bob"], "failed", "B gagal"),
    }
    clients: dict[str, httpx.AsyncClient] = {}
    world = {"ids": ids, "projects": projects_of, "tasks": tasks, "clients": clients}

    async def client(name: str) -> httpx.AsyncClient:
        if name not in clients:  # signed in on first use (grants are read on every request)
            clients[name] = await signed_in(app, f"{name}@example.test", PASSWORD)
        return clients[name]

    world["client"] = client
    yield world
    for open_client in clients.values():
        await open_client.aclose()


async def seen(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get(DASHBOARD)
    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    return {
        "metrics": data["metrics"],
        "projects": {project["id"] for project in data["recent_projects"]},
        "tasks": {task["id"] for task in data["recent_tasks"]},
        "queue": {task["id"] for task in data["failed_queue"]},
        "queue_total": body["meta"]["failed_queue_total"],
    }


async def test_other_users_projects_and_tasks_never_count(people: dict[str, Any]) -> None:
    alice = await seen(await people["client"]("alice"))
    assert alice["projects"] == {str(people["projects"]["alice_main"]), str(people["projects"]["alice_side"])}
    assert alice["tasks"] == {str(people["tasks"]["alice_main"]), str(people["tasks"]["alice_side"])}
    assert alice["queue"] == {str(people["tasks"]["alice_main"])}
    assert (alice["metrics"]["failed_tasks"], alice["metrics"]["running_tasks"], alice["queue_total"]) == (
        1,
        1,
        1,
    )
    bob = await seen(await people["client"]("bob"))
    assert bob["projects"] == {str(people["projects"]["bob_main"])}
    assert bob["tasks"] == bob["queue"] == {str(people["tasks"]["bob_main"])}
    assert (bob["metrics"]["active_projects"], bob["metrics"]["running_tasks"]) == (1, 0)


async def test_a_project_grant_counts_that_project(people: dict[str, Any], engine: AsyncEngine) -> None:
    main = people["projects"]["alice_main"]
    await grant(engine, people["ids"]["bob"], "developer", f"project:{main}")
    # Bob's own task in Alice's project counts for both (tasks join projects, never their creator).
    bobs_task = await add_task(engine, main, people["ids"]["bob"], "waiting_approval", "B di proyek A")
    bob = await seen(await people["client"]("bob"))
    assert bob["projects"] == {str(main), str(people["projects"]["bob_main"])}
    assert bob["tasks"] == {
        str(people["tasks"]["alice_main"]),
        str(people["tasks"]["bob_main"]),
        str(bobs_task),
    }
    assert str(people["tasks"]["alice_side"]) not in bob["tasks"]
    assert (bob["metrics"]["failed_tasks"], bob["metrics"]["waiting_approval"], bob["queue_total"]) == (
        2,
        1,
        3,
    )
    alice = await seen(await people["client"]("alice"))
    assert str(bobs_task) in alice["queue"] and alice["metrics"]["waiting_approval"] == 1


async def test_a_global_role_does_not_widen_the_scope(people: dict[str, Any]) -> None:
    carl = await seen(await people["client"]("carl"))
    assert carl["metrics"] == dict.fromkeys(carl["metrics"], 0)
    assert (carl["projects"], carl["tasks"], carl["queue"], carl["queue_total"]) == (set(), set(), set(), 0)


async def test_a_grant_on_a_deleted_project_counts_nothing(
    people: dict[str, Any], engine: AsyncEngine
) -> None:
    main = people["projects"]["alice_main"]
    await grant(engine, people["ids"]["carl"], "readonly", f"project:{main}")
    assert (await seen(await people["client"]("carl")))["metrics"]["failed_tasks"] == 1  # the grant works
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE projects SET status = 'deleted' WHERE id = :id"), {"id": main})
    carl = await seen(await people["client"]("carl"))
    assert carl["metrics"] == dict.fromkeys(carl["metrics"], 0)
    assert (carl["projects"], carl["tasks"], carl["queue"]) == (set(), set(), set())
    alice = await seen(await people["client"]("alice"))  # nor for its owner
    assert str(people["tasks"]["alice_main"]) not in alice["tasks"] | alice["queue"]


def test_every_role_that_sees_a_project_may_read_its_tasks() -> None:
    """AG-13 I1: the dashboard lists the tasks of every visible project without checking task:read, which is
    right only while every role holds it. A role without task:read must make this test fail first."""
    from app.core.rbac import ROLE_PERMISSIONS

    assert all("task:read" in permissions for permissions in ROLE_PERMISSIONS.values())


async def test_recent_projects_are_the_first_page_of_the_project_list(
    people: dict[str, Any], engine: AsyncEngine
) -> None:
    """The dashboard reads its recent projects in its own snapshot: the rows and their order must still be the
    ones GET /projects?page_size=5 returns, for an owner and for a granted user."""
    await grant(engine, people["ids"]["bob"], "readonly", f"project:{people['projects']['alice_main']}")
    for name in ("alice", "bob"):
        client = await people["client"](name)
        listed = (await client.get("/api/v1/projects", params={"page_size": 5})).json()["data"]
        shown = (await client.get(DASHBOARD)).json()["data"]["recent_projects"]
        assert shown == listed
