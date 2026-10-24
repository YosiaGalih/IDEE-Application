"""Workspace file operations (FR-028, FR-029, FR-032, SECURITY_MODEL T2/T9)."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.workspace import fs
from app.workspace.errors import (
    AlreadyExists,
    FileTooLarge,
    PathViolation,
    VersionConflict,
    WorkspaceNotFound,
)
from app.workspace.fs import ANY_VERSION, Workspace
from app.workspace.policy import Access

EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("one\ntwo\nthree\nfour\n")
    return root


@pytest.fixture
def ws(root: Path) -> Workspace:
    return Workspace(root)


def temp_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.name.startswith(fs.TEMP_PREFIX)]


# --- read ---------------------------------------------------------------------------------------------------


async def test_read_text(ws: Workspace) -> None:
    got = await ws.read_file("src/a.py", Access.USER_READ)
    assert got.content == "one\ntwo\nthree\nfour\n"
    assert got.encoding == "utf-8"
    assert got.size == 19
    assert got.sha256 == sha(b"one\ntwo\nthree\nfour\n")
    assert got.too_large is False
    assert got.path == "src/a.py"


async def test_read_binary_returns_metadata(ws: Workspace, root: Path) -> None:
    (root / "img.bin").write_bytes(b"PNG\x00\x01\x02")
    got = await ws.read_file("img.bin", Access.USER_READ)
    assert (got.content, got.encoding, got.size) == (None, "binary", 6)
    assert got.sha256 == sha(b"PNG\x00\x01\x02")


async def test_read_invalid_utf8_is_binary(ws: Workspace, root: Path) -> None:
    (root / "latin1.txt").write_bytes("café".encode("latin-1"))
    got = await ws.read_file("latin1.txt", Access.USER_READ)
    assert (got.content, got.encoding) == (None, "binary")


async def test_read_too_large(ws: Workspace, root: Path) -> None:
    data = b"x" * (fs.USER_READ_LIMIT + 1)
    (root / "big.txt").write_bytes(data)
    got = await ws.read_file("big.txt", Access.USER_READ)
    assert (got.content, got.too_large, got.size) == (None, True, len(data))
    assert got.sha256 == sha(data)
    agent = await ws.read_file("src/a.py", Access.AGENT_READ, max_bytes=5)
    assert agent.too_large is True


async def test_agent_read_limit_is_smaller(ws: Workspace, root: Path) -> None:
    (root / "mid.txt").write_bytes(b"y" * (fs.AGENT_READ_LIMIT + 1))
    assert (await ws.read_file("mid.txt", Access.USER_READ)).too_large is False
    assert (await ws.read_file("mid.txt", Access.AGENT_READ)).too_large is True


async def test_read_line_range(ws: Workspace) -> None:
    got = await ws.read_file("src/a.py", Access.AGENT_READ, start_line=2, end_line=3)
    assert got.content == "two\nthree\n"
    assert got.size == 19  # size and sha describe the whole file


@pytest.mark.parametrize(("start", "end"), [(0, 2), (3, 2), (None, 0)])
async def test_read_bad_line_range(ws: Workspace, start: int | None, end: int | None) -> None:
    with pytest.raises(AppError) as caught:
        await ws.read_file("src/a.py", Access.AGENT_READ, start_line=start, end_line=end)
    assert caught.value.code == "BAD_REQUEST"


async def test_read_missing_404(ws: Workspace) -> None:
    with pytest.raises(WorkspaceNotFound):
        await ws.read_file("src/missing.py", Access.USER_READ)
    with pytest.raises(WorkspaceNotFound):
        await ws.read_file("nope/missing.py", Access.USER_READ)


async def test_read_dir_is_not_regular(ws: Workspace) -> None:
    with pytest.raises(PathViolation) as caught:
        await ws.read_file("src", Access.USER_READ)
    assert caught.value.reason == "not_regular"


async def test_read_fifo_does_not_block(ws: Workspace, root: Path) -> None:
    """A FIFO planted in the worktree (e.g. by sandboxed code) must not hang the reader."""
    os.mkfifo(root / "pipe")
    with pytest.raises(PathViolation) as caught:
        await asyncio.wait_for(ws.read_file("pipe", Access.USER_READ), timeout=5)
    assert caught.value.reason == "not_regular"


async def test_read_empty_file(ws: Workspace, root: Path) -> None:
    (root / "empty.txt").write_bytes(b"")
    got = await ws.read_file("empty.txt", Access.USER_READ)
    assert (got.content, got.size, got.sha256) == ("", 0, EMPTY_SHA)
    change = await ws.write_file("empty.txt", "now\n", Access.USER_WRITE, expected_sha256=EMPTY_SHA)
    assert change.change == "modified"


async def test_unicode_names(ws: Workspace, root: Path) -> None:
    await ws.write_file("docs/café.md", "# Café\n", Access.USER_WRITE, expected_sha256=None)
    assert (root / "docs" / "café.md").read_text() == "# Café\n"
    assert (await ws.read_file("docs/café.md", Access.AGENT_READ)).content == "# Café\n"


async def test_agent_cannot_read_env_user_can(ws: Workspace, root: Path) -> None:
    (root / ".env").write_text("TOKEN=synthetic\n")
    assert (await ws.read_file(".env", Access.USER_READ)).content == "TOKEN=synthetic\n"
    with pytest.raises(PathViolation):
        await ws.read_file(".env", Access.AGENT_READ)


async def test_agentignore_is_enforced(ws: Workspace, root: Path) -> None:
    (root / ".agentignore").write_text("private/\n")
    (root / "private").mkdir()
    (root / "private" / "notes.md").write_text("hidden\n")
    with pytest.raises(PathViolation):
        await ws.read_file("private/notes.md", Access.AGENT_READ)
    assert (await ws.read_file("private/notes.md", Access.USER_READ)).content == "hidden\n"


async def test_unreadable_agentignore_fails_closed(ws: Workspace, root: Path) -> None:
    (root / ".agentignore").mkdir()
    with pytest.raises(PathViolation) as caught:
        await ws.read_file("src/a.py", Access.AGENT_READ)
    assert caught.value.reason == "agentignore"
    assert (await ws.read_file("src/a.py", Access.USER_READ)).size == 19


# --- write --------------------------------------------------------------------------------------------------


async def test_write_create_requires_absent(ws: Workspace) -> None:
    change = await ws.write_file("src/new.py", "x = 1\n", Access.USER_WRITE, expected_sha256=None)
    assert (change.change, change.old_sha256, change.new_sha256) == ("created", None, sha(b"x = 1\n"))
    with pytest.raises(VersionConflict) as caught:
        await ws.write_file("src/new.py", "x = 2\n", Access.USER_WRITE, expected_sha256=None)
    assert caught.value.current_sha256 == sha(b"x = 1\n")


async def test_write_modify_requires_matching_sha(ws: Workspace, root: Path) -> None:
    old = sha(b"one\ntwo\nthree\nfour\n")
    with pytest.raises(VersionConflict) as caught:
        await ws.write_file("src/a.py", "stale\n", Access.USER_WRITE, expected_sha256="0" * 64)
    assert caught.value.current_sha256 == old
    assert (root / "src" / "a.py").read_text() == "one\ntwo\nthree\nfour\n"
    change = await ws.write_file("src/a.py", "fresh\n", Access.USER_WRITE, expected_sha256=old)
    assert (change.change, change.old_sha256, change.size) == ("modified", old, 6)
    assert (root / "src" / "a.py").read_text() == "fresh\n"


async def test_write_expected_sha_for_missing_file_conflicts(ws: Workspace, root: Path) -> None:
    with pytest.raises(VersionConflict) as caught:
        await ws.write_file("gone/x.py", "x\n", Access.USER_WRITE, expected_sha256="0" * 64)
    assert caught.value.current_sha256 is None
    assert not (root / "gone").exists()  # no directories left behind by a refused write


async def test_write_any_version_overwrites(ws: Workspace, root: Path) -> None:
    await ws.write_file("src/a.py", "agent\n", Access.AGENT_WRITE, expected_sha256=ANY_VERSION)
    assert (root / "src" / "a.py").read_text() == "agent\n"


async def test_write_creates_parents_0755_and_file_0644(ws: Workspace, root: Path) -> None:
    await ws.write_file("a/b/c.txt", "c\n", Access.USER_WRITE, expected_sha256=None)
    assert (root / "a").stat().st_mode & 0o7777 == 0o755
    assert (root / "a" / "b").stat().st_mode & 0o7777 == 0o755
    assert (root / "a" / "b" / "c.txt").stat().st_mode & 0o7777 == 0o644


async def test_write_keeps_mode_without_setuid(ws: Workspace, root: Path) -> None:
    script = root / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o4755)
    await ws.write_file("run.sh", "#!/bin/sh\necho hi\n", Access.USER_WRITE, expected_sha256=ANY_VERSION)
    assert script.stat().st_mode & 0o7777 == 0o755


async def test_write_too_large_413(ws: Workspace) -> None:
    with pytest.raises(FileTooLarge):
        await ws.write_file("big.txt", "x" * (fs.WRITE_LIMIT + 1), Access.USER_WRITE, expected_sha256=None)


async def test_write_onto_directory_not_regular(ws: Workspace) -> None:
    with pytest.raises(PathViolation) as caught:
        await ws.write_file("src", "x\n", Access.USER_WRITE, expected_sha256=ANY_VERSION)
    assert caught.value.reason == "not_regular"


async def test_write_root_is_not_regular(ws: Workspace) -> None:
    with pytest.raises(PathViolation) as caught:
        await ws.write_file(".", "x\n", Access.USER_WRITE, expected_sha256=ANY_VERSION)
    assert caught.value.reason == "not_regular"


async def test_write_leaves_no_temp_files(ws: Workspace, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await ws.write_file("src/ok.py", "ok\n", Access.USER_WRITE, expected_sha256=None)
    assert temp_files(root) == []

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        await ws.write_file("src/a.py", "lost\n", Access.USER_WRITE, expected_sha256=ANY_VERSION)
    assert temp_files(root) == []
    assert (root / "src" / "a.py").read_text() == "one\ntwo\nthree\nfour\n"


@pytest.mark.parametrize(
    ("rel", "access"),
    [
        (".git/config", Access.USER_WRITE),
        (".env", Access.USER_WRITE),
        (".agentignore", Access.AGENT_WRITE),
        ("keys/deploy.pem", Access.AGENT_WRITE),
    ],
)
async def test_write_denied_paths(ws: Workspace, root: Path, rel: str, access: Access) -> None:
    with pytest.raises(PathViolation) as caught:
        await ws.write_file(rel, "x\n", access, expected_sha256=ANY_VERSION)
    assert caught.value.reason == "denied"
    assert not (root / rel).exists()


async def test_user_may_write_agentignore(ws: Workspace, root: Path) -> None:
    await ws.write_file(".agentignore", "secrets/\n", Access.USER_WRITE, expected_sha256=None)
    assert (root / ".agentignore").read_text() == "secrets/\n"


async def test_write_through_symlink_rejected(ws: Workspace, root: Path, tmp_path: Path) -> None:
    os.symlink("src", root / "inner")
    os.symlink("src/a.py", root / "alias.py")
    for rel in ("inner/a.py", "alias.py"):
        with pytest.raises(PathViolation) as caught:
            await ws.write_file(rel, "x\n", Access.USER_WRITE, expected_sha256=ANY_VERSION)
        assert caught.value.reason == "symlink"
    assert (root / "src" / "a.py").read_text() == "one\ntwo\nthree\nfour\n"


async def test_concurrent_writers_one_conflict(
    ws: Workspace, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version check and replace are one step: of N writers holding the same sha exactly one wins."""
    original = fs._check_version

    def slow_check(current: fs.Current | None, expected: object) -> None:
        import time

        time.sleep(0.05)  # widens the window a missing lock would let the writers through
        original(current, expected)  # type: ignore[arg-type]

    monkeypatch.setattr(fs, "_check_version", slow_check)
    old = sha(b"one\ntwo\nthree\nfour\n")
    results = await asyncio.gather(
        *(ws.write_file("src/a.py", f"w{i}\n", Access.USER_WRITE, expected_sha256=old) for i in range(8)),
        return_exceptions=True,
    )
    wins = [r for r in results if not isinstance(r, BaseException)]
    conflicts = [r for r in results if isinstance(r, VersionConflict)]
    assert (len(wins), len(conflicts)) == (1, 7)


