"""GitAdapter against real git in tmp folders (FR-113..116, ADR-016, AC-09; TEST_STRATEGY §2.2)."""

from __future__ import annotations

import asyncio
import os
import stat
import time
from pathlib import Path

import pytest

from app.gitops.adapter import Author, GitAdapter, GitTarget
from app.gitops.errors import GitError, GitInputError

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
    result = await git.merge_branch(repo.repo, "kk/task-00000001", "apply task", ME, into="main")
    assert (result.merged, result.reason, result.conflicts) == (True, "merged", [])
    assert result.commit == await git.current_commit(repo)
    assert (repo.repo / "new.py").read_text() == "x = 1\n"
    again = await git.merge_branch(repo.repo, "kk/task-00000001", "apply task", ME, into="main")
    assert (again.merged, again.reason) == (False, "up_to_date")


async def test_merge_conflict_aborts(git: GitAdapter, repo: GitTarget) -> None:
    base = await git.current_commit(repo)
    assert base is not None
    task = await git.create_worktree(repo.repo, repo.repo.parent / "tasks" / "t1", "kk/task-00000002", base)
    write(task.worktree / "f.txt", "a\nTASK\nc\n")  # type: ignore[operator]
    await git.commit_all(task, "task edit", ME)
    write(repo.repo / "f.txt", "a\nUSER\nc\n")
    head = await git.commit_all(repo, "user edit", ME)
    result = await git.merge_branch(repo.repo, "kk/task-00000002", "apply task", ME, into="main")
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
    result = await git.merge_branch(repo.repo, "kk/task-00000003", "apply", ME, into="main")
    assert (result.merged, result.reason) == (False, "dirty")
    assert (repo.repo / "f.txt").read_text() == "uncommitted user edit\n"


async def test_merge_refuses_untracked_overwrite(git: GitAdapter, repo: GitTarget) -> None:
    base = await git.current_commit(repo)
    assert base is not None
    task = await git.create_worktree(repo.repo, repo.repo.parent / "tasks" / "t1", "kk/task-00000004", base)
    write(task.worktree / "notes.txt", "from task\n")  # type: ignore[operator]
    await git.commit_all(task, "task", ME)
    write(repo.repo / "notes.txt", "user's own untracked notes\n")
    result = await git.merge_branch(repo.repo, "kk/task-00000004", "apply", ME, into="main")
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


# --- review regressions (WP-1.2c) ---------------------------------------------------------------------------


async def test_type_change_is_one_modified_file(git: GitAdapter, repo: GitTarget) -> None:
    """A file replaced by a symlink is one raw entry but two patch blocks (backend re-review)."""
    (repo.repo / "keep.md").unlink()
    os.symlink("f.txt", repo.repo / "keep.md")
    diff = await git.diff(repo)
    assert [(f.path, f.status) for f in diff.files] == [("keep.md", "modified")]
    assert diff.files[0].deletions == 1 and diff.files[0].additions == 1


async def test_large_diff_truncates_instead_of_failing(git: GitAdapter, repo: GitTarget) -> None:
    small_cap = GitAdapter(max_output=4096)
    write(repo.repo / "big.txt", "".join(f"line {i}\n" for i in range(5000)))
    write(repo.repo / "z_after.txt", "z\n")
    diff = await small_cap.diff(repo)
    assert diff.truncated is True
    assert {f.path for f in diff.files} >= {"big.txt", "z_after.txt"}  # every changed file is still listed


async def test_working_tree_diff_writes_nothing_to_the_repo(git: GitAdapter, repo: GitTarget) -> None:
    store = repo.repo / ".git" / "objects"

    def objects() -> set[str]:
        return {str(p.relative_to(store)) for p in store.rglob("*") if p.is_file()}

    before = objects()
    write(repo.repo / "untracked.txt", "fresh content never committed\n")
    diff = await git.diff(repo)
    assert "untracked.txt" in {f.path for f in diff.files}
    assert objects() == before


async def test_merge_refuses_the_wrong_branch(git: GitAdapter, repo: GitTarget) -> None:
    base = await git.current_commit(repo)
    assert base is not None
    task = await git.create_worktree(repo.repo, repo.repo.parent / "tasks" / "t1", "kk/task-00000006", base)
    write(task.worktree / "x.py", "x\n")  # type: ignore[operator]
    await git.commit_all(task, "task", ME)
    with pytest.raises(GitError) as caught:
        await git.merge_branch(repo.repo, "kk/task-00000006", "apply", ME, into="develop")
    assert caught.value.reason == "wrong_branch"
    assert not (repo.repo / "x.py").exists()


