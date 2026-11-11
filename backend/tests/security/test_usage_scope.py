"""``GET /usage`` scope (AC-01, SECURITY_MODEL §8, T6; spec WP-3.1f Keputusan Lead #7): another project's
usage does not exist (404, never 403), and ``usage:read_all`` does not widen the sum in v1."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy import func, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import make_sessionmaker
from app.models.projects import Project
from tests.integration.test_dashboard_api import add_project, add_task
from tests.integration.test_projects import OWNER
from tests.integration.test_usage_api import USAGE, record
from tests.security.test_cross_project import (  # noqa: F401 - fixtures shared with the cross-project matrix
    app,
    clean_state,
    world,
)

EMAILS = {"stranger": "eve@example.test", "admin": "adi@example.test"}  # the world fixture's users


async def _owner_id(engine: AsyncEngine) -> uuid.UUID:
    async with make_sessionmaker(engine)() as session:
        return (
            await session.execute(text("SELECT id FROM users WHERE email = :e"), {"e": OWNER[0]})
        ).scalar_one()


async def _usage_world(world: dict[str, Any], engine: AsyncEngine) -> dict[str, uuid.UUID]:  # noqa: F811
    project = uuid.UUID(world["project"]["id"])
    task = await add_task(engine, project, await _owner_id(engine), "draft", "Rahasia")
    await record(engine, project, task_id=task)
    await record(engine, project)
    return {"project": project, "task": task}


async def test_usage_of_another_project_is_404(world: dict[str, Any], engine: AsyncEngine) -> None:  # noqa: F811
    ids = await _usage_world(world, engine)
    for who in ("stranger", "admin"):
        client: httpx.AsyncClient = world[who]
        hidden = await client.get(USAGE, params={"project_id": str(ids["project"])})
        missing = await client.get(USAGE, params={"project_id": str(uuid.uuid4())})
        assert hidden.status_code == 404, hidden.text
        assert hidden.json()["error"]["code"] == "NOT_FOUND"
        assert (missing.status_code, missing.json()["error"]) == (404, hidden.json()["error"])
        assert "Secret Plan" not in hidden.text


async def test_usage_of_a_task_in_another_project_is_404(world: dict[str, Any], engine: AsyncEngine) -> None:  # noqa: F811
    ids = await _usage_world(world, engine)
    for who in ("stranger", "admin"):
        client: httpx.AsyncClient = world[who]
        hidden = await client.get(USAGE, params={"task_id": str(ids["task"])})
        missing = await client.get(USAGE, params={"task_id": str(uuid.uuid4())})
        assert hidden.status_code == 404, hidden.text
        assert (missing.status_code, missing.json()["error"]) == (404, hidden.json()["error"])
        # Naming a project of their own does not open someone else's task either.
        own = await add_project(engine, await _user_id(engine, EMAILS[who]), "Milikku")
        mixed = await client.get(USAGE, params={"task_id": str(ids["task"]), "project_id": str(own)})
        assert (mixed.status_code, mixed.json()["error"]) == (404, hidden.json()["error"])


async def _user_id(engine: AsyncEngine, email: str) -> uuid.UUID:
    async with make_sessionmaker(engine)() as session:
        return (
            await session.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
        ).scalar_one()


async def test_task_and_project_that_do_not_match_is_404(world: dict[str, Any], engine: AsyncEngine) -> None:  # noqa: F811
    ids = await _usage_world(world, engine)
    second = await add_project(engine, await _owner_id(engine), "Kedua")
    owner: httpx.AsyncClient = world["owner"]
    response = await owner.get(USAGE, params={"task_id": str(ids["task"]), "project_id": str(second)})
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    matching = await owner.get(USAGE, params={"task_id": str(ids["task"]), "project_id": str(ids["project"])})
    assert matching.status_code == 200, matching.text
    assert matching.json()["data"]["totals"]["input_tokens"] == 100


async def test_usage_read_all_does_not_widen(world: dict[str, Any], engine: AsyncEngine) -> None:  # noqa: F811
    """The global admin holds usage:read_all; in v1 it still sees only the projects it can see (AC-01)."""
    await _usage_world(world, engine)
    admin: httpx.AsyncClient = world["admin"]
    body = (await admin.get(USAGE)).json()
    assert body["data"]["totals"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
        "actual_cost": None,
        "cost_status": "estimated",
    }
    assert (body["data"]["series"], body["data"]["records"], body["meta"]["total"]) == ([], [], 0)
    owner_view = (await world["owner"].get(USAGE)).json()
    assert (
        owner_view["data"]["totals"]["input_tokens"] == 200
    )  # the rows exist; they are just not the admin's


async def test_deleted_project_usage_is_gone(world: dict[str, Any], engine: AsyncEngine) -> None:  # noqa: F811
    ids = await _usage_world(world, engine)
    async with make_sessionmaker(engine)() as session:
        await session.execute(
            update(Project)
            .where(Project.id == ids["project"])
            .values(status="deleted", deleted_at=func.now())
        )
        await session.commit()
    owner: httpx.AsyncClient = world["owner"]
    assert (await owner.get(USAGE, params={"project_id": str(ids["project"])})).status_code == 404
    assert (await owner.get(USAGE, params={"task_id": str(ids["task"])})).status_code == 404
    body = (await owner.get(USAGE)).json()
    assert body["data"]["totals"]["input_tokens"] == 0
    assert body["data"]["records"] == []


async def test_project_scoped_reader_reads_usage(world: dict[str, Any], engine: AsyncEngine) -> None:  # noqa: F811
    ids = await _usage_world(world, engine)
    reader: httpx.AsyncClient = world["reader"]
    by_project = await reader.get(USAGE, params={"project_id": str(ids["project"])})
    assert by_project.status_code == 200, by_project.text
    assert by_project.json()["data"]["totals"]["input_tokens"] == 200
    by_task = await reader.get(USAGE, params={"task_id": str(ids["task"])})
    assert by_task.status_code == 200, by_task.text
    assert by_task.json()["data"]["totals"]["input_tokens"] == 100
    assert (await reader.get(USAGE)).json()["data"]["totals"]["input_tokens"] == 200
