"""Every (from, to) pair through transition(): ALLOWED pairs succeed, the rest are INVALID_TRANSITION.

Location and scope per TEST_STRATEGY (AC-12 row): state machine exercised end to end, not only the dict.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task
from app.workflow.states import ALLOWED, Actor, TaskStatus, transition
from tests.conftest import reset_data

ACTOR = Actor("system", "matrix")
AUDIT = AuditWriter(Redactor())
PAIRS = [(source, target) for source in TaskStatus for target in TaskStatus]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def project(engine: AsyncEngine) -> tuple[uuid.UUID, uuid.UUID]:
    await reset_data(engine)
    async with make_sessionmaker(engine)() as session:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.commit()
        return project.id, owner.id


async def task_in(session: AsyncSession, project: tuple[uuid.UUID, uuid.UUID], status: TaskStatus) -> Task:
    task = Task(project_id=project[0], title="t", prompt="p", workflow_type="create", created_by=project[1])
    session.add(task)
    await session.flush()
    # Test setup only: place the task in ``status`` directly, past the writer backstop.
    await session.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
    await session.execute(
        text("UPDATE tasks SET status = :s WHERE id = :id"), {"s": status.value, "id": task.id}
    )
    await session.execute(text("SELECT set_config('kk.status_writer', '', true)"))
    await session.commit()
    await session.refresh(task)
    return task


@pytest.mark.parametrize(("source", "target"), PAIRS, ids=[f"{s.value}->{t.value}" for s, t in PAIRS])
async def test_transition_obeys_allowed(
    session: AsyncSession, project: tuple[uuid.UUID, uuid.UUID], source: TaskStatus, target: TaskStatus
) -> None:
    task = await task_in(session, project, source)
    if target in ALLOWED[source]:
        await transition(session, task, target, reason=None, actor=ACTOR, audit=AUDIT)
        await session.commit()
        assert task.status == target.value
    else:
        with pytest.raises(AppError) as caught:
            await transition(session, task, target, reason=None, actor=ACTOR, audit=AUDIT)
        assert caught.value.code == "INVALID_TRANSITION"
