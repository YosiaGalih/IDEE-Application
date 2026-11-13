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
    assert f"head: {before}" in out, out  # silent-failure 3.1d2b L4: what the branch holds is said


async def test_only_the_task_branch_takes_commits(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    write(ws.root / "src" / "a.py", "changed\n")
    before = await head(ws)
    main = TaskWorkspace(ws.root, repo=ws.repo, branch="main", base_commit=ws.base_commit)
    status, out = await run(sessions, scope, main, "wrong branch")
    assert status == "denied" and "task branch" in out, out  # LOW-2: a policy refusal
    assert await head(ws) == before


async def test_a_worktree_off_its_task_branch_is_an_error_not_a_denial(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    """Review L4: the workspace names a task branch the worktree is not on. The worktree is broken; the agent
    asked for nothing the policy refuses, so the call ends ``error`` ``branch_mismatch``, not ``denied``."""
    write(ws.root / "src" / "a.py", "changed\n")
    before = await head(ws)
    other = TaskWorkspace(ws.root, repo=ws.repo, branch="kk/task-99999999", base_commit=ws.base_commit)
    built = gateway(sessions, *commit.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=TOOLS, workspace=other)
    result = await built.execute(ctx, "git.commit_task", {"message": "x"}, tool_use_id="toolu_mismatch")
    assert (result.status, result.content) == ("error", "error: the worktree is not on the task branch")
    (row,) = [r for r in await denials.calls(sessions, scope.task_id) if r.id == result.tool_call_id]
    assert (row.status, row.error_code) == ("error", "branch_mismatch")
    assert "tool.denied" not in [a.action for a in await denials.audits(sessions, row)]
    assert await head(ws) == before


async def test_a_worktree_on_a_non_task_branch_takes_no_commit(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    """Mutant C6a: the worktree and the task agree on a branch, but it is not a kk/task-* branch."""
    import asyncio
    import subprocess

    switch = ["git", "-C", str(ws.root), "switch", "-q", "-c", "feature"]
    await asyncio.to_thread(subprocess.run, switch, check=True)  # test setup only
    on_feature = TaskWorkspace(ws.root, repo=ws.repo, branch="feature", base_commit=ws.base_commit)
    write(ws.root / "src" / "a.py", "changed\n")
    before = await head(on_feature)
    status, out = await run(sessions, scope, on_feature, "not a task branch")
    assert status == "denied" and "task branch" in out, out
    assert await head(on_feature) == before


async def test_staging_and_committing_run_under_the_worktree_lock(
    sessions: async_sessionmaker[AsyncSession],
    scope: Scope,
    ws: TaskWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/code-review 3.1d2b spec, Review Focus 2: the lock is held while git stages and commits, not only
    while the names are read: a write attempted during the commit waits and gives up."""
    from app.workspace.errors import WorkspaceBusy

    monkeypatch.setattr(fs, "LOCK_WAIT_S", 0.1)
    commit_files = GitAdapter.commit
    seen: list[str] = []

    async def write_during_commit(self: GitAdapter, *args: object, **kwargs: object) -> str | None:
        try:
            await ws.write_text("src/during.py", "x\n")
            seen.append("written")
        except WorkspaceBusy:
            seen.append("busy")
        return await commit_files(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(GitAdapter, "commit", write_during_commit)
    write(ws.root / "src" / "a.py", "changed\n")
    status, out = await run(sessions, scope, ws, "under the lock")
    assert status == "ok" and seen == ["busy"], (seen, out)


async def test_names_the_commit_cannot_take_are_counted(
    sessions: async_sessionmaker[AsyncSession],
    scope: Scope,
    ws: TaskWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lead decision 3.1d2b #5: names that are not UTF-8 and names past the check cap are not committed,
    and said."""
    import os

    from app.tools.builtin import _common

    (ws.root / os.fsdecode(b"bad\xff.txt")).write_text("x\n")
    write(ws.root / "src" / "a.py", "changed\n")
    write(ws.root / "src" / "b.py", "new\n")
    monkeypatch.setattr(_common, "MAX_NAMES", 1)
    status, out = await run(sessions, scope, ws, "partly")
    assert status == "ok" and "files: 1\nsrc/a.py\n" in out, out
    assert "[1 changed paths not committed: names that are not UTF-8]" in out, out
    assert (
        "[1 changed paths not committed: not checked against the agent policy (too many or too costly)]"
        in out
    )


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


async def test_the_answer_names_what_the_commit_holds_not_what_was_asked(
    sessions: async_sessionmaker[AsyncSession],
    scope: Scope,
    ws: TaskWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silent-failure 3.1d2b M2: the file list and count come from the commit itself; a listed path that
    changed nothing is said, not counted as committed."""
    from app.gitops.adapter import NameList

    changed_paths = GitAdapter.changed_paths

    async def with_an_unchanged_path(self: GitAdapter, target: object, *, base: str) -> NameList:
        found = await changed_paths(self, target, base=base)  # type: ignore[arg-type]
        return NameList([*found.paths, "src/old.py"], found.skipped)  # unchanged against HEAD

    monkeypatch.setattr(GitAdapter, "changed_paths", with_an_unchanged_path)
    write(ws.root / "src" / "a.py", "changed\n")
    status, out = await run(sessions, scope, ws, "one real change")
    assert status == "ok", out
    assert "files: 1\nsrc/a.py\n" in out and "\nsrc/old.py\n" not in out, out
    assert "[1 listed paths had no change and are not in the commit]" in out, out


async def test_a_file_replaced_by_a_folder_is_an_ordinary_change(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    """AG-13 3.1d2b delta INFO: only a folder holding a repository (.git) is a nested repository; a file
    that became a plain folder is committed as the file's deletion plus the folder's files."""
    (ws.root / "src" / "old.py").unlink()
    write(ws.root / "src" / "old.py" / "inner.py", "now a package\n")
    status, out = await run(sessions, scope, ws, "file to folder")
    assert status == "ok", out
    sha = await head(ws)
    assert sorted(await GitAdapter().commit_files(git_target(ws), sha or "")) == [
        "src/old.py",
        "src/old.py/inner.py",
    ]
    assert "nested repositories" not in out, out
