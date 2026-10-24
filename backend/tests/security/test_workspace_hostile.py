"""A hostile worktree cannot freeze, block, or exhaust the backend (WP-1.2a security review, T2/T5/T9).

Sandboxed code can write anything in a task worktree: ``.agentignore``, sparse files, sockets, locks.
"""

from __future__ import annotations

import fcntl
import os
import socket
import time
from pathlib import Path

import pytest

from app.workspace import fs
from app.workspace.errors import FileTooLarge, PathViolation, WorkspaceBusy
from app.workspace.fs import ANY_VERSION, TaskWorkspace, Workspace
from app.workspace.policy import MAX_PATTERNS, Access, Policy

GIB = 1024 * 1024 * 1024


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "project" / "tasks" / "t1"
    root.mkdir(parents=True)
    (root / "a.py").write_text("x = 1\n")
    return root


def long_name() -> str:
    return "a" * 200 + ".py"


@pytest.mark.parametrize(
    "agentignore",
    [
        "*a" * 8 + "b\n",  # at the wildcard cap: must stay fast
        "*?" * 4 + "x\n",
        "**/" + "*a" * 7 + "b\n",
        "**/x/**/" + "*?" * 3 + "y\n",
    ],
)
async def test_agentignore_patterns_cannot_freeze_matching(root: Path, agentignore: str) -> None:
    (root / ".agentignore").write_text(agentignore)
    (root / long_name()).write_text("x\n")
    started = time.monotonic()
    await Workspace(root).read_file(long_name(), Access.AGENT_READ)
    assert time.monotonic() - started < 1.0


def test_worst_case_policy_is_bounded() -> None:
    patterns = "".join(f"d{i}*/**/*x*y*z/q{i}/**\n" for i in range(MAX_PATTERNS))
    policy = Policy.from_agentignore(patterns)
    deep = tuple(["d"] * 127 + ["file.txt"])
    started = time.monotonic()
    for _ in range(10):
        policy.denied(deep, Access.AGENT_READ)
    assert time.monotonic() - started < 2.0  # a 200-file patch under the lock stays well under a minute


@pytest.mark.parametrize(
    "agentignore",
    [
        "*a" * 10 + "b\n",  # over the wildcard cap
        "x" * 300 + "\n",  # over the pattern length cap
        "".join(f"p{i}\n" for i in range(MAX_PATTERNS + 1)),  # over the pattern count cap
        "/**/a/**/b/**/c\n",  # over the ** cap
    ],
)
async def test_over_cap_agentignore_fails_closed_for_agents_only(root: Path, agentignore: str) -> None:
    (root / ".agentignore").write_text(agentignore)
    ws = Workspace(root)
    with pytest.raises(PathViolation) as caught:
        await ws.read_file("a.py", Access.AGENT_READ)
    assert caught.value.reason == "agentignore"
    # the owner can still read and repair it
    assert (await ws.read_file("a.py", Access.USER_READ)).content == "x = 1\n"
    await ws.write_file(".agentignore", "", Access.USER_WRITE, expected_sha256=ANY_VERSION)
    assert (await ws.read_file("a.py", Access.AGENT_READ)).content == "x = 1\n"


async def test_sparse_agentignore_is_not_read(root: Path) -> None:
    with (root / ".agentignore").open("wb") as handle:
        handle.truncate(4 * GIB)
    started = time.monotonic()
    with pytest.raises(PathViolation):
        await Workspace(root).read_file("a.py", Access.AGENT_READ)
    assert time.monotonic() - started < 1.0


async def test_sparse_file_is_not_hashed(root: Path) -> None:
    with (root / "huge.bin").open("wb") as handle:
        handle.truncate(4 * GIB)
    ws = Workspace(root)
    started = time.monotonic()
    got = await ws.read_file("huge.bin", Access.AGENT_READ)
    assert time.monotonic() - started < 1.0
    assert (got.sha256, got.too_large, got.size) == (None, True, 4 * GIB)
    with pytest.raises(FileTooLarge):
        await ws.write_file("huge.bin", "small\n", Access.USER_WRITE, expected_sha256="0" * 64)
    with pytest.raises(FileTooLarge):
        await ws.delete_file("huge.bin", Access.USER_WRITE, expected_sha256="0" * 64)
    change = await ws.write_file("huge.bin", "small\n", Access.AGENT_WRITE, expected_sha256=ANY_VERSION)
    assert (change.change, change.old_sha256) == ("modified", None)


