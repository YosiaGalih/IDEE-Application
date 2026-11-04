"""Repository index (WP-2.1d; DATA_MODEL ``repo_index_entries``, FR-023) on real PostgreSQL, Redis and git."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import make_sessionmaker
from app.models.projects import Project, RepoIndexEntry
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    clean_state,
    owner_id,
)


async def _project(engine: AsyncEngine, owner: uuid.UUID) -> uuid.UUID:
    project_id = uuid.uuid4()
    async with make_sessionmaker(engine)() as session:
        session.add(
            Project(
                id=project_id,
                owner_id=owner,
                name="Idx",
                slug=f"idx-{project_id.hex[:8]}",
                workspace_ref=str(project_id),
            )
        )
        await session.commit()
    return project_id


async def test_index_entries_go_with_their_project(engine: AsyncEngine, owner_id: uuid.UUID) -> None:  # noqa: F811
    project_id = await _project(engine, owner_id)
    async with make_sessionmaker(engine)() as session:
        session.add_all(
            [
                RepoIndexEntry(project_id=project_id, path="a.py", kind="file", commit="c" * 40),
                RepoIndexEntry(
                    project_id=project_id,
                    path="a.py",
                    kind="symbol",
                    symbol="main",
                    symbol_kind="function",
                    line=1,
                    language="python",
                    commit="c" * 40,
                ),
            ]
        )
        await session.commit()
    async with make_sessionmaker(engine)() as session:
        with pytest.raises(IntegrityError):
            session.add(RepoIndexEntry(project_id=project_id, path="b", kind="dir", commit="c" * 40))
            await session.commit()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
        left = await conn.scalar(
            text("SELECT count(*) FROM repo_index_entries WHERE project_id = :id"), {"id": project_id}
        )
    assert left == 0
