"""``repositories`` (DATA_MODEL M04): one per project, enum CHECKs, cascade with the project."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.projects import Repository
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


async def test_one_repository_per_project(session: AsyncSession) -> None:
    project = await make_project(session)
    session.add(Repository(project_id=project.id, provider="local", branch="main"))
    await session.flush()
    session.add(Repository(project_id=project.id, provider="local", branch="main"))
    with pytest.raises(IntegrityError, match="uq_repositories_project_id"):
        await session.flush()


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("provider", "ftp", "ck_repositories_provider"),
        ("sync_state", "syncing", "ck_repositories_sync_state"),
    ],
)
async def test_enum_checks(session: AsyncSession, field: str, value: str, constraint: str) -> None:
    project = await make_project(session)
    repo = Repository(project_id=project.id, provider="local", branch="main")
    setattr(repo, field, value)
    session.add(repo)
    with pytest.raises(IntegrityError, match=constraint):
        await session.flush()


async def test_defaults_and_cascade(session: AsyncSession) -> None:
    project = await make_project(session)
    session.add(Repository(project_id=project.id, provider="template", branch="main"))
    await session.flush()
    repo = await session.scalar(select(Repository).where(Repository.project_id == project.id))
    assert repo is not None
    await session.refresh(repo)
    assert repo.sync_state == "ready"
    await session.delete(project)
    await session.flush()
    assert await session.scalar(select(func.count()).select_from(Repository)) == 0