# --- delete / mkdir -----------------------------------------------------------------------------------------


async def test_delete_requires_sha(ws: Workspace, root: Path) -> None:
    with pytest.raises(VersionConflict):
        await ws.delete_file("src/a.py", Access.USER_WRITE, expected_sha256="0" * 64)
    assert (root / "src" / "a.py").exists()
    change = await ws.delete_file(
        "src/a.py", Access.USER_WRITE, expected_sha256=sha(b"one\ntwo\nthree\nfour\n")
    )
    assert (change.change, change.new_sha256) == ("deleted", None)
    assert not (root / "src" / "a.py").exists()
    with pytest.raises(WorkspaceNotFound):
        await ws.delete_file("src/a.py", Access.USER_WRITE, expected_sha256=ANY_VERSION)


async def test_delete_any_version(ws: Workspace, root: Path) -> None:
    await ws.delete_file("src/a.py", Access.AGENT_WRITE, expected_sha256=ANY_VERSION)
    assert not (root / "src" / "a.py").exists()


async def test_delete_directory_is_not_regular(ws: Workspace) -> None:
    with pytest.raises(PathViolation):
        await ws.delete_file("src", Access.USER_WRITE, expected_sha256=ANY_VERSION)


async def test_make_dir(ws: Workspace, root: Path) -> None:
    assert await ws.make_dir("docs/guides/", Access.USER_WRITE) == "docs/guides"
    assert (root / "docs" / "guides").stat().st_mode & 0o7777 == 0o755
    with pytest.raises(AlreadyExists):
        await ws.make_dir("docs/guides", Access.USER_WRITE)
    with pytest.raises(PathViolation):
        await ws.make_dir(".git/hooks", Access.USER_WRITE)


async def test_too_large_text_cut_inside_a_multibyte_char_stays_text(ws: Workspace, root: Path) -> None:
    data = ("a" * (fs._SNIFF_BYTES - 1) + "é" * 10).encode()  # "é" straddles the 8 KiB sniff boundary
    (root / "long.txt").write_bytes(data)
    got = await ws.read_file("long.txt", Access.USER_READ, max_bytes=100)
    assert (got.too_large, got.encoding) == (True, "utf-8")
