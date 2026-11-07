"""Builtins `repo.*` through the real gateway over a task worktree (spec 3.1d2a Goals 3, 4, 6, 7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tools.builtin import repo
from app.workspace.fs import AGENT_READ_LIMIT, TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway

clean = denials.clean
sessions = denials.sessions
scope = denials.scope
TOOLS = ["repo.get_tree", "repo.read_file", "repo.detect_stack", "repo.find_related_tests"]


@pytest.fixture
def worktree(tmp_path: Path) -> TaskWorkspace:
    repo_dir, root = tmp_path / "p" / "repo", tmp_path / "p" / "tasks" / "t1"
    repo_dir.mkdir(parents=True)
    root.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text("from src.calc import add\n")
    (root / "tests" / "test_other.py").write_text("x = 1\n")
    (root / "web").mkdir()
    (root / "web" / "calc.test.ts").write_text("it('adds', () => {})\n")
    (root / "docs").mkdir()
    (root / "docs" / "calc.md").write_text("# calc\n")  # carries the stem, but is not a test file
    (root / "pyproject.toml").write_text('[project]\nname = "calc"\ndependencies = ["pytest"]\n')
    return TaskWorkspace(root, repo=repo_dir, branch="kk/task-1", base_commit=None)


async def run(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace | None,
              name: str, raw: dict[str, object]) -> str:  # fmt: skip
    built = gateway(sessions, *repo.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=TOOLS, workspace=ws)
    result = await built.execute(ctx, name, raw)
    return f"{result.status}|{result.content}"


async def test_read_file_numbers_lines_and_is_wrapped(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace
) -> None:
    out = await run(sessions, scope, worktree, "repo.read_file", {"path": "src/calc.py"})
    assert out == (
        'ok|<untrusted_content source="repo.read_file" path="src/calc.py">\n'
        "1\tdef add(a, b):\n2\t    return a + b\n\n</untrusted_content>"
    )


async def test_read_file_range_and_truncation_note(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace
) -> None:
    (worktree.root / "long.txt").write_text("".join(f"line {i} " + "x" * 90 + "\n" for i in range(1, 2001)))
    ranged = await run(sessions, scope, worktree, "repo.read_file", {"path": "long.txt", "start_line": 5,
                                                                       "end_line": 6})  # fmt: skip
    assert "\n5\tline 5 " in ranged and "\n6\tline 6 " in ranged and "\n7\t" not in ranged
    whole = await run(sessions, scope, worktree, "repo.read_file", {"path": "long.txt"})
    assert "read on with start_line=" in whole and whole.endswith("</untrusted_content>")
    assert len(whole.encode()) < 64 * 1024


async def test_read_file_refusals_are_codes_not_content(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace
) -> None:
    (worktree.root / "big.txt").write_text("y" * (AGENT_READ_LIMIT + 1))
    (worktree.root / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
    cases = {
        "big.txt": "agents never read it",
        "blob.bin": "not text",
        "missing.py": "no such file",
        "../outside.txt": "relative path",
        "/etc/passwd": "relative path",
    }
    for path, expected in cases.items():
        out = await run(sessions, scope, worktree, "repo.read_file", {"path": path})
        assert out.startswith("error|") and expected in out, (path, out)
    ranged = await run(sessions, scope, worktree, "repo.read_file", {"path": "big.txt", "start_line": 1,
                                                                       "end_line": 1})  # fmt: skip
    assert ranged.startswith("error|") and "y" * 50 not in ranged  # a range never opens a file over the limit


async def test_get_tree_lists_paths_with_folders_marked(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace
) -> None:
    out = await run(sessions, scope, worktree, "repo.get_tree", {})
    body = out.split("\n", 1)[1].rsplit("\n", 1)[0]
    assert {"src/", "src/calc.py", "tests/", "tests/test_calc.py", "pyproject.toml"} <= set(body.split("\n"))


async def test_find_related_tests_by_name(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace
) -> None:
    out = await run(sessions, scope, worktree, "repo.find_related_tests", {"path": "src/calc.py"})
    body = out.split("\n", 1)[1].rsplit("\n", 1)[0]
    assert body.split("\n") == ["tests/test_calc.py", "web/calc.test.ts"]


async def test_detect_stack(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, worktree: TaskWorkspace
) -> None:
    out = await run(sessions, scope, worktree, "repo.detect_stack", {})
    assert out.startswith('ok|<untrusted_content source="repo.detect_stack">') and '"python"' in out


async def test_without_a_worktree_the_tool_says_so(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    out = await run(sessions, scope, None, "repo.read_file", {"path": "a.py"})
    assert out.startswith("error|") and "no worktree" in out
