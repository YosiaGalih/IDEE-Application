"""Deleting a project through a ``project_delete`` approval (spec WP-3.1c Goal 5; FR-018, ADR-005,
WP-INT-04)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.models.approvals import Approval
from app.models.audit import AuditEvent
from app.models.identity import UserRole
from app.models.projects import Project
from app.models.tasks import Task
from app.services import project_delete, projects
from tests.integration.test_dashboard_api import add_task
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    owner_actor,
    owner_id,
    service_env,
    workspace_root,
)

NOW = datetime.now(UTC)


async def blank(env: projects.ProjectEnv, actor: projects.Actor, name: str = "Doomed") -> uuid.UUID:
    view = await projects.create_project(
        env, actor, name=name, description=None, source=projects.BlankSource(), default_branch="main"
    )
    return view.project.id


async def load[T](engine: AsyncEngine, model: type[T], row_id: uuid.UUID) -> T:
    async with make_sessionmaker(engine)() as session:
        found = await session.get(model, row_id)
        assert found is not None
        return found


async def gone(engine: AsyncEngine, model: type, row_id: uuid.UUID) -> bool:
    async with make_sessionmaker(engine)() as session:
        return await session.get(model, row_id) is None


async def approve(engine: AsyncEngine, approval_id: uuid.UUID, status: str = "approved") -> None:
    async with engine.begin() as conn:
        await conn.execute(update(Approval).where(Approval.id == approval_id).values(status=status))


async def audit_actions(engine: AsyncEngine, project_id: uuid.UUID) -> list[str]:
    async with make_sessionmaker(engine)() as session:
        query = select(AuditEvent.action).where(AuditEvent.project_id == project_id).order_by(AuditEvent.id)
        return list((await session.scalars(query)).all())


@pytest.fixture
def env(app: FastAPI, workspace_root: Path, clean_state: None) -> projects.ProjectEnv:  # noqa: F811
    return service_env(app, workspace_root)


@pytest.fixture
def handler(env: projects.ProjectEnv) -> project_delete.ProjectDeleteHandler:
    return project_delete.ProjectDeleteHandler(sessions=env.sessions, audit=env.audit)


@pytest.mark.parametrize("start", ["active", "archived"])
async def test_request_moves_to_pending_and_records_previous_status(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    start: str,
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    if start == "archived":
        await projects.set_archived(env, actor, project_id, archived=True)
    approval, view = await project_delete.request_delete(env, actor, project_id)
    assert view.project.status == "deleting_pending_approval"
    assert approval.payload == {"project_id": str(project_id), "name": "Doomed", "previous_status": start}
    assert (approval.status, approval.risk_level, approval.requested_by) == ("pending", 3, f"user:{owner_id}")
    assert "project.delete_requested" in await audit_actions(engine, project_id)


async def test_repeat_request_returns_the_open_approval(
    env: projects.ProjectEnv,
    owner_id: uuid.UUID,  # noqa: F811
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    first, _ = await project_delete.request_delete(env, actor, project_id)
    second, _ = await project_delete.request_delete(env, actor, project_id)
    assert first.id == second.id


async def test_concurrent_deletes_make_one_approval(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    results = await asyncio.gather(*(project_delete.request_delete(env, actor, project_id) for _ in range(3)))
    assert len({approval.id for approval, _ in results}) == 1
    async with make_sessionmaker(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(Approval)) == 1


async def test_rename_supersedes_the_pending_delete(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    old, _ = await project_delete.request_delete(env, actor, project_id)
    await projects.update_project(
        env, actor, project_id, projects.ProjectChanges(fields=frozenset({"description"}), description="d")
    )
    assert (await load(engine, Approval, old.id)).status == "pending"  # not in the payload: nothing changes
    await projects.update_project(
        env, actor, project_id, projects.ProjectChanges(fields=frozenset({"name"}), name="Renamed")
    )
    assert (await load(engine, Approval, old.id)).status == "superseded"
    async with make_sessionmaker(engine)() as session:
        new = await session.scalar(select(Approval).where(Approval.status == "pending"))
    assert new is not None and new.payload["name"] == "Renamed"
    assert new.payload_hash != old.payload_hash


async def test_approved_cancels_open_tasks_and_deletes(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    running = await add_task(engine, project_id, owner_id, "running", "Run")
    waiting = await add_task(engine, project_id, owner_id, "waiting_approval", "Wait")
    done = await add_task(engine, project_id, owner_id, "completed", "Done")
    approval, _ = await project_delete.request_delete(env, actor, project_id)
    await approve(engine, approval.id)
    await handler.approved(approval.id, now=NOW)
    for task_id, expected in ((running, "cancelled"), (waiting, "cancelled"), (done, "completed")):
        assert (await load(engine, Task, task_id)).status == expected
    project = await load(engine, Project, project_id)
    assert (project.status, project.deleted_at) == ("deleted", NOW)
    assert (await load(engine, Approval, approval.id)).status == "consumed"
    actions = await audit_actions(engine, project_id)
    assert "approval.consumed" in actions and "project.deleted" in actions


async def test_handler_is_idempotent(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    approval, _ = await project_delete.request_delete(env, actor, project_id)
    await approve(engine, approval.id)
    await handler.approved(approval.id, now=NOW)
    await handler.approved(approval.id, now=NOW + timedelta(minutes=1))
    assert (await audit_actions(engine, project_id)).count("project.deleted") == 1


async def test_approved_but_expired_before_consume_restores(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    approval, _ = await project_delete.request_delete(env, actor, project_id)
    await approve(engine, approval.id)
    await handler.approved(approval.id, now=NOW + timedelta(days=8))
    assert (await load(engine, Approval, approval.id)).status == "expired"
    assert (await load(engine, Project, project_id)).status == "active"


@pytest.mark.parametrize(
    ("status", "path", "action"),
    [
        ("rejected", "rejected", "project.delete_rejected"),
        ("expired", "expired", "project.delete_expired"),
    ],
)
async def test_rejected_and_expired_restore_previous_status(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,
    status: str,
    path: str,
    action: str,
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    await projects.set_archived(env, actor, project_id, archived=True)
    approval, _ = await project_delete.request_delete(env, actor, project_id)
    await approve(engine, approval.id, status)
    await getattr(handler, path)(approval.id, now=NOW)
    assert (await load(engine, Project, project_id)).status == "archived"
    assert action in await audit_actions(engine, project_id)


async def test_deleted_project_cannot_be_requested_again(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    approval, _ = await project_delete.request_delete(env, actor, project_id)
    await approve(engine, approval.id)
    await handler.approved(approval.id, now=NOW)
    with pytest.raises(AppError) as raised:
        await project_delete.request_delete(env, actor, project_id)
    assert raised.value.http_status == 404


async def deleted_project(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,
) -> uuid.UUID:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    async with make_sessionmaker(engine)() as session:
        session.add(UserRole(user_id=owner_id, role_name="readonly", scope=f"project:{project_id}"))
        await session.commit()
    approval, _ = await project_delete.request_delete(env, actor, project_id)
    await approve(engine, approval.id)
    await handler.approved(approval.id, now=NOW)
    return project_id


async def test_purge_waits_seven_days(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,
) -> None:
    project_id = await deleted_project(env, engine, owner_id, handler)
    folder = workspace_root / str(project_id)
    assert folder.is_dir()
    early = NOW + timedelta(days=6, hours=23)
    assert (
        await project_delete.purge_deleted(
            sessions=env.sessions, audit=env.audit, workspace_root=workspace_root, now=early
        )
        == []
    )
    assert folder.is_dir()
    purged = await project_delete.purge_deleted(
        sessions=env.sessions, audit=env.audit, workspace_root=workspace_root, now=NOW + timedelta(days=7)
    )
    assert purged == [project_id]
    assert not folder.exists()
    assert await gone(engine, Project, project_id)
    async with make_sessionmaker(engine)() as session:
        grants = await session.scalar(
            select(func.count()).select_from(UserRole).where(UserRole.scope == f"project:{project_id}")
        )
    assert grants == 0
    assert "project.purged" in await audit_actions(engine, project_id)  # the trail outlives the project


async def test_purge_does_not_follow_symlinks(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,
    tmp_path: Path,
) -> None:
    project_id = await deleted_project(env, engine, owner_id, handler)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (workspace_root / str(project_id) / "escape").symlink_to(outside)
    await project_delete.purge_deleted(
        sessions=env.sessions, audit=env.audit, workspace_root=workspace_root, now=NOW + timedelta(days=8)
    )
    assert not (workspace_root / str(project_id)).exists()
    assert (outside / "keep.txt").read_text() == "keep"


async def test_purge_of_a_symlinked_project_folder_keeps_the_target(
    env: projects.ProjectEnv,
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,
    tmp_path: Path,
) -> None:
    project_id = await deleted_project(env, engine, owner_id, handler)
    folder = workspace_root / str(project_id)
    moved = tmp_path / "elsewhere"
    folder.rename(moved)
    folder.symlink_to(moved)
    await project_delete.purge_deleted(
        sessions=env.sessions, audit=env.audit, workspace_root=workspace_root, now=NOW + timedelta(days=8)
    )
    assert not folder.is_symlink() and not folder.exists()
    assert moved.is_dir()
