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
