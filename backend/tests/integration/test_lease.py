"""Task lease (ARCHITECTURE §5.3, NFR-05): one execution per task, MAX_ACTIVE_TASKS enforced at claim."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.db import make_sessionmaker
from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task
from app.workflow.lease import claim_lease, release_lease, renew_lease
from tests.conftest import reset_data


@pytest.fixture
async def task_ids(engine: AsyncEngine) -> list[uuid.UUID]:
    await reset_data(engine)
    async with make_sessionmaker(engine)() as session:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.flush()
        tasks = [
            Task(
                project_id=project.id, title=f"t{i}", prompt="p", workflow_type="create", created_by=owner.id
            )
            for i in range(3)
        ]
        session.add_all(tasks)
        await session.commit()
        return [t.id for t in tasks]


async def claim(engine: AsyncEngine, task_id: uuid.UUID, worker: str, max_active: int = 2) -> bool:
    async with make_sessionmaker(engine)() as session:
        won = await claim_lease(session, task_id, worker, max_active=max_active)
        await session.commit()
        return won


async def owner_of(session: AsyncSession, task_id: uuid.UUID) -> str | None:
    return await session.scalar(
        select(Task.lease_owner).where(Task.id == task_id).execution_options(populate_existing=True)
    )


async def test_claim_blocks_others_while_live(
    engine: AsyncEngine, session: AsyncSession, task_ids: list[uuid.UUID]
) -> None:
    assert await claim(engine, task_ids[0], "w1") is True
    assert await claim(engine, task_ids[0], "w2") is False
    assert await owner_of(session, task_ids[0]) == "w1"


async def test_expired_lease_can_be_claimed(
    engine: AsyncEngine, session: AsyncSession, task_ids: list[uuid.UUID]
) -> None:
    await claim(engine, task_ids[0], "w1")
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE tasks SET lease_expires_at = now() - interval '1 second'"))
    assert await claim(engine, task_ids[0], "w2") is True
    assert await owner_of(session, task_ids[0]) == "w2"


async def test_renew_and_release_only_by_owner(engine: AsyncEngine, task_ids: list[uuid.UUID]) -> None:
    await claim(engine, task_ids[0], "w1")
    sessions = make_sessionmaker(engine)
    async with sessions() as session:
        assert await renew_lease(session, task_ids[0], "w2") is False
        assert await renew_lease(session, task_ids[0], "w1") is True
        assert await release_lease(session, task_ids[0], "w2") is False
        await session.commit()
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE tasks SET lease_expires_at = now() - interval '1 second'"))
    async with sessions() as session:
        assert await renew_lease(session, task_ids[0], "w1") is False  # a lost lease cannot be revived
        assert await release_lease(session, task_ids[0], "w1") is True
        await session.commit()
        task = await session.get(Task, task_ids[0])
        assert task is not None and task.lease_owner is None and task.lease_expires_at is None


async def test_parallel_claims_one_winner(engine: AsyncEngine, task_ids: list[uuid.UUID]) -> None:
    results = await asyncio.gather(*(claim(engine, task_ids[0], f"w{i}") for i in range(6)))
    assert sorted(results) == [False] * 5 + [True]


async def test_max_active_holds_under_parallel_claims(engine: AsyncEngine, task_ids: list[uuid.UUID]) -> None:
    """Deterministic overlap: B claims while A's claim is uncommitted; B must wait for A, then see it."""
    async with make_sessionmaker(engine)() as first:
        assert await claim_lease(first, task_ids[0], "w1", max_active=1) is True
        second = asyncio.create_task(claim(engine, task_ids[1], "w2", max_active=1))
        await asyncio.sleep(0.3)
        assert not second.done()  # serialised behind the first claim
        await first.commit()
    assert await second is False


async def test_worker_id_is_validated(session: AsyncSession, task_ids: list[uuid.UUID]) -> None:
    with pytest.raises(ValueError, match="worker"):
        await claim_lease(session, task_ids[0], "", max_active=2)
    with pytest.raises(ValueError, match="worker"):
        await claim_lease(session, task_ids[0], "w" * 121, max_active=2)
