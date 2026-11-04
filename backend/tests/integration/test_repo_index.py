"""Repository index (WP-2.1d; DATA_MODEL ``repo_index_entries``, FR-023) on real PostgreSQL, Redis and git."""

from __future__ import annotations

import uuid
from pathlib import Path

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


async def test_symbol_patterns_find_every_language_through_git(tmp_path: Path) -> None:
    """``SYMBOL_PATTERNS`` (ERE, run by git) and ``classify`` (Python) agree on every sample definition."""
    from app.gitops.adapter import Author, GitAdapter, GitTarget
    from app.workspace.index import SYMBOL_PATTERNS, classify
    from tests.unit.test_repo_index import DEFINITIONS

    git = GitAdapter()
    root = tmp_path / "repo"
    root.mkdir()
    await git.init(root, "main")
    by_file: dict[str, list[str]] = {}
    for path, sample, _ in DEFINITIONS:
        by_file.setdefault(path, []).append(sample)
    for path, lines in by_file.items():
        (root / path).write_text("\n".join(["// filler", *lines, ""]))
    (root / "not_js.py").write_text("function f() {}\n")
    target = GitTarget(root)
    sha = await git.commit(target, "samples", Author("Dev", "dev@example.test"))
    assert sha is not None
    result = await git.grep(target, list(SYMBOL_PATTERNS), commit=sha, fixed=False, ignore_case=False,
                            only_matching=True, timeout_s=30, max_output=1 << 20)  # fmt: skip
    found = {(m.path, classify(m.path, m.text)) for m in result.matches}
    found.discard(("not_js.py", None))
    assert found == {(path, expected) for path, _, expected in DEFINITIONS}
