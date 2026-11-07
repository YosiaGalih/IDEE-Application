"""`code.write_file`, `code.apply_patch`, `code.delete_file` through the real gateway (3.1d2b Goal 1)."""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tools.builtin import code
from app.workspace import fs
from app.workspace.fs import TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway
from tests.worktree_fakes import task_worktree, write

clean = denials.clean
sessions = denials.sessions
scope = denials.scope
TOOLS = ["code.write_file", "code.apply_patch", "code.delete_file"]
_uses = itertools.count()


async def run(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, name: str,
              raw: dict[str, object], *, use_id: str | None = None, **perms: str) -> str:  # fmt: skip
    built = gateway(sessions, *code.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=TOOLS, workspace=ws, **perms)
    result = await built.execute(ctx, name, raw, tool_use_id=use_id or f"toolu_code_{next(_uses)}")
    return f"{result.status}|{result.content}"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
async def ws(tmp_path: Path) -> TaskWorkspace:
    return await task_worktree(tmp_path, {"src/a.py": "one\ntwo\n", "tests/test_a.py": "x = 1\n"})


async def test_write_file_adds_then_modifies_and_answers_a_file_change(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    added = await run(sessions, scope, ws, "code.write_file", {"path": "src/new.py", "content": "x = 1\n"})
    assert added.startswith("ok|"), added
    expected = {"path": "src/new.py", "change": "added", "sha256": sha("x = 1\n")}
    assert json.loads(added.split("|", 1)[1]) == expected
    changed = await run(sessions, scope, ws, "code.write_file", {"path": "src/new.py", "content": "x = 2\n"})
    assert json.loads(changed.split("|", 1)[1])["change"] == "modified"
    assert (ws.root / "src" / "new.py").read_text() == "x = 2\n"


async def test_delete_file_and_a_missing_file(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    out = await run(sessions, scope, ws, "code.delete_file", {"path": "src/a.py"})
    assert json.loads(out.split("|", 1)[1]) == {"path": "src/a.py", "change": "deleted", "sha256": None}
    assert not (ws.root / "src" / "a.py").exists()
    missing = await run(sessions, scope, ws, "code.delete_file", {"path": "src/a.py"})
    assert missing.startswith("error|") and "no such file" in missing, missing


async def test_apply_patch_changes_files_all_or_nothing(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    diff = "--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +1,2 @@\n one\n-two\n+TWO\n"
    out = await run(sessions, scope, ws, "code.apply_patch", {"diff": diff})
    assert out.startswith("ok|"), out
    assert json.loads(out.split("|", 1)[1]) == [
        {"path": "src/a.py", "change": "modified", "sha256": sha("one\nTWO\n")}
    ]
    stale = diff + "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@ -1 +1 @@\n-x = 9\n+x = 2\n"
    conflict = await run(sessions, scope, ws, "code.apply_patch", {"diff": stale})
    assert conflict.startswith("error|"), conflict
    assert "src/a.py" in conflict and "tests/test_a.py" in conflict and "hunk 1" in conflict, conflict
    assert "x = 9" not in conflict  # file content never travels in an error
    assert (ws.root / "tests" / "test_a.py").read_text() == "x = 1\n"


async def test_a_malformed_patch_is_an_invalid_input_not_a_crash(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    out = await run(sessions, scope, ws, "code.apply_patch", {"diff": "--- a/x\n+++ b/y\n@@ nonsense\n"})
    assert out.startswith("error|") and "invalid input" in out, out
    renamed = "diff --git a/src/a.py b/src/b.py\nrename from src/a.py\nrename to src/b.py\n"
    out = await run(sessions, scope, ws, "code.apply_patch", {"diff": renamed})
    assert out.startswith("error|") and "rename" in out, out


@pytest.mark.parametrize(
    ("filesystem", "path", "allowed"),
    [("test_only", "tests/test_b.py", True), ("test_only", "src/b.py", False),
     ("docs_only", "README.md", True), ("docs_only", "src/b.py", False)],
)  # fmt: skip
async def test_the_filesystem_mode_bounds_writes(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, filesystem: str, path: str,
    allowed: bool,
) -> None:  # fmt: skip
    raw: dict[str, object] = {"path": path, "content": "x\n"}
    out = await run(sessions, scope, ws, "code.write_file", raw, filesystem=filesystem)
    assert out.startswith("ok|" if allowed else "denied|"), out
    assert (ws.root / path).exists() is allowed or path == "README.md"


async def test_deleting_through_a_patch_needs_scoped_write(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    """Lead decision 3.1d2b #3: code.delete_file needs scoped_write, so a patch that deletes does too."""
    diff = "--- a/tests/test_a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    out = await run(sessions, scope, ws, "code.apply_patch", {"diff": diff}, filesystem="test_only")
    assert out.startswith("error|") and "scoped_write" in out, out
    assert (ws.root / "tests" / "test_a.py").exists()
    out = await run(sessions, scope, ws, "code.apply_patch", {"diff": diff})
    assert out.startswith("ok|"), out
    assert not (ws.root / "tests" / "test_a.py").exists()


async def test_one_tool_use_writes_once(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    raw: dict[str, object] = {"path": "src/a.py", "content": "first\n"}
    first = await run(sessions, scope, ws, "code.write_file", raw, use_id="toolu_once")
    write(ws.root / "src" / "a.py", "changed since\n")
    again = await run(sessions, scope, ws, "code.write_file", raw, use_id="toolu_once")
    assert "it is not run again" in again and first.split("|", 1)[1] in again, again
    assert (ws.root / "src" / "a.py").read_text() == "changed since\n"


async def test_a_busy_worktree_is_said(
    sessions: async_sessionmaker[AsyncSession],
    scope: Scope,
    ws: TaskWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fs, "LOCK_WAIT_S", 0.2)
    async with ws.exclusive():
        out = await run(sessions, scope, ws, "code.write_file", {"path": "src/a.py", "content": "x\n"})
    assert out.startswith("error|") and "busy" in out, out
