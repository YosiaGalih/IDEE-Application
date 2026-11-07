"""`repo.search_symbol` never lets the model learn about hidden paths (spec 3.1d2b Review Focus 4; T1, T5):
not their names, not how many hits they hold (AG-13 3.1d2b LOW-1: a count is an oracle on symbol names), not
what lies outside the worktree behind a symlinked folder (/code-review 3.1d2b spec); and every answer is
wrapped and redacted."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
WRAPPED = '<untrusted_content source="repo.search_symbol">'


class FakeIndex:
    def __init__(self, rows: SymbolRows) -> None:
        self.rows = rows

    async def candidates(self, project_id: uuid.UUID, name: str, kind: str | None) -> SymbolRows:
        return self.rows


async def run(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace,
              rows: SymbolRows) -> str:  # fmt: skip
    built = gateway(sessions, *symbols.specs(FakeIndex(rows)))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["repo.search_symbol"],
                  workspace=ws)  # fmt: skip
    result = await built.execute(ctx, "repo.search_symbol", {"name": "add"})
    assert result.status == "ok", result.content
    return result.content


@pytest.fixture
async def ws(tmp_path: Path) -> TaskWorkspace:
    return await task_worktree(
        tmp_path, {"src/calc.py": "def add(): pass\n", "private/keys.py": "def add_hidden(): pass\n"},
        repo_agentignore="private/\n",
    )  # fmt: skip


async def test_hidden_hits_leave_no_trace(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    hidden = [
        SymbolRow("private/keys.py", 1, "add_hidden", "function"),  # repo/'s .agentignore
        SymbolRow("config/prod.env", 1, "add_env", "function"),  # a secret name
        SymbolRow(".kk/gen.py", 1, "add_gen", "function"),  # sandbox output
    ]
    only_hidden = await run(sessions, scope, ws, SymbolRows(hidden, False, COMMIT, False))
    nothing = await run(sessions, scope, ws, SymbolRows([], False, COMMIT, False))
    assert only_hidden == nothing, (only_hidden, nothing)  # the same answer as no candidate at all
    for text in (
        "add_hidden",
        "private",
        "add_env",
        "prod.env",
        "add_gen",
        ".kk",
        "not shown",
        "may not read",
    ):
        assert text not in only_hidden, (text, only_hidden)
    visible = SymbolRow("src/calc.py", 1, "add", "function")
    mixed = await run(sessions, scope, ws, SymbolRows([visible, *hidden], False, COMMIT, False))
    alone = await run(sessions, scope, ws, SymbolRows([visible], False, COMMIT, False))
    assert mixed == alone, (mixed, alone)


async def test_a_folder_swapped_for_a_symlink_is_not_followed(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, tmp_path: Path
) -> None:
    """The index names repo/ paths; in the worktree a parent may be a symlink out of the root. What lies
    there (does it exist, is it large) must not show: such a hit is "no longer in the worktree"."""
    outside = tmp_path / "outside"
    write(outside / "big.py", "x" * (AGENT_READ_LIMIT + 1))
    (ws.root / "lib").symlink_to(outside)
    rows = SymbolRows([SymbolRow("lib/big.py", 1, "add_big", "function")], False, COMMIT, False)
    out = await run(sessions, scope, ws, rows)
    assert "[1 hits in files no longer in the worktree were left out]" in out, out
    assert "agent read limit" not in out and "add_big" not in out, out


async def test_every_answer_is_wrapped_and_redacted(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    token = "ghp_" + "a1B2c3D4e5" * 4  # the redaction pattern's shape, not a real token
    leaked = SymbolRows([SymbolRow("src/calc.py", 1, token, "function")], True, COMMIT, True)
    answers = [
        await run(sessions, scope, ws, SymbolRows([], False, None, None)),  # no index yet
        await run(sessions, scope, ws, SymbolRows([], False, COMMIT, False)),  # no hit
        await run(sessions, scope, ws, leaked),
    ]
    for out in answers:
        assert out.startswith(WRAPPED) and out.endswith("</untrusted_content>"), out
    assert token not in answers[2] and "[REDACTED]" in answers[2], answers[2]
