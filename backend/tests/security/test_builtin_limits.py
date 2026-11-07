"""Bounds of the builtins under what sandboxed code can leave in a worktree (security regressions of
WP-3.1d2a: AG-13 MEDIUM-4, delta LOW-D1 to LOW-D4)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tools.builtin import git as git_tools
from app.tools.builtin import repo
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway
from tests.worktree_fakes import task_worktree, write

clean = denials.clean
sessions = denials.sessions
scope = denials.scope


async def run(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: object, name: str,
              **raw: object) -> str:  # fmt: skip
    specs = [*git_tools.specs(), *repo.specs()]
    built = gateway(sessions, *specs)
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=[s.name for s in specs],
                  workspace=ws)  # fmt: skip
    result = await built.execute(ctx, name, raw)
    return f"{result.status}|{result.content}"


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


async def test_a_huge_line_is_redacted_before_it_is_cut(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """AG-13 delta LOW-D4: a secret across the cut of a huge line must not survive as a prefix that no
    pattern recognises any more; a token cut at the edge (a configured literal) is dropped whole."""
    worktree = await task_worktree(tmp_path, {})
    edge = repo.READ_MAX_BYTES - 200 - len("1\t")
    url = "x" * (edge - 20) + " https://user:hunter2secretpw@host/x " + "y" * 100_000
    (worktree.root / "url.js").write_text(url + "\n")
    out = await run(sessions, scope, worktree, "repo.read_file", path="url.js")
    assert out.startswith("ok|") and "longer than one read" in out, out[:200]
    assert "hunter" not in out and "user:" not in out
    token = "x" * (edge - 20) + " " + "Z9q" * 1000 + " tail"
    (worktree.root / "token.js").write_text(token + "\n")
    out = await run(sessions, scope, worktree, "repo.read_file", path="token.js")
    assert "longer than one read" in out and "Z9q" not in out, out[-300:]


async def test_a_file_over_the_agent_read_limit_in_the_base_is_named_not_diffed(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """/code-review WP-3.1d2a spec: the limit holds for both sides. A base version over AGENT_READ_LIMIT cut
    down to a small file must not show its old lines, even when git's answer is cut before the block ends."""
    from app.workspace.fs import AGENT_READ_LIMIT

    big = "".join(f"old line {n} OLD-MARK\n" for n in range(AGENT_READ_LIMIT // 20 + 10))
    ws = await task_worktree(tmp_path, {"data/big.txt": big, "src/a.py": "a\n"})
    write(ws.root / "data" / "big.txt", "small now\n")
    write(ws.root / "src" / "a.py", "b\n")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(git_tools, "DIFF_READ_BYTES", 4096)  # git's answer cut inside the big block
        out = await run(sessions, scope, ws, "git.diff")
    assert "OLD-MARK" not in out, out[:400]
    assert "[data/big.txt not shown: larger than the agent read limit, or unreadable]" in out, out
    assert "+b" in out
