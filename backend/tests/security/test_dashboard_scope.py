"""Dashboard scope (WP-2.1e; AC-01, T6): the dashboard adds up every project a user can see, so it must see
exactly what ``GET /projects`` sees. One visibility rule serves both."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.rbac import RoleGrant
from app.core.redaction import Redactor
from app.gitops.adapter import GitAdapter
from app.models.projects import Project
from app.services import projects
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    clean_state,
    make_user,
)


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
