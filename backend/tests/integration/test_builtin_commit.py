"""`git.commit_task` through the real gateway (spec 3.1d2b Goal 2; WP-2.1c I-b, I-6)."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gitops.adapter import GitAdapter
from app.tools.builtin import commit
from app.tools.builtin._common import git_target
from app.workspace import fs
from app.workspace.fs import TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway
from tests.worktree_fakes import task_worktree, write

clean = denials.clean
sessions = denials.sessions
scope = denials.scope
TOOLS = ["git.commit_task"]
_uses = itertools.count()


async def run(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, message: str,
              *, use_id: str | None = None) -> tuple[str, str]:  # fmt: skip
    built = gateway(sessions, *commit.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=TOOLS, workspace=ws)
    result = await built.execute(
        ctx, "git.commit_task", {"message": message}, tool_use_id=use_id or f"toolu_c_{next(_uses)}"
    )
    return result.status, result.content


@pytest.fixture
async def ws(tmp_path: Path) -> TaskWorkspace:
    return await task_worktree(tmp_path, {"src/a.py": "a\n", "src/old.py": "old\n"})


async def head(ws: TaskWorkspace) -> str | None:
    return await GitAdapter().current_commit(git_target(ws))


async def test_commit_takes_the_visible_changes_as_the_agent(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    before = await head(ws)
    write(ws.root / "src" / "a.py", "changed\n")
    write(ws.root / "src" / "new.py", "new\n")
    (ws.root / "src" / "old.py").unlink()
    status, out = await run(sessions, scope, ws, "agent change")
    assert status == "ok" and out.startswith('<untrusted_content source="git.commit_task">'), out
    sha = await head(ws)
    assert sha != before and f"commit: {sha}" in out, out
    assert sorted(await GitAdapter().commit_files(git_target(ws), sha or "")) == [
        "src/a.py", "src/new.py", "src/old.py"
    ]  # fmt: skip
    for path in ("src/a.py", "src/new.py", "src/old.py"):
        assert f"\n{path}\n" in out, out
    log = await GitAdapter().log(git_target(ws), 1)
    agent = commit.AGENT_AUTHOR
    assert log[0].author == f"{agent.name} <{agent.email}>" and log[0].message == "agent change"


async def test_nothing_to_commit_is_said(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    before = await head(ws)
    status, out = await run(sessions, scope, ws, "nothing")
    assert status == "ok" and "[nothing to commit]" in out, out
    assert await head(ws) == before


async def test_only_the_task_branch_takes_commits(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    write(ws.root / "src" / "a.py", "changed\n")
    before = await head(ws)
    for branch in ("main", "kk/task-99999999"):  # not a task branch; not the branch the worktree is on
        other = TaskWorkspace(ws.root, repo=ws.repo, branch=branch, base_commit=ws.base_commit)
        status, out = await run(sessions, scope, other, "wrong branch")
        assert status == "error" and "task branch" in out, (branch, out)
    assert await head(ws) == before


async def test_the_commit_holds_the_worktree_lock(
    sessions: async_sessionmaker[AsyncSession],
    scope: Scope,
    ws: TaskWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WP-2.1c I-b: staging and committing see one consistent tree; a write in progress makes it wait."""
    monkeypatch.setattr(fs, "LOCK_WAIT_S", 0.2)
    write(ws.root / "src" / "a.py", "changed\n")
    before = await head(ws)
    async with ws.exclusive():
        status, out = await run(sessions, scope, ws, "while busy")
    assert status == "error" and "busy" in out, out
    assert await head(ws) == before


async def test_one_tool_use_commits_once(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    write(ws.root / "src" / "a.py", "changed\n")
    first = await run(sessions, scope, ws, "once", use_id="toolu_commit_once")
    sha = await head(ws)
    write(ws.root / "src" / "a.py", "changed again\n")
    again = await run(sessions, scope, ws, "once", use_id="toolu_commit_once")
    assert "it is not run again" in again[1] and await head(ws) == sha, (first, again)


async def test_an_invalid_message_is_refused(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    write(ws.root / "src" / "a.py", "changed\n")
    before = await head(ws)
    for message in ("", "   ", "x" * 5001):
        status, out = await run(sessions, scope, ws, message)
        assert status == "error", (message[:10], out)
    assert await head(ws) == before