async def test_sandbox_lock_on_root_does_not_block_writes(root: Path) -> None:
    """The root is mounted into the sandbox; a flock it takes there must not stall the backend."""
    held = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(held, fcntl.LOCK_EX)
        started = time.monotonic()
        await Workspace(root).write_file("a.py", "y\n", Access.AGENT_WRITE, expected_sha256=ANY_VERSION)
        assert time.monotonic() - started < 1.0
    finally:
        os.close(held)
    lock_dir = root.parent / fs.LOCKS_DIRNAME
    assert lock_dir.is_dir()
    assert not lock_dir.is_relative_to(root)


async def test_lock_wait_is_bounded(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs, "LOCK_WAIT_S", 0.2)
    ws = Workspace(root)
    await ws.write_file(
        "a.py", "warm\n", Access.AGENT_WRITE, expected_sha256=ANY_VERSION
    )  # creates the lock file
    (lock_file,) = (root.parent / fs.LOCKS_DIRNAME).iterdir()
    holder = os.open(lock_file, os.O_RDWR)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(WorkspaceBusy):
            await ws.write_file("a.py", "blocked\n", Access.AGENT_WRITE, expected_sha256=ANY_VERSION)
        assert time.monotonic() - started < 2.0
    finally:
        os.close(holder)


async def test_socket_is_not_a_file(root: Path) -> None:
    server = socket.socket(socket.AF_UNIX)
    try:
        server.bind(str(root / "agent.sock"))
        with pytest.raises(PathViolation) as caught:
            await Workspace(root).read_file("agent.sock", Access.USER_READ)
        assert caught.value.reason == "not_regular"
        listed = await Workspace(root).tree(".", Access.USER_READ)
        assert "agent.sock" not in [e.name for e in listed.entries]
    finally:
        server.close()


async def test_agent_cannot_raise_its_read_limit(root: Path) -> None:
    (root / "big.txt").write_text("y" * (fs.AGENT_READ_LIMIT + 10))
    got = await Workspace(root).read_file("big.txt", Access.AGENT_READ, max_bytes=50 * 1024 * 1024)
    assert (got.too_large, got.content) == (True, None)


async def test_task_workspace_is_agent_bound(root: Path) -> None:
    (root / ".env").write_text("TOKEN=synthetic\n")
    task = TaskWorkspace(root)
    change = await task.write_text("src/new.py", "print('hi')\n")
    assert change.change == "added"
    assert (await task.read_text("src/new.py", start_line=1, end_line=1)).content == "print('hi')\n"
    for call in (task.read_text(".env"), task.write_text(".env", "x"), task.delete(".agentignore")):
        with pytest.raises(PathViolation):
            await call
    assert ".env" not in [e.name for e in (await task.tree()).entries]


@pytest.mark.parametrize(
    ("operation", "access"),
    [
        ("write", Access.USER_READ),
        ("write", Access.AGENT_READ),
        ("read", Access.USER_WRITE),
        ("patch", Access.AGENT_READ),
        ("tree", Access.AGENT_WRITE),
    ],
)
async def test_access_kind_must_match_operation(root: Path, operation: str, access: Access) -> None:
    ws = Workspace(root)
    with pytest.raises(ValueError):
        if operation == "write":
            await ws.write_file(".env", "x", access, expected_sha256=ANY_VERSION)
        elif operation == "read":
            await ws.read_file("a.py", access)
        elif operation == "patch":
            await ws.apply_patch("--- /dev/null\n+++ b/.env\n@@ -0,0 +1 @@\n+x\n", access)
        else:
            await ws.tree(".", access)
    assert not (root / ".env").exists()


