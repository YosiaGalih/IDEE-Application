"""Task tables (DATA_MODEL projects, conversations, tasks, task_dependencies, task_events; ADR-018)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task, TaskDependency, TaskEvent
from tests.conftest import reset_data


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


async def make_project(session: AsyncSession, slug: str = "demo") -> Project:
    owner = await session.scalar(select(User).where(User.email == "owner@example.test"))
    if owner is None:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
    project = Project(owner_id=owner.id, name="Demo", slug=slug, workspace_ref="pending")
    session.add(project)
    await session.flush()
    return project


async def make_task(session: AsyncSession, project: Project) -> Task:
    task = Task(
        project_id=project.id, title="t", prompt="p", workflow_type="create", created_by=project.owner_id
    )
    session.add(task)
    await session.flush()
    return task


async def test_defaults_match_data_model(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    await session.refresh(task)
    await session.refresh(project)
    assert (project.status, project.default_branch, project.settings, project.stack) == (
        "active",
        "main",
        {},
        {},
    )
    assert task.status == "draft"
    assert task.priority == "normal"
    assert (task.iteration, task.debug_attempts, task.event_seq, task.version) == (1, 0, 0, 1)
    assert (task.checkpoint, task.budget_limit) == ({}, {})
    assert (task.cancel_requested, task.pause_requested) == (False, False)
    assert task.current_step is None and task.started_at is None and task.completed_at is None


async def test_status_check_rejects_unknown_value(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    await session.commit()
    with pytest.raises(IntegrityError, match="ck_tasks_status"):
        await session.execute(text("UPDATE tasks SET status = 'bogus' WHERE id = :id"), {"id": task.id})


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE tasks SET workflow_type = 'deploy' WHERE id = :id", "ck_tasks_workflow_type"),
        ("UPDATE tasks SET priority = 'urgent' WHERE id = :id", "ck_tasks_priority"),
        ("UPDATE tasks SET current_step = 'teleport' WHERE id = :id", "ck_tasks_current_step"),
    ],
)
async def test_task_enums_are_checked(session: AsyncSession, statement: str, constraint: str) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    await session.commit()
    with pytest.raises(IntegrityError, match=constraint):
        await session.execute(text(statement), {"id": task.id})


async def test_project_slug_unique_per_owner(session: AsyncSession) -> None:
    await make_project(session, slug="same")
    with pytest.raises(IntegrityError, match="uq_projects_owner_id_slug"):
        await make_project(session, slug="same")


async def test_dependency_cannot_point_to_itself(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    session.add(TaskDependency(task_id=task.id, depends_on_task_id=task.id, dependency_type="blocks"))
    with pytest.raises(IntegrityError, match="ck_task_dependencies_not_self"):
        await session.flush()


async def test_event_seq_unique_per_task(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    session.add_all([TaskEvent(task_id=task.id, seq=1, type="x", payload={}) for _ in range(2)])
    with pytest.raises(IntegrityError, match="uq_task_events_task_id_seq"):
        await session.flush()


async def test_deleting_project_cascades_tasks_and_events(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    child = Task(
        project_id=project.id,
        parent_task_id=task.id,
        title="c",
        prompt="p",
        workflow_type="debug",
        created_by=project.owner_id,
    )
    session.add(child)
    await session.flush()
    session.add(TaskEvent(task_id=task.id, seq=1, type="task.log", payload={"level": "info"}))
    session.add(TaskDependency(task_id=child.id, depends_on_task_id=task.id, dependency_type="blocks"))
    await session.commit()
    await session.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project.id})
    await session.commit()
    for model in (Task, TaskEvent, TaskDependency):
        assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_owner_with_projects_cannot_be_deleted(session: AsyncSession) -> None:
    project = await make_project(session)
    await session.commit()
    with pytest.raises(IntegrityError, match="fk_projects_owner_id_users"):
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": project.owner_id})


async def test_task_ids_are_application_generated(session: AsyncSession) -> None:
    project = await make_project(session)
    task = await make_task(session, project)
    assert isinstance(task.id, uuid.UUID)
