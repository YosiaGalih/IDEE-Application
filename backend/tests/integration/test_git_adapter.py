"""GitAdapter against real git in tmp folders (FR-113..116, ADR-016, AC-09; TEST_STRATEGY §2.2)."""

from __future__ import annotations

import asyncio
import os
import stat
import time
from pathlib import Path

import pytest

from app.gitops.adapter import Author, GitAdapter, GitTarget
from app.gitops.errors import GitError

ME = Author("Dev", "dev@example.test")


@pytest.fixture
def git() -> GitAdapter:
    return GitAdapter()


@pytest.fixture
async def repo(tmp_path: Path, git: GitAdapter) -> GitTarget:
    root = tmp_path / "p" / "repo"
    root.mkdir(parents=True)
    await git.init(root, "main")
    (root / "f.txt").write_text("a\nb\nc\n")
    (root / "keep.md").write_text("keep\n")
    await git.commit_all(GitTarget(root), "init", ME)
    return GitTarget(root)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- task 1: status, log, branches, commit ---------------------------------------------------------------


async def test_unborn_repo(tmp_path: Path, git: GitAdapter) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    await git.init(root, "main")
    status = await git.status(GitTarget(root))
    assert (status.branch, status.head, status.clean, status.changes) == ("main", None, True, [])
    assert await git.current_commit(GitTarget(root)) is None
    assert await git.log(GitTarget(root)) == []


async def test_commit_and_log(git: GitAdapter, repo: GitTarget) -> None:
    head = await git.current_commit(repo)
    assert head is not None and len(head) == 40
    write(repo.repo / "f.txt", "a\nB\nc\n")
    sha = await git.commit_all(repo, "change b", ME)
    assert sha is not None and sha != head
    entries = await git.log(repo, limit=5)
    assert [e.message for e in entries] == ["change b", "init"]
    assert entries[0].commit == sha
    assert entries[0].author == "Dev <dev@example.test>"
    assert entries[0].date.startswith("20")


async def test_nothing_to_commit(git: GitAdapter, repo: GitTarget) -> None:
    assert await git.commit_all(repo, "noop", ME) is None


async def test_status_changes(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "f.txt", "changed\n")
    (repo.repo / "keep.md").unlink()
    write(repo.repo / "docs" / "café note.md", "new\n")
    status = await git.status(repo)
    assert status.branch == "main"
    assert status.clean is False
    assert sorted((c.path, c.status) for c in status.changes) == [
        ("docs/café note.md", "untracked"),
        ("f.txt", "modified"),
        ("keep.md", "deleted"),
    ]


async def test_branches(git: GitAdapter, repo: GitTarget) -> None:
    head = await git.current_commit(repo)
    assert await git.create_branch(repo, "kk/task-0a1b2c3d", "main") == head
    assert await git.create_branch(repo, "feature/x", head or "") == head
    branches = {b.name: b for b in await git.branches(repo)}
    assert set(branches) == {"main", "kk/task-0a1b2c3d", "feature/x"}
    assert branches["kk/task-0a1b2c3d"].is_task_branch is True
    assert branches["feature/x"].is_task_branch is False
    with pytest.raises(GitError):
        await git.create_branch(repo, "feature/x", "main")  # exists


