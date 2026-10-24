"""Workspace file operations (ARCHITECTURE §4.2 ``app/workspace/fs.py``, §7; FR-028, FR-029, FR-032; T2, T9).

``Workspace(root)`` serves both trees of ARCHITECTURE §7: the user's ``repo/`` and a task worktree.

**Access**
- Every path goes through ``paths.resolve`` (string checks, containment, denylist per ``Access``).
- Files are opened one component at a time without following symlinks.

**Writes**
- Atomic: temp file in the same folder, ``fsync``, then ``os.replace``.
- Version-checked with ``expected_sha256`` (FR-029).
- The version check and the replace run under an ``flock`` on the root fd. That serialises writers across
  threads and processes (several API workers, the Celery worker).

**Async**
- Blocking work runs in ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import hashlib
import os
import secrets
import stat
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from app.core.errors import AppError
from app.core.logging import get_logger
from app.workspace.errors import (
    AlreadyExists,
    FileTooLarge,
    PatchConflict,
    PathViolation,
    VersionConflict,
    WorkspaceNotFound,
)
from app.workspace.patches import FilePatch, HunkConflict, apply_hunks, parse_unified_diff
from app.workspace.paths import open_parent, open_root, resolve
from app.workspace.policy import AGENTIGNORE, Access, Policy

logger = get_logger(__name__)

USER_READ_LIMIT = 1024 * 1024  # [Menebak] what the IDE opens as text
AGENT_READ_LIMIT = 256 * 1024  # ARCHITECTURE §7: larger files never reach the LLM
WRITE_LIMIT = 5 * 1024 * 1024  # [Menebak] per file written through the workspace API
AGENTIGNORE_LIMIT = 64 * 1024
MAX_TREE_DEPTH = 10
MAX_TREE_ENTRIES = 5000
TEMP_PREFIX = ".kk-tmp-"
_SNIFF_BYTES = 8192
_CHUNK = 64 * 1024
# O_NONBLOCK: opening a FIFO planted in a worktree must fail fast (fstat → not regular), never block.
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


class AnyVersion:
    """``expected_sha256=ANY_VERSION``: overwrite without a version check (an agent in its own worktree)."""

    _instance: AnyVersion | None = None

    def __new__(cls) -> AnyVersion:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "ANY_VERSION"


ANY_VERSION: Final = AnyVersion()


@dataclass(frozen=True)
class FileContent:
    path: str
    content: str | None
    encoding: Literal["utf-8", "binary"]
    size: int
    sha256: str
    too_large: bool


@dataclass(frozen=True)
class FileChange:
    path: str
    change: Literal["created", "modified", "deleted"]
    old_sha256: str | None
    new_sha256: str | None
    size: int


@dataclass(frozen=True)
class TreeEntry:
    path: str
    name: str
    type: Literal["file", "dir"]
    size: int | None
    children: list[TreeEntry] | None


@dataclass(frozen=True)
class TreeResult:
    entries: list[TreeEntry]
    truncated: bool


@dataclass(frozen=True)
class Current:
    """The file a write would replace."""

    sha256: str
    mode: int
    size: int


def _bad_request(message: str) -> AppError:
    return AppError("BAD_REQUEST", message)


def _open_file(parent_fd: int, name: str) -> int:
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        raise WorkspaceNotFound() from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PathViolation("symlink") from None
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PathViolation("not_regular")
    return fd


def _hash_fd(fd: int, keep: int) -> tuple[str, int, bytes]:
    """sha256 and size of the whole file, plus its first ``keep`` bytes."""
    digest = hashlib.sha256()
    size = 0
    head = bytearray()
    while chunk := os.read(fd, _CHUNK):
        digest.update(chunk)
        size += len(chunk)
        if len(head) < keep:
            head += chunk[: keep - len(head)]
    return digest.hexdigest(), size, bytes(head)


def _current(parent_fd: int, name: str) -> Current | None:
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        raise PathViolation("symlink")
    if not stat.S_ISREG(st.st_mode):
        raise PathViolation("not_regular")
    fd = _open_file(parent_fd, name)
    try:
        digest, size, _ = _hash_fd(fd, 0)
    finally:
        os.close(fd)
    return Current(digest, stat.S_IMODE(st.st_mode) & 0o777, size)


def _check_version(current: Current | None, expected: str | AnyVersion | None) -> None:
    if isinstance(expected, AnyVersion):
        return
    current_sha = current.sha256 if current else None
    if expected != current_sha:
        raise VersionConflict(current_sha)


def _replace(parent_fd: int, name: str, data: bytes, mode: int) -> None:
    temp = TEMP_PREFIX + secrets.token_hex(8)
    fd = os.open(temp, _TEMP_FLAGS, 0o600, dir_fd=parent_fd)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(temp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=parent_fd)
        raise
    os.fsync(parent_fd)


def _decode(head: bytes, size: int, limit: int) -> tuple[str | None, Literal["utf-8", "binary"]]:
    """Text or binary (NUL or invalid UTF-8 in the first 8 KiB); content only when within ``limit``."""
    if b"\x00" in head[:_SNIFF_BYTES]:
        return None, "binary"
    if size > limit:
        try:
            head[:_SNIFF_BYTES].decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.reason != "unexpected end of data":  # a sequence cut at the sniff boundary is still text
                return None, "binary"
        return None, "utf-8"
    try:
        return head.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return None, "binary"


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    # --- public async API ---

    async def read_file(
        self,
        rel: str,
        access: Access,
        *,
        max_bytes: int | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> FileContent:
        return await asyncio.to_thread(self._read_file, rel, access, max_bytes, start_line, end_line)

    async def write_file(
        self, rel: str, content: str, access: Access, *, expected_sha256: str | AnyVersion | None
    ) -> FileChange:
        return await asyncio.to_thread(self._write_file, rel, content, access, expected_sha256)

    async def delete_file(self, rel: str, access: Access, *, expected_sha256: str | AnyVersion) -> FileChange:
        return await asyncio.to_thread(self._delete_file, rel, access, expected_sha256)

    async def make_dir(self, rel: str, access: Access) -> str:
        return await asyncio.to_thread(self._make_dir, rel, access)

    async def tree(
        self, rel: str = ".", access: Access = Access.USER_READ, *, depth: int = 3, max_entries: int = 500
    ) -> TreeResult:
        depth = min(max(depth, 1), MAX_TREE_DEPTH)
        max_entries = min(max(max_entries, 1), MAX_TREE_ENTRIES)
        return await asyncio.to_thread(self._tree, rel, access, depth, max_entries)

    async def disk_usage(self) -> int:
        """Bytes in regular files under the root, symlinks not followed (quota input, ADR-005)."""
        return await asyncio.to_thread(self._disk_usage)

    async def apply_patch(self, diff: str, access: Access) -> list[FileChange]:
        """Apply a unified diff all-or-nothing: any conflict → ``PatchConflict`` and no file changes."""
        return await asyncio.to_thread(self._apply_patch, diff, access)

    # --- plumbing ---

    @contextlib.contextmanager
    def _opened(self, *, lock: bool = False) -> Iterator[tuple[int, str]]:
        fd, real = open_root(self.root)
        try:
            if lock:
                fcntl.flock(fd, fcntl.LOCK_EX)  # released when the fd is closed
            yield fd, real
        finally:
            os.close(fd)

    def _policy(self, root_fd: int, access: Access) -> Policy:
        """The project's policy. An ``.agentignore`` that cannot be read denies everything but USER_READ."""
        try:
            fd = _open_file(root_fd, AGENTIGNORE)
        except WorkspaceNotFound:
            return Policy()
        except PathViolation:
            text = None
        else:
            try:
                _, size, head = _hash_fd(fd, AGENTIGNORE_LIMIT + 1)
            finally:
                os.close(fd)
            text = head.decode("utf-8", errors="replace") if size <= AGENTIGNORE_LIMIT else None
        if text is None:
            if access is Access.USER_READ:
                return Policy()
            raise PathViolation("agentignore")
        return Policy.from_agentignore(text)

    def _target(self, root_fd: int, real: str, rel: str, access: Access) -> tuple[str, ...]:
        parts = resolve(real, rel, access, self._policy(root_fd, access))
        if not parts:
            raise PathViolation("not_regular")
        return parts

    # --- operations ---

    def _read_file(
        self,
        rel: str,
        access: Access,
        max_bytes: int | None,
        start_line: int | None,
        end_line: int | None,
    ) -> FileContent:
        if (start_line is not None and start_line < 1) or (
            end_line is not None and end_line < (start_line or 1)
        ):
            raise _bad_request("Invalid line range")
        limit = max_bytes if max_bytes is not None else _read_limit(access)
        with self._opened() as (root_fd, real):
            parts = self._target(root_fd, real, rel, access)
            parent = open_parent(root_fd, parts, create=False)
            try:
                fd = _open_file(parent, parts[-1])
            finally:
                os.close(parent)
            try:
                digest, size, head = _hash_fd(fd, limit + 1)
            finally:
                os.close(fd)
        content, encoding = _decode(head, size, limit)
        if content is not None and (start_line is not None or end_line is not None):
            lines = content.splitlines(keepends=True)
            content = "".join(lines[(start_line or 1) - 1 : end_line])
        return FileContent("/".join(parts), content, encoding, size, digest, size > limit)

    def _write_file(
        self, rel: str, content: str, access: Access, expected: str | AnyVersion | None
    ) -> FileChange:
        data = content.encode("utf-8")
        if len(data) > WRITE_LIMIT:
            raise FileTooLarge(WRITE_LIMIT)
        with self._opened(lock=True) as (root_fd, real):
            parts = self._target(root_fd, real, rel, access)
            try:
                parent = open_parent(root_fd, parts, create=False)
            except WorkspaceNotFound:
                _check_version(None, expected)  # a refused write must not leave folders behind
                parent = open_parent(root_fd, parts, create=True)
            try:
                current = _current(parent, parts[-1])
                _check_version(current, expected)
                _replace(parent, parts[-1], data, current.mode if current else 0o644)
            finally:
                os.close(parent)
        return FileChange(
            "/".join(parts),
            "modified" if current else "created",
            current.sha256 if current else None,
            hashlib.sha256(data).hexdigest(),
            len(data),
        )

    def _delete_file(self, rel: str, access: Access, expected: str | AnyVersion) -> FileChange:
        with self._opened(lock=True) as (root_fd, real):
            parts = self._target(root_fd, real, rel, access)
            parent = open_parent(root_fd, parts, create=False)
            try:
                current = _current(parent, parts[-1])
                if current is None:
                    raise WorkspaceNotFound()
                _check_version(current, expected)
                os.unlink(parts[-1], dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(parent)
        return FileChange("/".join(parts), "deleted", current.sha256, None, 0)

    def _make_dir(self, rel: str, access: Access) -> str:
        with self._opened(lock=True) as (root_fd, real):
            parts = self._target(root_fd, real, rel, access)
            parent = open_parent(root_fd, parts, create=True)
            try:
                try:
                    os.mkdir(parts[-1], 0o755, dir_fd=parent)
                except FileExistsError:
                    raise AlreadyExists() from None
                created = os.open(parts[-1], _DIR_FLAGS, dir_fd=parent)
                try:
                    os.fchmod(created, 0o755)  # mkdir's mode is masked by the umask
                finally:
                    os.close(created)
            finally:
                os.close(parent)
        return "/".join(parts)

    def _apply_patch(self, diff: str, access: Access) -> list[FileChange]:
        patches = parse_unified_diff(diff)
        with self._opened(lock=True) as (root_fd, real), contextlib.ExitStack() as fds:
            policy = self._policy(root_fd, access)
            plans: list[_PatchPlan] = []
            conflicts: list[dict[str, object]] = []
            for patch in patches:
                plan = _plan(root_fd, real, patch, access, policy, fds)
                if isinstance(plan, dict):
                    conflicts.append(plan)
                else:
                    plans.append(plan)
            if conflicts:
                raise PatchConflict(conflicts)  # nothing has been written yet
            _commit(root_fd, plans, fds)
        return [plan.change for plan in plans]

    def _tree(self, rel: str, access: Access, depth: int, max_entries: int) -> TreeResult:
        """Breadth-first, so a truncated listing still shows every entry of the upper levels."""
        with self._opened() as (root_fd, real):
            policy = self._policy(root_fd, access)
            start = resolve(real, rel, access, policy)
            top: list[TreeEntry] = []
            queue: deque[tuple[tuple[str, ...], list[TreeEntry], int]] = deque([(start, top, 1)])
            count = 0
            while queue:
                parts, into, level = queue.popleft()
                for entry, child_parts in _list_dir(root_fd, real, parts, access, policy):
                    if count == max_entries:
                        return TreeResult(top, truncated=True)
                    if entry.type == "dir" and child_parts is not None and level < depth:
                        children: list[TreeEntry] = []
                        entry = TreeEntry(entry.path, entry.name, "dir", None, children)
                        queue.append((child_parts, children, level + 1))
                    into.append(entry)
                    count += 1
        return TreeResult(top, truncated=False)

    def _disk_usage(self) -> int:
        fd, real = open_root(self.root)  # raises WorkspaceNotFound for a missing root
        os.close(fd)
        total = 0
        for folder, _dirs, files in os.walk(real, followlinks=False):
            for name in files:
                st = os.lstat(os.path.join(folder, name))
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
        return total


@dataclass
class _PatchPlan:
    parts: tuple[str, ...]
    parent: int | None  # None: the parent folder does not exist yet (an added file)
    current: Current | None
    old: bytes
    new: bytes | None  # None: delete

    @property
    def change(self) -> FileChange:
        path = "/".join(self.parts)
        old_sha = self.current.sha256 if self.current else None
        if self.new is None:
            return FileChange(path, "deleted", old_sha, None, 0)
        kind: Literal["created", "modified"] = "modified" if self.current else "created"
        return FileChange(path, kind, old_sha, hashlib.sha256(self.new).hexdigest(), len(self.new))


def _snapshot(parent: int, name: str) -> tuple[Current, bytes] | None:
    current = _current(parent, name)
    if current is None or current.size > WRITE_LIMIT:
        return (current, b"") if current else None
    fd = _open_file(parent, name)
    try:
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return current, handle.read()
    finally:
        os.close(fd)


def _plan(
    root_fd: int,
    real: str,
    patch: FilePatch,
    access: Access,
    policy: Policy,
    fds: contextlib.ExitStack,
) -> _PatchPlan | dict[str, object]:
    """What one file patch will write, or its conflict detail ``{path, hunk, reason}``."""
    parts = resolve(real, patch.path, access, policy)
    if not parts:
        raise PathViolation("not_regular")

    def conflict(reason: str, hunk: int | None = None) -> dict[str, object]:
        return {"path": "/".join(parts), "hunk": hunk, "reason": reason}

    try:
        parent: int | None = open_parent(root_fd, parts, create=False)
        fds.callback(os.close, parent)
    except WorkspaceNotFound:
        parent = None
    snapshot = _snapshot(parent, parts[-1]) if parent is not None else None
    if patch.old_path is None:
        if snapshot is not None:
            return conflict("exists")
        current, old_text = None, None
    elif snapshot is None:
        return conflict("missing")
    else:
        current, raw = snapshot
        if current.size > WRITE_LIMIT:
            return conflict("too_large")
        if b"\x00" in raw[:_SNIFF_BYTES]:
            return conflict("binary")
        try:
            old_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return conflict("binary")
    try:
        new_text = apply_hunks(old_text, patch.hunks)
    except HunkConflict as exc:
        return conflict(exc.reason, exc.hunk_index)
    if patch.new_path is None:
        if new_text:
            return conflict("not_empty_after_delete")
        new = None
    else:
        new = new_text.encode("utf-8")
        if len(new) > WRITE_LIMIT:
            return conflict("too_large")
    return _PatchPlan(parts, parent, current, snapshot[1] if snapshot else b"", new)


def _commit(root_fd: int, plans: list[_PatchPlan], fds: contextlib.ExitStack) -> None:
    """Write every planned file; on failure put back what was already written, then re-raise."""
    done: list[_PatchPlan] = []
    try:
        for plan in plans:
            if plan.parent is None:
                plan.parent = open_parent(root_fd, plan.parts, create=True)
                fds.callback(os.close, plan.parent)
            if plan.new is None:
                os.unlink(plan.parts[-1], dir_fd=plan.parent)
            else:
                _replace(plan.parent, plan.parts[-1], plan.new, plan.current.mode if plan.current else 0o644)
            done.append(plan)
    except BaseException:
        for plan in reversed(done):
            assert plan.parent is not None
            try:
                if plan.current is None:
                    os.unlink(plan.parts[-1], dir_fd=plan.parent)
                else:
                    _replace(plan.parent, plan.parts[-1], plan.old, plan.current.mode)
            except OSError as exc:
                logger.error(
                    "patch_rollback_failed",
                    path="/".join(plan.parts),
                    error_type=type(exc).__name__,
                )
        raise


def _open_dir_at(root_fd: int, parts: tuple[str, ...]) -> int:
    if not parts:
        return os.dup(root_fd)
    parent = open_parent(root_fd, parts, create=False)
    try:
        return os.open(parts[-1], _DIR_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        raise WorkspaceNotFound() from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            st = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            raise PathViolation("symlink" if stat.S_ISLNK(st.st_mode) else "not_regular") from None
        raise
    finally:
        os.close(parent)


def _list_dir(
    root_fd: int, real: str, parts: tuple[str, ...], access: Access, policy: Policy
) -> list[tuple[TreeEntry, tuple[str, ...] | None]]:
    """One directory's visible entries, folders first. A child to descend into carries its parts."""
    fd = _open_dir_at(root_fd, parts)
    try:
        with os.scandir(fd) as scan:
            raw = sorted(scan, key=lambda e: e.name)
        listed: list[tuple[TreeEntry, tuple[str, ...] | None]] = []
        for item in raw:
            child = (*parts, item.name)
            if item.name.startswith(TEMP_PREFIX) or policy.denied(child, access):
                continue
            path = "/".join(child)
            st = os.stat(item.name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                listed.append((TreeEntry(path, item.name, "dir", None, None), child))
            elif stat.S_ISREG(st.st_mode):
                listed.append((TreeEntry(path, item.name, "file", st.st_size, None), None))
            elif stat.S_ISLNK(st.st_mode) and (target := _link_target(real, child, access, policy)):
                kind, size = target
                listed.append((TreeEntry(path, item.name, kind, size, None), None))  # never descended
    finally:
        os.close(fd)
    return sorted(listed, key=lambda pair: pair[0].type != "dir")  # stable: names stay sorted


def _link_target(
    real: str, child: tuple[str, ...], access: Access, policy: Policy
) -> tuple[Literal["file", "dir"], int | None] | None:
    """What an in-root symlink points at; ``None`` hides escaping, dangling, or denied links."""
    try:
        resolved = resolve(real, "/".join(child), access, policy)
    except PathViolation:
        return None
    target = os.path.join(real, *resolved)
    try:
        st = os.stat(target, follow_symlinks=False)  # resolved: no symlinks left in it
    except OSError:
        return None
    if stat.S_ISDIR(st.st_mode):
        return "dir", None
    if stat.S_ISREG(st.st_mode):
        return "file", st.st_size
    return None


def _read_limit(access: Access) -> int:
    return AGENT_READ_LIMIT if access in (Access.AGENT_READ, Access.AGENT_WRITE) else USER_READ_LIMIT
