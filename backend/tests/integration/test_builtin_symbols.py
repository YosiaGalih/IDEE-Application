"""`repo.search_symbol` through the real gateway, over the project index port (spec 3.1d2b Goal 3, Lead
decision 3.1d2b #7)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.projects import RepoIndexEntry, Repository
from app.services.repo_index import RepoSymbolIndex
from app.tools.base import SymbolRow, SymbolRows
from app.tools.builtin import symbols
from app.workspace.fs import TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway
from tests.worktree_fakes import task_worktree

clean = denials.clean
sessions = denials.sessions
scope = denials.scope
COMMIT = "c" * 40


class FakeIndex:
    def __init__(self, rows: SymbolRows) -> None:
        self.rows = rows
        self.asked: list[tuple[uuid.UUID, str, str | None]] = []

    async def candidates(self, project_id: uuid.UUID, name: str, kind: str | None) -> SymbolRows:
        self.asked.append((project_id, name, kind))
        return self.rows


async def run(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, index: object,
              **raw: object) -> tuple[str, str]:  # fmt: skip
    built = gateway(sessions, *symbols.specs(index))  # type: ignore[arg-type]
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["repo.search_symbol"],
                  workspace=ws)  # fmt: skip
    result = await built.execute(ctx, "repo.search_symbol", raw)
    return result.status, result.content


@pytest.fixture
async def ws(tmp_path: Path) -> TaskWorkspace:
    return await task_worktree(
        tmp_path, {"src/calc.py": "def add(): pass\n", "private/keys.py": "def add_key(): pass\n"},
        repo_agentignore="private/\n",
    )  # fmt: skip


async def test_hits_name_the_index_commit_and_are_wrapped(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    index = FakeIndex(SymbolRows([SymbolRow("src/calc.py", 1, "add", "function")], False, COMMIT, False))
    status, out = await run(sessions, scope, ws, index, name="add", kind="function")
    assert status == "ok" and out.startswith('<untrusted_content source="repo.search_symbol">'), out
    assert f"index: {COMMIT}" in out and "src/calc.py:1: function add" in out, out
    assert index.asked == [(scope.project_id, "add", "function")]
    # silent-failure 3.1d2b M3: an index of another commit than the worktree is said
    assert "the worktree is at" in out and "line numbers may differ" in out, out


async def test_a_project_without_an_index_says_so(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    status, out = await run(sessions, scope, ws, FakeIndex(SymbolRows([], False, None, None)), name="add")
    assert status == "ok" and "[no symbol index yet" in out, out


async def test_the_production_index_reads_the_project_rows(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    async with sessions() as session:
        session.add(
            Repository(project_id=scope.project_id, provider="local", branch="main", index_commit=COMMIT)
        )
        session.add_all([
            RepoIndexEntry(project_id=scope.project_id, path="src/calc.py", kind="symbol", symbol="add",
                           symbol_kind="function", line=1, commit=COMMIT),
            RepoIndexEntry(project_id=scope.project_id, path="src/calc.py", kind="symbol", symbol="Adder",
                           symbol_kind="class", line=3, commit=COMMIT),
            RepoIndexEntry(project_id=scope.project_id, path="private/keys.py", kind="symbol",
                           symbol="add_key", symbol_kind="function", line=1, commit=COMMIT),
        ])  # fmt: skip
        await session.commit()
    status, out = await run(sessions, scope, ws, RepoSymbolIndex(sessions), name="add", kind="function")
    assert status == "ok", out
    assert f"index: {COMMIT}" in out and "src/calc.py:1: function add" in out, out
    assert "Adder" not in out and "add_key" not in out, out


async def test_an_index_of_the_worktree_head_needs_no_warning(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    from app.gitops.adapter import GitAdapter
    from app.tools.builtin._common import git_target

    head = await GitAdapter().current_commit(git_target(ws))
    index = FakeIndex(SymbolRows([SymbolRow("src/calc.py", 1, "add", "function")], False, head, False))
    _, out = await run(sessions, scope, ws, index, name="add")
    assert "src/calc.py:1: function add" in out and "line numbers may differ" not in out, out
    # Uncommitted edits are never in the index, and that is said on every answer (no per-call status:
    # whether the worktree is dirty would also tell about hidden files, AG-13 3.1d2b INFO-6).
    assert "uncommitted edits are not indexed" in out, out


async def test_hits_not_checked_against_the_policy_are_said_apart(
    sessions: async_sessionmaker[AsyncSession],
    scope: Scope,
    ws: TaskWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silent-failure 3.1d2b L1: a hit left out because the policy check stopped is not "may not read"."""
    from app.tools.builtin import _common

    monkeypatch.setattr(_common, "MAX_NAMES", 1)
    rows = [SymbolRow("src/calc.py", 1, "add", "function"), SymbolRow("README.md", 1, "add_doc", "function")]
    _, out = await run(sessions, scope, ws, FakeIndex(SymbolRows(rows, False, COMMIT, False)), name="add")
    assert "src/calc.py:1: function add" in out and "add_doc" not in out, out
    assert "[1 hits not checked against the agent policy (too many or too costly): not shown]" in out, out
    assert "agents may not read them" not in out, out