async def test_timeout_kills_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "child-alive"
    fake = tmp_path / "fake-git"
    fake.write_text(f"#!/bin/sh\n(sleep 3; touch {marker}) &\nsleep 30\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    (tmp_path / ".git").mkdir()
    slow = GitAdapter(str(fake), timeout_s=0.5)
    started = time.monotonic()
    with pytest.raises(GitError) as caught:
        await slow.status(GitTarget(tmp_path))
    assert caught.value.reason == "timeout"
    assert time.monotonic() - started < 5
    await asyncio.sleep(3.5)
    assert not marker.exists()  # the background child died with its process group


async def test_output_cap(git: GitAdapter, repo: GitTarget) -> None:
    capped = GitAdapter(max_output=64)
    with pytest.raises(GitError) as caught:
        await capped.log(repo, limit=10)
    assert caught.value.reason == "output_too_large"


async def test_git_errors_carry_no_absolute_path(git: GitAdapter, repo: GitTarget) -> None:
    with pytest.raises(GitError) as caught:
        await git.create_branch(repo, "x", "0" * 40)  # unknown commit
    assert str(repo.repo) not in str(caught.value.details)
    assert caught.value.code == "GIT_ERROR"
    assert caught.value.http_status == 409


# --- task 2: worktrees, diff, merge -----------------------------------------------------------------------


async def test_worktree_commit_does_not_touch_main(git: GitAdapter, repo: GitTarget) -> None:
    base = await git.current_commit(repo)
    assert base is not None
    wt_path = repo.repo.parent / "tasks" / "t1"
    task = await git.create_worktree(repo.repo, wt_path, "kk/task-0a1b2c3d", base)
    assert task == GitTarget(repo.repo, wt_path)
    assert (await git.status(task)).branch == "kk/task-0a1b2c3d"
    write(wt_path / "f.txt", "a\nb\nc\nd\n")
    write(wt_path / "new.py", "x = 1\n")
    sha = await git.commit_all(task, "task work", ME)
    assert sha is not None
    assert (repo.repo / "f.txt").read_text() == "a\nb\nc\n"
    assert (await git.status(repo)).clean is True
    diff = await git.diff(repo, base=base, head="kk/task-0a1b2c3d")
    assert {(f.path, f.status) for f in diff.files} == {("f.txt", "modified"), ("new.py", "added")}
    assert (diff.stats.files, diff.stats.additions, diff.stats.deletions) == (2, 2, 0)


async def test_working_tree_diff_includes_untracked_and_binary(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "f.txt", "a\nB\nc\n")
    write(repo.repo / "docs" / "café note.md", "hello\n")
    (repo.repo / "img.bin").write_bytes(b"\x00\x01\x02")
    index = repo.repo / ".git" / "index"
    before = index.read_bytes()
    diff = await git.diff(repo)
    assert index.read_bytes() == before  # the user's index is never touched
    files = {f.path: f for f in diff.files}
    assert files["docs/café note.md"].status == "added"
    assert files["docs/café note.md"].old_path is None
    assert files["img.bin"].is_binary is True
    assert files["img.bin"].hunks == []
    modified = files["f.txt"]
    assert (modified.status, modified.old_path, modified.additions, modified.deletions) == (
        "modified",
        "f.txt",
        1,
        1,
    )
    hunk = modified.hunks[0]
    assert (hunk.old_start, hunk.old_lines, hunk.new_start, hunk.new_lines) == (1, 3, 1, 3)
    assert [(ln.type, ln.old_no, ln.new_no, ln.text) for ln in hunk.lines] == [
        ("context", 1, 1, "a"),
        ("del", 2, None, "b"),
        ("add", None, 2, "B"),
        ("context", 3, 3, "c"),
    ]
    assert diff.truncated is False
    assert diff.head is None


async def test_diff_unborn_repo_uses_empty_tree(tmp_path: Path, git: GitAdapter) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    await git.init(root, "main")
    write(root / "a.txt", "a\n")
    diff = await git.diff(GitTarget(root))
    assert [(f.path, f.status) for f in diff.files] == [("a.txt", "added")]


async def test_diff_paths_filter_and_truncation(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "f.txt", "".join(f"line {i}\n" for i in range(500)))
    write(repo.repo / "keep.md", "changed\n")
    only = await git.diff(repo, paths=["keep.md"])
    assert [f.path for f in only.files] == ["keep.md"]
    small = await git.diff(repo, max_bytes=200)
    assert small.truncated is True
    big = next(f for f in small.files if f.path == "f.txt")
    assert big.additions == 500  # counts come from the full diff even when lines are cut


async def test_merge_clean(git: GitAdapter, repo: GitTarget) -> None:
    base = await git.current_commit(repo)
    assert base is not None
    task = await git.create_worktree(repo.repo, repo.repo.parent / "tasks" / "t1", "kk/task-00000001", base)
    write(task.worktree / "new.py", "x = 1\n")  # type: ignore[operator]
    await git.commit_all(task, "task", ME)
    result = await git.merge_branch(repo.repo, "kk/task-00000001", "apply task", ME)
    assert (result.merged, result.reason, result.conflicts) == (True, "merged", [])
    assert result.commit == await git.current_commit(repo)
    assert (repo.repo / "new.py").read_text() == "x = 1\n"
    again = await git.merge_branch(repo.repo, "kk/task-00000001", "apply task", ME)
    assert (again.merged, again.reason) == (False, "up_to_date")


async def test_merge_conflict_aborts(git: GitAdapter, repo: GitTarget) -> None:
    base = await git.current_commit(repo)
    assert base is not None
    task = await git.create_worktree(repo.repo, repo.repo.parent / "tasks" / "t1", "kk/task-00000002", base)
    write(task.worktree / "f.txt", "a\nTASK\nc\n")  # type: ignore[operator]
    await git.commit_all(task, "task edit", ME)
    write(repo.repo / "f.txt", "a\nUSER\nc\n")
    head = await git.commit_all(repo, "user edit", ME)
    result = await git.merge_branch(repo.repo, "kk/task-00000002", "apply task", ME)
    assert (result.merged, result.reason, result.conflicts, result.commit) == (
        False,
        "conflict",
        ["f.txt"],
        None,
    )
    assert (repo.repo / "f.txt").read_text() == "a\nUSER\nc\n"
    assert (await git.status(repo)).clean is True
    assert await git.current_commit(repo) == head


async def test_merge_refuses_dirty_tree(git: GitAdapter, repo: GitTarget) -> None:
    base = await git.current_commit(repo)
    assert base is not None
    task = await git.create_worktree(repo.repo, repo.repo.parent / "tasks" / "t1", "kk/task-00000003", base)
    write(task.worktree / "f.txt", "task\n")  # type: ignore[operator]
    await git.commit_all(task, "task", ME)
    write(repo.repo / "f.txt", "uncommitted user edit\n")
    result = await git.merge_branch(repo.repo, "kk/task-00000003", "apply", ME)
    assert (result.merged, result.reason) == (False, "dirty")
    assert (repo.repo / "f.txt").read_text() == "uncommitted user edit\n"


async def test_merge_refuses_untracked_overwrite(git: GitAdapter, repo: GitTarget) -> None:
    base = await git.current_commit(repo)
    assert base is not None
    task = await git.create_worktree(repo.repo, repo.repo.parent / "tasks" / "t1", "kk/task-00000004", base)
    write(task.worktree / "notes.txt", "from task\n")  # type: ignore[operator]
    await git.commit_all(task, "task", ME)
    write(repo.repo / "notes.txt", "user's own untracked notes\n")
    result = await git.merge_branch(repo.repo, "kk/task-00000004", "apply", ME)
    assert (result.merged, result.reason) == (False, "dirty")
    assert (repo.repo / "notes.txt").read_text() == "user's own untracked notes\n"


async def test_remove_worktree(git: GitAdapter, repo: GitTarget) -> None:
    base = await git.current_commit(repo)
    assert base is not None
    path = repo.repo.parent / "tasks" / "t1"
    await git.create_worktree(repo.repo, path, "kk/task-00000005", base)
    write(path / "dirty.txt", "uncommitted\n")
    await git.remove_worktree(repo.repo, path)
    assert not path.exists()
    assert "kk/task-00000005" in {b.name for b in await git.branches(repo)}  # branch kept for the audit trail
    registered = repo.repo / ".git" / "worktrees"
    assert not registered.exists() or os.listdir(registered) == []


async def test_workspace_temp_files_are_never_committed(git: GitAdapter, repo: GitTarget) -> None:
    from app.workspace.fs import TEMP_PREFIX

    write(repo.repo / f"{TEMP_PREFIX}0123abcd", "half-written\n")
    write(repo.repo / "real.txt", "real\n")
    await git.commit_all(repo, "add real", ME)
    status = await git.status(repo)
    assert status.clean is True
    parent = (await git.log(repo, 2))[1].commit
    diff = await git.diff(repo, base=parent, head="main")
    assert [f.path for f in diff.files] == ["real.txt"]
