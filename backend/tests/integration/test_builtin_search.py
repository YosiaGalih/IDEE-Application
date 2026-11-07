"""`repo.search_text` over the task worktree through the real gateway (spec 3.1d2a Goal 5)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tools.builtin import search
from app.workspace.fs import TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway
from tests.worktree_fakes import task_worktree, write

clean = denials.clean
sessions = denials.sessions
scope = denials.scope


async def find(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace,
               **raw: object) -> str:  # fmt: skip
    built = gateway(sessions, *search.specs())
    ctx = context(
        task_id=scope.task_id, project_id=scope.project_id, tools=["repo.search_text"], workspace=ws
    )
    result = await built.execute(ctx, "repo.search_text", raw)
    return f"{result.status}|{result.content}"


async def test_finds_text_in_tracked_and_untracked_files(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    ws = await task_worktree(tmp_path, {"src/a.py": "x = 1\ndef needle():\n    pass\n"})
    write(ws.root / "src" / "new.py", "# needle here too\n")  # untracked, written by the agent
    out = await find(sessions, scope, ws, query="needle")
    assert out.startswith('ok|<untrusted_content source="repo.search_text">\n')
    assert "src/a.py:2: def needle():" in out and "src/new.py:1: # needle here too" in out


async def test_regex_glob_and_limit(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    files = {f"m{i}.py": "hit\n" for i in range(5)} | {"doc.md": "hit\n"}
    ws = await task_worktree(tmp_path, files)
    regex = await find(sessions, scope, ws, query="h.t$", regex=True, glob="*.md")
    assert "doc.md:1: hit" in regex and ".py" not in regex
    limited = await find(sessions, scope, ws, query="hit", max_results=2)
    assert limited.count(": hit") == 2 and "[truncated" in limited


async def test_inputs_that_explode_are_refused(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    ws = await task_worktree(tmp_path, {"a.txt": "a\n"})
    for raw in ({"query": r"(a)\1", "regex": True}, {"query": "a{1,9}", "regex": True},
                {"query": "a", "glob": "*" * 20}, {"query": "a\nb"}):  # fmt: skip
        out = await find(sessions, scope, ws, **raw)
        assert out.startswith("error|"), (raw, out)


async def test_hits_left_out_for_size_are_said(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """Silent-failure M3: a match only in a file over the agent read limit is not "[no matches]"."""
    from app.workspace.fs import AGENT_READ_LIMIT

    ws = await task_worktree(tmp_path, {"src/a.py": "x\n"})
    write(ws.root / "dist2" / "bundle.js", "needle\n" + "z" * AGENT_READ_LIMIT)
    out = await find(sessions, scope, ws, query="needle")
    assert "[no matches]" not in out, out
    assert "[1 hits in files over the agent read limit were left out]" in out, out
