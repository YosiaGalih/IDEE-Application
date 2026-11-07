"""Nothing the agent policy hides reaches the model through a builtin (spec 3.1d2a Review Focus 1; T1, T5).

Hidden: built-in secret names (incl. ``*.env``), ``.agentignore`` of the worktree **or** of ``repo/``,
dependency folders, files over ``AGENT_READ_LIMIT``. Each check reads the worktree through the real gateway.
"""

from __future__ import annotations

from collections.abc import Mapping
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
               raw: Mapping[str, object]) -> str:  # fmt: skip
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


# --- repo.search_text: hits in hidden files never reach the model (2.1d hand-off, Review Focus 1, 3) ---


async def search_out(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace,
                     **raw: object) -> str:  # fmt: skip
    from app.tools.builtin import search

    built = gateway(sessions, *search.specs())
    ctx = context(
        task_id=scope.task_id, project_id=scope.project_id, tools=["repo.search_text"], workspace=ws
    )
    return (await built.execute(ctx, "repo.search_text", raw)).content


async def test_search_never_returns_hits_from_hidden_files(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    from app.workspace.fs import AGENT_READ_LIMIT
    from tests.worktree_fakes import task_worktree, write

    ws = await task_worktree(tmp_path, {"src/app.py": "visible\n"}, repo_agentignore="private/\nscratch/\n")
    for rel in HIDDEN:
        write(ws.root / rel, f"{MARK}\n")
    write(ws.root / ".kk" / "junit.xml", f"{MARK}\n")  # sandbox output
    write(ws.root / "big.log", f"{MARK}\n" + "z" * AGENT_READ_LIMIT)  # over the agent read limit
    (ws.root / ".agentignore").write_text("")  # the sandbox emptied the worktree copy
    out = await search_out(sessions, scope, ws, query=MARK)
    assert MARK not in out, out
    regex = await search_out(sessions, scope, ws, query="HIDDEN-.*", regex=True)
    assert MARK not in regex, regex


async def test_a_hostile_agentignore_cannot_make_search_unbounded(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One Budget for the whole search (2.1d hand-off): when it runs out the search stops and says so; the
    hits it did not check are never shown (fail closed). A budget per hit would let N hits cost N budgets."""
    from app.tools.builtin import search
    from app.workspace.policy import MAX_PATTERNS
    from tests.worktree_fakes import task_worktree, write

    monkeypatch.setattr(search, "SEARCH_POLICY_BUDGET", 20_000)
    ws = await task_worktree(tmp_path, {})
    for i in range(400):
        write(ws.root / "d" / f"f{i}.txt", "needle\n")
    patterns = [f"**/{'*' * 8}x{i}/**" for i in range(MAX_PATTERNS)]  # valid, at the cap, costly per path
    (ws.root / ".agentignore").write_text("\n".join(patterns) + "\n")
    out = await search_out(sessions, scope, ws, query="needle", max_results=200)
    assert out.startswith("<untrusted_content"), out[:400]
    assert "[truncated" in out and out.count("needle") < 200, out[-300:]


async def test_one_text_search_per_task_at_a_time(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from app.gitops.adapter import GitAdapter
    from tests.worktree_fakes import task_worktree

    ws = await task_worktree(tmp_path, {"a.txt": "needle\n"})
    started, release = asyncio.Event(), asyncio.Event()
    grep = GitAdapter.grep

    async def held(self: GitAdapter, *args: object, **kwargs: object) -> object:
        started.set()
        await release.wait()
        return await grep(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(GitAdapter, "grep", held)
    first = asyncio.ensure_future(search_out(sessions, scope, ws, query="needle"))
    await asyncio.wait_for(started.wait(), 10)
    try:
        second = await search_out(sessions, scope, ws, query="needle")
        assert second.startswith("error:") and "still running" in second
    finally:
        release.set()
    assert "a.txt:1: needle" in await first


# --- git.status / git.diff: hidden names and contents never reach the model (WP-1.2 hand-off) ---


async def git_out(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace, name: str,
                  **raw: object) -> str:  # fmt: skip
    from app.tools.builtin import git as git_tools

    built = gateway(sessions, *git_tools.specs())
    tools = ["git.status", "git.diff"]
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=tools, workspace=ws)
    return (await built.execute(ctx, name, raw)).content


async def test_git_never_shows_hidden_files(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    from tests.worktree_fakes import task_worktree, write

    ws = await task_worktree(tmp_path, {"src/app.py": "v\n"}, repo_agentignore="private/\nscratch/\n")
    for rel in HIDDEN:
        write(ws.root / rel, f"{MARK}\n")  # untracked: what a sandboxed install or the agent left
    write(ws.root / "src" / "app.py", "visible change\n")
    (ws.root / ".agentignore").write_text("")
    outputs = [
        await git_out(sessions, scope, ws, "git.status"),
        await git_out(sessions, scope, ws, "git.diff"),
        await git_out(sessions, scope, ws, "git.diff", paths=[".env"]),
        await git_out(sessions, scope, ws, "git.diff", paths=["config/prod.env", "private", "."]),
    ]
    for out in outputs:
        assert MARK not in out, out
        for rel in HIDDEN:
            assert rel not in out, (rel, out)
    assert "src/app.py" in outputs[0] and "+visible change" in outputs[1]
