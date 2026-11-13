"""The write builtins never write a path AGENT_WRITE refuses, nor outside the agent's filesystem mode, also
through a patch (spec 3.1d2b Review Focus 1; T2, T5; Lead decision 3.1d2b #2 for ``.kk/``)."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tools.builtin import code
from app.workspace.fs import TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway
from tests.worktree_fakes import task_worktree, write

clean = denials.clean
sessions = denials.sessions
scope = denials.scope
TOOLS = ["code.write_file", "code.apply_patch", "code.delete_file"]
PROTECTED = [".env", "config/prod.env", "certs/server.pem", ".agentignore", ".kk/junit.xml",
             ".github/workflows/ci.yml", "private/notes.md"]  # fmt: skip
_uses = itertools.count()


async def run(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, name: str,
              raw: dict[str, object], **perms: str) -> tuple[str, str]:  # fmt: skip
    built = gateway(sessions, *code.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=TOOLS, workspace=ws, **perms)
    result = await built.execute(ctx, name, raw, tool_use_id=f"toolu_w_{next(_uses)}")
    return result.status, result.content


@pytest.fixture
async def ws(tmp_path: Path) -> TaskWorkspace:
    return await task_worktree(tmp_path, {"src/a.py": "a\n"}, repo_agentignore="private/\n")


@pytest.mark.parametrize("path", PROTECTED)
async def test_no_write_tool_creates_a_protected_path(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, path: str
) -> None:
    (ws.root / ".agentignore").unlink()  # what sandboxed code can do; repo/'s copy still hides private/
    status, out = await run(sessions, scope, ws, "code.write_file", {"path": path, "content": "x\n"})
    assert status in ("error", "denied") and not (ws.root / path).exists(), (status, out)
    diff = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+x\n"
    status, out = await run(sessions, scope, ws, "code.apply_patch", {"diff": diff})
    assert status in ("error", "denied") and not (ws.root / path).exists(), (status, out)


@pytest.mark.parametrize("path", PROTECTED)
async def test_no_write_tool_changes_or_deletes_a_protected_file(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, path: str
) -> None:
    write(ws.root / path, "kept\n")  # put there by the user or by sandboxed code
    attempts = [
        ("code.write_file", {"path": path, "content": "changed\n"}),
        ("code.delete_file", {"path": path}),
        ("code.apply_patch", {"diff": f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-kept\n+changed\n"}),
        ("code.apply_patch", {"diff": f"--- a/{path}\n+++ /dev/null\n@@ -1 +0,0 @@\n-kept\n"}),
    ]
    for name, raw in attempts:
        status, out = await run(sessions, scope, ws, name, raw)
        assert status in ("error", "denied"), (name, status, out)
        assert (ws.root / path).read_text() == "kept\n", (name, out)


async def test_no_write_leaves_the_worktree(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    outside = ws.root.parent / "outside.txt"
    for path in ("../outside.txt", "src/../../outside.txt", str(outside), "a\\b.txt"):
        status, _ = await run(sessions, scope, ws, "code.write_file", {"path": path, "content": "x\n"})
        assert status in ("error", "denied"), path
        assert not outside.exists(), path


async def test_a_patch_cannot_reach_outside_the_filesystem_mode(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    """A test_only agent's patch that also touches src/ is refused whole: nothing is written."""
    diff = (
        "--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1 @@\n+ok\n"
        "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    raw: dict[str, object] = {"diff": diff}
    status, out = await run(sessions, scope, ws, "code.apply_patch", raw, filesystem="test_only")
    assert status == "denied", out
    assert not (ws.root / "tests" / "test_new.py").exists()
    assert (ws.root / "src" / "a.py").read_text() == "a\n"


# --- git.commit_task (spec 3.1d2b Review Focus 2) -------------------------------------------------------


async def commit_out(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace,
                     message: str) -> tuple[str, str]:  # fmt: skip
    from app.tools.builtin import commit

    built = gateway(sessions, *commit.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["git.commit_task"], workspace=ws)
    result = await built.execute(
        ctx, "git.commit_task", {"message": message}, tool_use_id=f"toolu_w_{next(_uses)}"
    )
    return result.status, result.content


async def test_a_commit_never_takes_what_agents_may_not_read(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """Hidden changes stay out of the commit (and in the worktree): untracked secrets, a tracked secret the
    sandbox changed, .agentignore'd files of repo/ and of the worktree, sandbox output, a nested repo."""
    from app.gitops.adapter import GitAdapter
    from app.tools.builtin._common import git_target

    ws = await task_worktree(
        tmp_path, {"config/prod.env": "OLD=1\n", "src/a.py": "a\n"}, repo_agentignore="private/\n"
    )
    for rel in (".env", "private/notes.md", ".kk/junit.xml", "scratch/x.txt", "config/prod.env"):
        write(ws.root / rel, "HIDDEN-MARK\n")
    write(ws.root / ".agentignore", "private/\nscratch/\n")  # the task's own addition
    (ws.root / "nested").mkdir()
    await GitAdapter().init(ws.root / "nested", "main")  # a repository inside the worktree (untracked)
    write(ws.root / "src" / "a.py", "visible\n")
    status, out = await commit_out(sessions, scope, ws, "visible only")
    assert status == "ok", out
    git, target = GitAdapter(), git_target(ws)
    sha = await git.current_commit(target)
    committed = await git.commit_files(target, sha or "")
    assert committed == ["src/a.py"], (
        committed
    )  # .agentignore: agents may not write it (AG-13 3.1d2b MEDIUM-1)
    assert "[1 changed paths not committed: nested repositories]" in out, out
    assert "HIDDEN-MARK" not in out and "prod.env" not in out and "private/" not in out, out
    assert "not committed" in out, out
    assert (ws.root / "config" / "prod.env").read_text() == "HIDDEN-MARK\n"  # left in the worktree


async def test_a_visible_change_that_drags_hidden_paths_is_refused(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """Lead decision 3.1d2b #4: a folder holding a secret, replaced by a file, would commit the secret's
    deletion: nothing is committed and the index is back at HEAD."""
    import shutil

    from app.gitops.adapter import GitAdapter
    from app.tools.builtin._common import git_target

    ws = await task_worktree(tmp_path, {"config/prod.env": "S=1\n", "config/x.txt": "x\n"})
    git, target = GitAdapter(), git_target(ws)
    before = await git.current_commit(target)
    shutil.rmtree(ws.root / "config")
    write(ws.root / "config", "now a file\n")
    status, out = await commit_out(sessions, scope, ws, "swap")
    assert status == "denied" and "nothing was committed" in out, out  # LOW-2: a policy refusal
    assert await git.current_commit(target) == before
    assert (await git.status(target)).changes  # the worktree change is still there, unstaged


async def test_a_commit_never_takes_what_agents_may_not_write(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """AG-13 3.1d2b MEDIUM-1: sandboxed code can plant or drop a workflow or empty the task's .agentignore;
    the agent cannot write those paths, so its commit must not carry them either (TOOL_CATALOG R4)."""
    from app.gitops.adapter import GitAdapter
    from app.tools.builtin._common import git_target

    ws = await task_worktree(
        tmp_path,
        {".github/workflows/ci.yml": "on: push\n", ".github/actions/setup/action.yml": "runs: {}\n",
         ".agentignore": "private/\n", "src/a.py": "a\n"},
    )  # fmt: skip
    write(ws.root / ".github" / "workflows" / "evil.yml", "on: push\njobs: {}\n")
    write(ws.root / ".github" / "actions" / "setup" / "action.yml", "runs: {using: evil}\n")
    (ws.root / ".github" / "workflows" / "ci.yml").unlink()
    write(ws.root / ".agentignore", "")
    write(ws.root / "src" / "a.py", "visible\n")
    status, out = await commit_out(sessions, scope, ws, "visible only")
    assert status == "ok", out
    git, target = GitAdapter(), git_target(ws)
    assert await git.commit_files(target, await git.current_commit(target) or "") == ["src/a.py"]
    assert "[4 changed paths not committed: agents may not read or write them, or sandbox output]" in out, out
    assert (ws.root / ".github" / "workflows" / "evil.yml").exists()  # left in the worktree, uncommitted


async def test_a_workflows_folder_replaced_by_a_file_is_refused(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    import shutil

    from app.gitops.adapter import GitAdapter
    from app.tools.builtin._common import git_target

    ws = await task_worktree(tmp_path, {".github/workflows/ci.yml": "on: push\n"})
    git, target = GitAdapter(), git_target(ws)
    before = await git.current_commit(target)
    shutil.rmtree(ws.root / ".github")
    write(ws.root / ".github", "now a file\n")
    status, out = await commit_out(sessions, scope, ws, "drop the workflows")
    assert status == "denied" and "nothing was committed" in out, out  # LOW-2: a policy refusal
    assert await git.current_commit(target) == before


async def test_a_tracked_submodule_is_never_committed(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """/code-review 3.1d2b spec: a gitlink whose checkout moved shows up as a changed path; committing it
    would point the project at another repository's commit (Lead decision 3.1d2b #5: nested repositories)."""
    import asyncio
    import subprocess

    from app.gitops.adapter import GitAdapter
    from app.tools.builtin._common import git_target

    ws = await task_worktree(tmp_path, {"src/a.py": "a\n"})
    who = ["-c", "user.name=T", "-c", "user.email=t@example.test"]

    def git(cwd: Path, *args: str) -> str:
        done = subprocess.run(  # noqa: S603 - test setup: fixed git argv
            ["git", "-C", str(cwd), *who, *args],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    sub = ws.root / "sub"
    sub.mkdir()
    await GitAdapter().init(sub, "main")
    write(sub / "x.txt", "one\n")
    await asyncio.to_thread(git, sub, "add", "x.txt")
    await asyncio.to_thread(git, sub, "commit", "-q", "-m", "one")
    first = await asyncio.to_thread(git, sub, "rev-parse", "HEAD")
    await asyncio.to_thread(git, ws.root, "update-index", "--add", "--cacheinfo", f"160000,{first},sub")
    await asyncio.to_thread(git, ws.root, "commit", "-q", "-m", "gitlink")
    write(sub / "x.txt", "two\n")
    await asyncio.to_thread(git, sub, "commit", "-q", "-am", "two")  # the submodule's checkout moves
    write(ws.root / "src" / "a.py", "visible\n")
    status, out = await commit_out(sessions, scope, ws, "visible only")
    assert status == "ok", out
    target = git_target(ws)
    assert await GitAdapter().commit_files(target, await GitAdapter().current_commit(target) or "") == [
        "src/a.py"
    ]
    assert "[1 changed paths not committed: nested repositories]" in out, out