async def test_lone_surrogates_are_client_errors(root: Path) -> None:
    ws = Workspace(root)
    with pytest.raises(PathViolation):
        await ws.read_file("bad\ud800.py", Access.USER_READ)
    with pytest.raises(Exception) as caught:
        await ws.write_file("ok.py", "bad\ud800", Access.USER_WRITE, expected_sha256=None)
    assert getattr(caught.value, "code", None) == "BAD_REQUEST"


def _deep_tree(root: Path, depth: int) -> None:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for _ in range(depth):
            os.mkdir("d", dir_fd=fd)
            child = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        with open(os.open("leaf.txt", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd), "w") as handle:
            handle.write("12345")
    finally:
        os.close(fd)


async def test_disk_usage_survives_deep_trees(root: Path) -> None:
    """A tree deeper than the walk cap (sandboxed code can build one) is refused cleanly.

    The walk is iterative, so depth never reaches the interpreter's recursion limit; 300 levels is enough to
    cross ``MAX_WALK_DEPTH`` while keeping pytest's own tmp cleanup (recursive rmtree) safe.
    """
    ws = Workspace(root)
    (root / "shallow").mkdir()
    _deep_tree(root / "shallow", 50)
    assert await ws.disk_usage() == 6 + 5  # a.py + the leaf 50 levels down
    (root / "deep").mkdir()
    _deep_tree(root / "deep", fs.MAX_WALK_DEPTH + 44)
    with pytest.raises(PathViolation) as caught:
        await ws.disk_usage()
    assert caught.value.reason == "too_deep"


# --- security re-review: matching cost and depth (WP-1.2a) ---

UNIFORM = "**/" + "a/" * 60 + "**/" + "a/" * 60 + "z\n"  # within every cap, matches the path all the way


def test_uniform_worst_case_single_call_is_bounded() -> None:
    policy = Policy.from_agentignore(UNIFORM * MAX_PATTERNS)
    deep = tuple(["a"] * 127 + ["f.txt"])
    started = time.monotonic()
    assert policy.denied(deep, Access.AGENT_READ) is True  # budget ran out: fail closed
    assert time.monotonic() - started < 0.5
    assert policy.denied(deep, Access.USER_READ) is False


def _chain(root: Path, name: str, depth: int, files: int = 200) -> Path:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for _ in range(depth):
            os.mkdir(name, dir_fd=fd)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        for i in range(files):
            os.close(os.open(f"f{i}.txt", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd))
    finally:
        os.close(fd)
    return root.joinpath(*([name] * depth))


async def test_tree_with_hostile_agentignore_is_bounded(root: Path) -> None:
    """A sandbox-planted .agentignore cannot make one tree() call cost per-entry work times thousands."""
    (root / ".agentignore").write_text(UNIFORM * MAX_PATTERNS)
    _chain(root, "a", 0, files=3000)  # 3000 entries right under the root
    started = time.monotonic()
    result = await Workspace(root).tree(".", Access.AGENT_READ, depth=1, max_entries=5000)
    assert time.monotonic() - started < 5.0
    assert result.truncated is True  # the shared budget ran out, so the listing says it is incomplete


async def test_link_into_a_deeper_tree_is_too_long(root: Path) -> None:
    """realpath can be deeper than any requested path; the policy never recurses on it (no 500)."""
    (root / ".agentignore").write_text("**/zzz\n")
    deep = _chain(root, "d", 300)
    os.symlink("/".join(["d"] * 300), root / "x")
    assert deep == root.joinpath(*(["d"] * 300))
    with pytest.raises(PathViolation) as caught:
        await Workspace(root).read_file("x/f0.txt", Access.AGENT_READ)
    assert caught.value.reason == "too_long"


def test_offset_search_is_bounded() -> None:
    from app.workspace.patches import HunkConflict, apply_hunks, parse_unified_diff

    old = "x\n" * 100_000
    body = " x\n" * 20_000 + "-missing\n+new\n"
    diff = f"--- a/f\n+++ b/f\n@@ -5,20001 +5,20001 @@\n{body}"
    started = time.monotonic()
    with pytest.raises(HunkConflict):
        apply_hunks(old, parse_unified_diff(diff)[0].hunks)
    assert time.monotonic() - started < 2.0
