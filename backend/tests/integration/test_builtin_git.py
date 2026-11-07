"""`git.status`, `git.diff`, `git.log` over the task worktree through the real gateway (3.1d2a Goals 8-10)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gitops.adapter import GitAdapter
from app.tools.builtin import git as git_tools
from app.tools.builtin._common import git_target
from app.workspace.fs import TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway
from tests.worktree_fakes import ME, task_worktree, write

clean = denials.clean
sessions = denials.sessions
scope = denials.scope
TOOLS = ["git.status", "git.diff", "git.log"]


async def run(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, name: str,
              **raw: object) -> str:  # fmt: skip
    built = gateway(sessions, *git_tools.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=TOOLS, workspace=ws)
    result = await built.execute(ctx, name, raw)
    return f"{result.status}|{result.content}"


async def changed(tmp_path: Path) -> TaskWorkspace:
    ws = await task_worktree(tmp_path, {"src/a.py": "one\ntwo\n", "src/b.py": "b\n"})
    write(ws.root / "src" / "a.py", "one\nTWO\n")
    await GitAdapter().commit_all(git_target(ws), "agent change", ME)  # committed on the task branch
    write(ws.root / "src" / "b.py", "b\nmore\n")  # in the worktree only
    write(ws.root / "src" / "new.py", "print('new')\n")  # untracked
    return ws


async def test_status_lists_changes_and_branch(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    out = await run(sessions, scope, await changed(tmp_path), "git.status")
    assert out.startswith('ok|<untrusted_content source="git.status">')
    assert "branch: kk/task-00000001" in out
    assert "modified src/b.py" in out and "untracked src/new.py" in out


async def test_diff_against_the_task_base_includes_commits_worktree_and_untracked(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    ws = await changed(tmp_path)
    out = await run(sessions, scope, ws, "git.diff")
    assert out.startswith('ok|<untrusted_content source="git.diff">')
    for expected in ("+++ b/src/a.py", "-two", "+TWO", "+++ b/src/b.py", "+more", "+++ b/src/new.py",
                     "+print('new')"):  # fmt: skip
        assert expected in out, (expected, out)
    only = await run(sessions, scope, ws, "git.diff", paths=["src/b.py"])
    assert "+more" in only and "src/a.py" not in only and "src/new.py" not in only


async def test_diff_without_a_base_commit_says_so(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    ws = await changed(tmp_path)
    no_base = TaskWorkspace(ws.root, repo=ws.repo, branch=ws.branch, base_commit=None)
    out = await run(sessions, scope, no_base, "git.diff")
    assert out.startswith("error|") and "base commit" in out


async def test_log_lists_commits_newest_first(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    out = await run(sessions, scope, await changed(tmp_path), "git.log", limit=5)
    body = out.split("\n")[1:-1]
    assert body[0].endswith("agent change") and body[1].endswith("init"), body
    assert all(len(line.split()[0]) == 40 for line in body), body  # TOOL_CATALOG ``commit``: the full SHA


async def test_a_large_diff_is_cut_at_a_file_boundary(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    ws = await task_worktree(tmp_path, {})
    for i in range(40):
        write(ws.root / "gen" / f"f{i:02}.txt", "".join(f"line {n} {'x' * 60}\n" for n in range(40)))
    out = await run(sessions, scope, ws, "git.diff")
    assert out.endswith("</untrusted_content>") and len(out.encode()) <= 64 * 1024
    assert "more changed files not shown; pass paths to see them]" in out and "cut after" in out
    assert "[truncated:" not in out  # cut by the tool with its own markers, never by the gateway
