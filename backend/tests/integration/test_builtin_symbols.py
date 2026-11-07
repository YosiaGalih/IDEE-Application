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
from app.workspace.fs import AGENT_READ_LIMIT, TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway
from tests.worktree_fakes import task_worktree, write

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


async def test_a_project_without_an_index_says_so(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    status, out = await run(sessions, scope, ws, FakeIndex(SymbolRows([], False, None, None)), name="add")
    assert status == "ok" and "[no symbol index yet" in out, out


async def test_hits_the_agent_may_not_see_are_left_out_and_counted(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    """Review Focus 3.1d2b #4: hidden paths never reach the model, not even as a symbol's file name."""
    write(ws.root / "big.py", "x" * (AGENT_READ_LIMIT + 1))
    rows = [
        SymbolRow("src/calc.py", 1, "add", "function"),
        SymbolRow("private/keys.py", 1, "add_key", "function"),  # repo/'s .agentignore
        SymbolRow("config/prod.env", 1, "add_env", "function"),  # a secret name
        SymbolRow(".kk/gen.py", 1, "add_gen", "function"),  # sandbox output
        SymbolRow("big.py", 1, "add_big", "function"),  # over the agent read limit in the worktree
        SymbolRow("gone.py", 1, "add_gone", "function"),  # no longer in the worktree
    ]
    status, out = await run(sessions, scope, ws, FakeIndex(SymbolRows(rows, True, COMMIT, True)), name="add")
    assert status == "ok", out
    assert "src/calc.py:1: function add" in out
    for hidden in ("add_key", "private", "add_env", "prod.env", "add_gen", ".kk", "add_big", "add_gone"):
        assert hidden not in out, (hidden, out)
    assert "[3 hits not shown: agents may not read them, or sandbox output]" in out, out
    assert "[1 hits in files over the agent read limit were left out]" in out, out
    assert "[1 hits in files no longer in the worktree were left out]" in out, out
    assert "[truncated: more symbols match; narrow the name or the kind]" in out, out
    assert "[the index itself stopped at a limit: some symbols are missing]" in out, out


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
