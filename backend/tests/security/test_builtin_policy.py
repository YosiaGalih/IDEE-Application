"""Nothing the agent policy hides reaches the model through a builtin (spec 3.1d2a Review Focus 1; T1, T5).

Hidden: built-in secret names (incl. ``*.env``), ``.agentignore`` of the worktree **or** of ``repo/``,
dependency folders, files over ``AGENT_READ_LIMIT``. Each check reads the worktree through the real gateway.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tools.builtin import repo
from app.workspace.fs import TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway

clean = denials.clean
sessions = denials.sessions
scope = denials.scope
MARK = "HIDDEN-MARK-7f3a"  # in every hidden file; must never appear in any result
HIDDEN = [".env", "config/prod.env", "certs/server.pem", "private/notes.md", "scratch/tmp.txt",
          "node_modules/lib/index.js"]  # fmt: skip


@pytest.fixture
def worktree(tmp_path: Path) -> TaskWorkspace:
    repo_dir, root = tmp_path / "p" / "repo", tmp_path / "p" / "tasks" / "t1"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".agentignore").write_text("private/\n")  # the user's, trusted
    root.mkdir(parents=True)
    (root / ".agentignore").write_text("scratch/\n")  # the task's own addition
    for rel in [*HIDDEN, "src/app.py"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(f"{MARK if rel in HIDDEN else 'visible'}\n")
    return TaskWorkspace(root, repo=repo_dir, branch="kk/task-1", base_commit=None)


async def call(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, name: str,
               raw: dict[str, object]) -> str:  # fmt: skip
    tools = [s.name for s in repo.specs()]
    built = gateway(sessions, *repo.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=tools, workspace=ws)
    return (await built.execute(ctx, name, raw)).content


@pytest.mark.parametrize("path", HIDDEN)
async def test_a_hidden_file_cannot_be_read(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace, path: str
) -> None:
    for raw in ({"path": path}, {"path": path, "start_line": 1, "end_line": 1}, {"path": f"./{path}"}):
        out = await call(sessions, scope, worktree, "repo.read_file", raw)
        assert MARK not in out and out.startswith("error:"), (raw, out)


async def test_hidden_names_are_not_listed(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace
) -> None:
    out = await call(sessions, scope, worktree, "repo.get_tree", {"depth": 10})
    listed = set(out.split("\n")[1:-1])
    for path in HIDDEN:
        assert path not in listed, (path, out)
    assert not listed & {"private/", "scratch/", "node_modules/"}  # hidden folders are not listed at all
    assert "src/app.py" in listed and "config/" in listed  # a folder holding a secret is not itself hidden


async def test_the_worktree_agentignore_cannot_unhide_what_repo_hides(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace
) -> None:
    (worktree.root / ".agentignore").write_text("")  # what sandboxed code can do
    out = await call(sessions, scope, worktree, "repo.read_file", {"path": "private/notes.md"})
    assert MARK not in out and out.startswith("error:")


async def test_related_tests_of_a_hidden_path_are_refused(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace
) -> None:
    out = await call(sessions, scope, worktree, "repo.find_related_tests", {"path": "private/notes.md"})
    assert out.startswith("error:") and "not read" in out