async def test_init_missing_folder(tmp_path: Path, git: GitAdapter) -> None:
    with pytest.raises(GitError) as caught:
        await git.init(tmp_path / "nope", "main")
    assert caught.value.reason == "not_found"


# --- WP-2.1c: commit selected paths, never .kk --------------------------------------------------------------


def raw_git(repo: GitTarget, *args: str) -> str:
    import subprocess  # test helper only: the product runs git through the adapter

    return subprocess.run(  # noqa: S603 - fixed argv, test helper
        ["git", "--git-dir", str(repo.repo / ".git"), "--work-tree", str(repo.repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def test_commit_only_the_given_paths(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "a.txt", "a\n")
    write(repo.repo / "b.txt", "b\n")
    sha = await git.commit(repo, "only a", ME, paths=["a.txt"])
    assert sha is not None
    assert raw_git(repo, "show", "--name-only", "--format=", "HEAD").splitlines() == ["a.txt"]
    assert "b.txt" in raw_git(repo, "status", "--porcelain")


async def test_commit_never_stages_kk_even_when_tracked(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / ".kk" / "old.xml", "<old/>\n")
    raw_git(repo, "add", "-f", ".kk/old.xml")
    raw_git(repo, "-c", "user.name=x", "-c", "user.email=x@example.test", "commit", "-q", "-m", "tracked kk")
    write(repo.repo / ".kk" / "old.xml", "<changed/>\n")
    write(repo.repo / ".kk" / "new.xml", "<new/>\n")
    write(repo.repo / "x.py", "print(1)\n")
    sha = await git.commit(repo, "code only", ME)
    assert sha is not None
    assert raw_git(repo, "show", "--name-only", "--format=", "HEAD").splitlines() == ["x.py"]


async def test_commit_with_only_kk_changes_is_nothing(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / ".kk" / "junit.xml", "<testsuite/>\n")
    assert await git.commit(repo, "nothing", ME) is None


# --- WP-2.1c review round: index hygiene, .kk, timeouts, evidence -------------------------------------------


async def test_a_stale_staged_file_never_rides_a_paths_commit(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "a.txt", "a\n")
    write(repo.repo / "b.txt", "secret\n")
    raw_git(repo, "add", "b.txt")  # left staged by an earlier, failed run
    await git.commit(repo, "only a", ME, paths=["a.txt"])
    assert raw_git(repo, "show", "--name-only", "--format=", "HEAD").splitlines() == ["a.txt"]
    assert raw_git(repo, "diff", "--cached", "--name-only") == ""


async def test_a_failed_commit_leaves_nothing_staged(
    git: GitAdapter, repo: GitTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(repo.repo / "a.txt", "a\n")
    run = GitAdapter._run  # pyright: ignore[reportPrivateUsage]

    async def failing(self: GitAdapter, args: list[str], **kwargs: object) -> object:
        if args[0] == "commit":
            raise GitError("commit_failed")
        return await run(self, args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(GitAdapter, "_run", failing)
    with pytest.raises(GitError):
        await git.commit(repo, "fails", ME)
    monkeypatch.undo()
    assert raw_git(repo, "diff", "--cached", "--name-only") == ""


async def test_a_blank_commit_message_is_refused(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "a.txt", "a\n")
    with pytest.raises(GitInputError):
        await git.commit(repo, "   \n", ME)
    assert raw_git(repo, "diff", "--cached", "--name-only") == ""


async def test_the_first_commit_never_stages_kk(tmp_path: Path, git: GitAdapter) -> None:
    root = tmp_path / "fresh"
    root.mkdir()
    await git.init(root, "main")
    write(root / ".kk" / "junit.xml", "<x/>\n")
    write(root / "a.py", "print(1)\n")
    target = GitTarget(root)
    assert await git.commit(target, "first", ME) is not None
    assert raw_git(target, "show", "--name-only", "--format=", "HEAD").splitlines() == ["a.py"]


async def test_kk_is_ignored_by_status(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / ".kk" / "junit.xml", "<x/>\n")
    await git.commit(repo, "nothing", ME)  # writes info/exclude
    status = await git.status(repo)
    assert status.clean and status.changes == []


async def test_a_timeout_leaves_no_index_lock(
    git: GitAdapter, repo: GitTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(repo.repo / "a.txt", "a\n")
    run = GitAdapter._run  # pyright: ignore[reportPrivateUsage]
    lock = repo.repo / ".git" / "index.lock"

    async def killed(self: GitAdapter, args: list[str], **kwargs: object) -> object:
        if args[0] == "add":
            lock.write_text("")  # what a git killed mid-add leaves behind
            raise GitError("timeout")
        return await run(self, args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(GitAdapter, "_run", killed)
    with pytest.raises(GitError):
        await git.commit(repo, "slow", ME)
    monkeypatch.undo()
    assert not lock.exists()
    assert await git.commit(repo, "recovered", ME) is not None


@pytest.mark.parametrize(("step", "reason"), [("restore", "timeout"), ("add", "output_too_large")])
async def test_every_kill_leaves_no_index_lock(
    git: GitAdapter, repo: GitTarget, monkeypatch: pytest.MonkeyPatch, step: str, reason: str
) -> None:
    """AG-13 L1: the first reset is a git run too, and an output cap kills git like a timeout does."""
    write(repo.repo / "a.txt", "a\n")
    raw_git(repo, "add", "a.txt")  # left staged by an earlier run: the first reset has work to do
    run = GitAdapter._run  # pyright: ignore[reportPrivateUsage]
    lock = repo.repo / ".git" / "index.lock"

    async def killed(self: GitAdapter, args: list[str], **kwargs: object) -> object:
        if args[0] == step:
            lock.write_text("")
            raise GitError(reason)
        return await run(self, args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(GitAdapter, "_run", killed)
    with pytest.raises(GitError):
        await git.commit(repo, "killed", ME)
    monkeypatch.undo()
    assert not lock.exists()
    assert await git.commit(repo, "recovered", ME) is not None


async def test_a_cancelled_commit_leaves_no_index_lock(
    git: GitAdapter, repo: GitTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AG-13 L1: ``_run`` kills git on a cancel too; the lock it held must not block every later commit."""
    write(repo.repo / "a.txt", "a\n")
    run = GitAdapter._run  # pyright: ignore[reportPrivateUsage]
    lock = repo.repo / ".git" / "index.lock"
    holding = asyncio.Event()

    async def hangs(self: GitAdapter, args: list[str], **kwargs: object) -> object:
        if args[0] == "add":
            lock.write_text("")  # git holds the index lock ...
            holding.set()
            await asyncio.sleep(30)  # ... until the cancel kills it
        return await run(self, args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(GitAdapter, "_run", hangs)
    committing = asyncio.create_task(git.commit(repo, "cancelled", ME))
    await asyncio.wait_for(holding.wait(), 5)
    committing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await committing
    monkeypatch.undo()
    assert not lock.exists()
    assert await git.commit(repo, "recovered", ME) is not None


async def test_a_commit_that_landed_is_returned_though_git_was_stopped(
    git: GitAdapter, repo: GitTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AG-13 M1: git killed after it wrote the ref; HEAD moved, so the commit exists and is the answer."""
    write(repo.repo / "a.txt", "a\n")
    before = await git.current_commit(repo)
    run = GitAdapter._run  # pyright: ignore[reportPrivateUsage]

    async def stopped(self: GitAdapter, args: list[str], **kwargs: object) -> object:
        result = await run(self, args, **kwargs)  # type: ignore[arg-type]
        if args[0] == "commit":
            raise GitError("timeout")
        return result

    monkeypatch.setattr(GitAdapter, "_run", stopped)
    sha = await git.commit(repo, "landed", ME)
    monkeypatch.undo()
    assert sha is not None and sha != before
    assert sha == await git.current_commit(repo)
    assert await git.commit_files(repo, sha) == ["a.txt"]


@pytest.mark.parametrize("foreign", ["another_author", "another_message", "same_identity_other_parent"])
async def test_a_head_moved_by_another_writer_is_not_taken_as_this_commit(
    git: GitAdapter, repo: GitTarget, monkeypatch: pytest.MonkeyPatch, foreign: str
) -> None:
    """AG-13 delta2 LOW: only a commit proven to be this one (parent, author, message) answers a failure."""
    write(repo.repo / "mine.txt", "mine\n")
    before = await git.current_commit(repo)
    run = GitAdapter._run  # pyright: ignore[reportPrivateUsage]

    def move_head() -> str:
        # Each case breaks exactly one of the three proofs: author, message, parent.
        if foreign == "another_author":
            raw_git(repo, "-c", "user.name=Other", "-c", "user.email=other@example.test",
                    "commit", "-q", "--allow-empty", "-m", "mine")  # fmt: skip
        elif foreign == "another_message":
            raw_git(repo, "-c", f"user.name={ME.name}", "-c", f"user.email={ME.email}",
                    "commit", "-q", "--allow-empty", "-m", "foreign")  # fmt: skip
        else:  # our author and message, but not on top of ``before``
            tree = raw_git(repo, "write-tree")
            orphan = raw_git(repo, "-c", f"user.name={ME.name}", "-c", f"user.email={ME.email}",
                             "commit-tree", tree, "-m", "mine")  # fmt: skip
            raw_git(repo, "update-ref", "HEAD", orphan)
        return raw_git(repo, "rev-parse", "HEAD")

    moved: list[str] = []

    async def killed(self: GitAdapter, args: list[str], **kwargs: object) -> object:
        if args[0] == "add":
            moved.append(move_head())  # another writer commits while our git is being killed
            raise GitError("timeout")
        return await run(self, args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(GitAdapter, "_run", killed)
    with pytest.raises(GitError) as raised:
        await git.commit(repo, "mine", ME)
    monkeypatch.undo()
    assert raised.value.reason == "timeout"  # the original error stands
    assert moved and moved[0] != before
    assert "mine.txt" not in raw_git(repo, "ls-tree", "-r", "--name-only", "HEAD").split()


async def test_an_unborn_index_that_differs_from_the_file_is_reset(tmp_path: Path, git: GitAdapter) -> None:
    """AG-13 I-d: ``rm --cached`` refuses an entry that differs from both the file and HEAD."""
    root = tmp_path / "repo"
    root.mkdir()
    await git.init(root, "main")
    target = GitTarget(root)
    write(root / "a.txt", "staged\n")
    raw_git(target, "add", "a.txt")
    write(root / "a.txt", "edited after staging\n")
    write(root / "b.txt", "b\n")
    sha = await git.commit(target, "first", ME, paths=["b.txt"])
    assert sha is not None
    assert await git.commit_files(target, sha) == ["b.txt"]


async def test_commit_files_lists_what_the_commit_holds(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "a.txt", "a\n")
    write(repo.repo / "d" / "b.txt", "b\n")
    sha = await git.commit(repo, "two", ME)
    assert sha is not None
    assert await git.commit_files(repo, sha) == ["a.txt", "d/b.txt"]


# --- WP-2.1d: list a commit's files, grep a tree or a commit ----------------------------------------------


async def test_list_files_lists_blobs_of_a_commit(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "src" / "app.py", "print('x')\n")
    os.symlink("f.txt", repo.repo / "link")
    sha = await git.commit(repo, "files", ME)
    assert sha is not None
    listing = await git.list_files(repo, sha)
    assert (listing.truncated, listing.skipped_paths) == (False, 0)
    assert {(f.path, f.size) for f in listing.files} == {("f.txt", 6), ("keep.md", 5), ("src/app.py", 11)}


async def test_list_files_counts_paths_that_are_not_utf8(git: GitAdapter, repo: GitTarget) -> None:
    (repo.repo / os.fsdecode(b"\xff.txt")).write_text("x\n")
    sha = await git.commit(repo, "latin-1 name", ME)
    assert sha is not None
    listing = await git.list_files(repo, sha)
    assert listing.skipped_paths == 1
    assert "f.txt" in {f.path for f in listing.files}


async def test_grep_finds_text_in_the_working_tree_including_untracked(
    git: GitAdapter, repo: GitTarget
) -> None:
    write(repo.repo / "new.txt", "one\nsay Hello there\n")
    result = await git.grep(repo, ["Hello"], commit=None, fixed=True, ignore_case=False,
                            only_matching=False, timeout_s=10, max_output=1 << 20)  # fmt: skip
    assert [(m.path, m.line, m.column, m.text) for m in result.matches] == [
        ("new.txt", 2, 5, "say Hello there")
    ]
    assert not result.truncated


async def test_grep_is_literal_unless_regex(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "x.txt", "axb\n")
    literal = await git.grep(repo, ["a.b"], commit=None, fixed=True, ignore_case=False,
                             only_matching=False, timeout_s=10, max_output=1 << 20)  # fmt: skip
    regex = await git.grep(repo, ["a.b"], commit=None, fixed=False, ignore_case=False,
                           only_matching=False, timeout_s=10, max_output=1 << 20)  # fmt: skip
    assert literal.matches == []
    assert [m.path for m in regex.matches] == ["x.txt"]


async def test_grep_ignore_case(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "x.txt", "MiXeD\n")
    result = await git.grep(repo, ["mixed"], commit=None, fixed=True, ignore_case=True,
                            only_matching=False, timeout_s=10, max_output=1 << 20)  # fmt: skip
    assert [m.text for m in result.matches] == ["MiXeD"]


async def test_grep_on_a_commit_reads_the_tree_not_the_files(git: GitAdapter, repo: GitTarget) -> None:
    head = await git.current_commit(repo)
    assert head is not None
    write(repo.repo / "f.txt", "changed on disk\n")
    result = await git.grep(repo, ["b"], commit=head, fixed=True, ignore_case=False,
                            only_matching=False, timeout_s=10, max_output=1 << 20)  # fmt: skip
    assert [(m.path, m.line, m.column, m.text) for m in result.matches] == [("f.txt", 2, None, "b")]


async def test_grep_only_matching_prints_the_match(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "big.js", "function   huge" + "x" * (1 << 20) + "\n")
    sha = await git.commit(repo, "big", ME)
    assert sha is not None
    pattern = "^function[[:space:]]+[a-z]{4}"
    result = await git.grep(repo, [pattern], commit=sha, fixed=False, ignore_case=False,
                            only_matching=True, timeout_s=10, max_output=1 << 16)  # fmt: skip
    assert [m.text for m in result.matches] == ["function   huge"]


async def test_grep_no_match_is_empty(git: GitAdapter, repo: GitTarget) -> None:
    result = await git.grep(repo, ["nowhere-to-be-found"], commit=None, fixed=True, ignore_case=False,
                            only_matching=False, timeout_s=10, max_output=1 << 20)  # fmt: skip
    assert (result.matches, result.truncated, result.skipped_paths) == ([], False, 0)


async def test_grep_invalid_regex_is_an_input_error(git: GitAdapter, repo: GitTarget) -> None:
    with pytest.raises(GitInputError) as raised:
        await git.grep(repo, ["("], commit=None, fixed=False, ignore_case=False,
                       only_matching=False, timeout_s=10, max_output=1 << 20)  # fmt: skip
    assert raised.value.reason == "regex"


async def test_grep_paths_with_colons_and_spaces(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "we:ird" / "a b.txt", "needle\n")
    result = await git.grep(repo, ["needle"], commit=None, fixed=True, ignore_case=False,
                            only_matching=False, timeout_s=10, max_output=1 << 20)  # fmt: skip
    assert [m.path for m in result.matches] == ["we:ird/a b.txt"]


async def test_grep_skips_paths_that_are_not_utf8(git: GitAdapter, repo: GitTarget) -> None:
    (repo.repo / os.fsdecode(b"\xff.txt")).write_text("needle\n")
    write(repo.repo / "ok.txt", "needle\n")
    result = await git.grep(repo, ["needle"], commit=None, fixed=True, ignore_case=False,
                            only_matching=False, timeout_s=10, max_output=1 << 20)  # fmt: skip
    assert [m.path for m in result.matches] == ["ok.txt"]
    assert result.skipped_paths == 1


async def test_grep_output_cap_truncates(git: GitAdapter, repo: GitTarget) -> None:
    write(repo.repo / "many.txt", "needle line\n" * 100)
    result = await git.grep(repo, ["needle"], commit=None, fixed=True, ignore_case=False,
                            only_matching=False, timeout_s=10, max_output=200)  # fmt: skip
    assert result.truncated
    assert 0 < len(result.matches) < 100
    assert all(m.text == "needle line" for m in result.matches)  # no partial record


async def test_grep_timeout_is_a_git_error(git: GitAdapter, repo: GitTarget) -> None:
    for index in range(200):
        write(repo.repo / f"d{index}" / "f.txt", "needle\n" * 50)
    with pytest.raises(GitError) as raised:
        await git.grep(repo, ["(n|e)*d"], commit=None, fixed=False, ignore_case=False,
                       only_matching=False, timeout_s=0.001, max_output=1 << 20)  # fmt: skip
    assert raised.value.reason == "timeout"
