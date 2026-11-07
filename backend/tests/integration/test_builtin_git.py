"""`git.status`, `git.diff`, `git.log` over the task worktree through the real gateway (3.1d2a Goals 8-10)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
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


async def test_what_one_diff_stages_is_bounded_and_the_rest_is_said(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """AG-13 WP-3.1d2a MEDIUM-4: the scratch index lives on the worker's tmpfs; one call stages at most
    STAGE_MAX_BYTES of working-tree files."""
    ws = await task_worktree(tmp_path, {})
    for i in range(6):
        write(ws.root / "gen" / f"f{i}.txt", "x" * 1000 + "\n")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(git_tools, "STAGE_MAX_BYTES", 2500)
        out = await run(sessions, scope, ws, "git.diff")
    assert out.count("diff --git") == 2, out
    assert "[4 more changed files not diffed in one call; pass paths to see them]" in out, out


async def test_files_below_a_folder_replaced_by_a_symlink_or_a_file_show_as_deleted(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """AG-13 delta LOW-D1/LOW-D3: git sees a path below a symlink or below a file as deleted, and so does
    git.diff: never a git failure, never "larger than the agent read limit"."""
    ws = await task_worktree(tmp_path, {"linked/a.txt": "a\n", "filed/b.txt": "b\n"})
    shutil.rmtree(ws.root / "linked")
    (ws.root / "linked").symlink_to(ws.repo)  # sandboxed code can do this in its worktree
    shutil.rmtree(ws.root / "filed")
    write(ws.root / "filed", "now a file\n")
    out = await run(sessions, scope, ws, "git.diff")
    assert out.startswith("ok|"), out
    assert "--- a/linked/a.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n" in out, out
    assert "--- a/filed/b.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-b\n" in out, out
    assert "agent read limit" not in out, out


async def test_the_names_one_diff_passes_to_git_are_bounded(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """AG-13 delta LOW-D2: an empty file costs no staging bytes but is still an argument to git; one call
    passes at most DIFF_MAX_FILES names and ARGV_MAX_BYTES of them (never E2BIG), the rest is said."""
    ws = await task_worktree(tmp_path, {})
    for i in range(5):
        write(ws.root / "gen" / f"e{i}.txt", "")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(git_tools, "DIFF_MAX_FILES", 3)
        out = await run(sessions, scope, ws, "git.diff")
    assert out.count("diff --git") == 3, out
    assert "[2 more changed files not diffed in one call; pass paths to see them]" in out, out
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(git_tools, "ARGV_MAX_BYTES", 30)  # "gen/eN.txt" + NUL = 11 bytes: two fit
        out = await run(sessions, scope, ws, "git.diff")
    assert out.count("diff --git") == 2, out
    assert "[3 more changed files not diffed in one call; pass paths to see them]" in out, out
