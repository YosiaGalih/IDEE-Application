"""Workspace file operations (ARCHITECTURE §4.2 ``app/workspace/fs.py``, §7; FR-028, FR-029, FR-032; T2, T9).

``Workspace(root)`` serves both trees of ARCHITECTURE §7: the user's ``repo/`` and a task worktree.
``TaskWorkspace`` is the agent-bound view of a task worktree that tools use (``ToolContext.workspace``).

**Access**
- Every path goes through ``paths.resolve`` (string checks, containment, denylist per ``Access``).
- Files are opened one component at a time without following symlinks.

**Writes**
- Atomic: temp file in the same folder, ``fsync``, then ``os.replace``.
- Version-checked with ``expected_sha256`` (FR-029).
- Serialised by an ``flock`` on a lock file **outside** the root, in ``<root>/../.kk-locks/``. Sandboxed code
  can reach the root but cannot hold the lock.
- The lock is waited for at most ``LOCK_WAIT_S``, then ``WorkspaceBusy``.

**Hostile trees.** A worktree is written by sandboxed code, so every read is bounded:
- FIFOs and sockets are refused without blocking;
- files over ``HASH_LIMIT`` are not hashed;
- ``.agentignore`` is capped;
- entries that vanish mid-listing are skipped.

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
import re
import secrets
import stat
import time
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
    PatchRejected,
    PatchRollbackFailed,
    PathViolation,
    VersionConflict,
    WorkspaceBusy,
    WorkspaceNotFound,
)
from app.workspace.patches import FilePatch, HunkConflict, apply_hunks, parse_unified_diff
from app.workspace.paths import open_parent, open_root, resolve
from app.workspace.policy import AGENTIGNORE, Access, AgentignoreInvalid, Budget, Policy, PolicyBudgetExceeded

logger = get_logger(__name__)

USER_READ_LIMIT = 1024 * 1024  # [Menebak] what the IDE opens as text
AGENT_READ_LIMIT = 256 * 1024  # ARCHITECTURE §7: larger files never reach the LLM
WRITE_LIMIT = 5 * 1024 * 1024  # [Menebak] per file written through the workspace API
HASH_LIMIT = 64 * 1024 * 1024  # [Menebak] larger (or sparse) files are reported without a hash
AGENTIGNORE_LIMIT = 64 * 1024
LOCK_WAIT_S = 10.0  # [Menebak]
LOCKS_DIRNAME = ".kk-locks"
MAX_TREE_DEPTH = 10
TREE_POLICY_BUDGET = 2_000_000  # [Menebak] .agentignore matching cells per tree() call; then truncated
MAX_WALK_DEPTH = 256  # folders open at once in disk_usage; API paths stop at 128 segments
MAX_TREE_ENTRIES = 5000
TEMP_PREFIX = ".kk-tmp-"
_SNIFF_BYTES = 8192
_CHUNK = 64 * 1024
# O_NONBLOCK: opening a FIFO planted in a worktree must fail fast (fstat → not regular), never block.
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_NOT_A_FILE = (errno.ENXIO, errno.ENODEV, errno.EOPNOTSUPP)  # sockets, devices without a driver, ...
_READS = (Access.USER_READ, Access.AGENT_READ)
_WRITES = (Access.USER_WRITE, Access.AGENT_WRITE)
_LINES = re.compile(r"[^\n]*\n|[^\n]+")  # "\n" only: editor, diff and agent line numbers must agree


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
    sha256: str | None  # None only above HASH_LIMIT
    too_large: bool


@dataclass(frozen=True)
class FileChange:
    """API_CONTRACTS §3 ``FileChange`` (``path, change, sha256``) plus the replaced version and size."""

    path: str
    change: Literal["added", "modified", "deleted"]
    sha256: str | None  # the new content; None after a delete
    old_sha256: str | None
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

    sha256: str | None  # None when larger than WRITE_LIMIT: it cannot be version-checked
    mode: int
    size: int


def _bad_request(message: str) -> AppError:
    return AppError("BAD_REQUEST", message)


def _require(access: Access, allowed: tuple[Access, ...]) -> Access:
    access = Access(access)
    if access not in allowed:
        raise ValueError(f"{access} is not valid for this operation")  # a caller bug, never client input
    return access


def _open_file(parent_fd: int, name: str) -> int:
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        raise WorkspaceNotFound() from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PathViolation("symlink") from None
        if exc.errno in _NOT_A_FILE:
            raise PathViolation("not_regular") from None
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PathViolation("not_regular")
    return fd


def _read_fd(fd: int, keep: int, hash_limit: int) -> tuple[str | None, int, bytes]:
    """sha256 of the whole file (``None`` above ``hash_limit``), its size, and its first ``keep`` bytes."""
    size = os.fstat(fd).st_size
    if size > hash_limit:
        return None, size, os.read(fd, keep)
    digest = hashlib.sha256()
    total = 0
    head = bytearray()
    while chunk := os.read(fd, _CHUNK):
        digest.update(chunk)
        total += len(chunk)
        if len(head) < keep:
            head += chunk[: keep - len(head)]
        if total > hash_limit:  # grew while being read
            return None, total, bytes(head)
    return digest.hexdigest(), total, bytes(head)


def _snapshot(parent_fd: int, name: str) -> tuple[Current, bytes] | None:
    """The file at ``name`` and its bytes, from one bounded read (bytes only up to WRITE_LIMIT)."""
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
        digest, size, data = _read_fd(fd, WRITE_LIMIT + 1, WRITE_LIMIT)
    finally:
        os.close(fd)
    mode = stat.S_IMODE(st.st_mode) & 0o777
    if digest is None:
        return Current(None, mode, size), b""
    return Current(digest, mode, size), data


def _check_version(path: str, current: Current | None, expected: str | AnyVersion | None) -> None:
    if isinstance(expected, AnyVersion):
        return
    if isinstance(expected, str) and current is not None and current.sha256 is None:
        raise FileTooLarge(WRITE_LIMIT)
    current_sha = current.sha256 if current else None
    if current is not None and expected is None:
        raise VersionConflict(path, current_sha)
    if expected != current_sha:
        raise VersionConflict(path, current_sha)


def _stage(parent_fd: int, data: bytes, mode: int) -> str:
    """A temp file next to the target holding ``data``, flushed to disk; returns its name."""
    temp = TEMP_PREFIX + secrets.token_hex(8)
    fd = os.open(temp, _TEMP_FLAGS, 0o600, dir_fd=parent_fd)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=parent_fd)
        raise
    return temp


def _fsync_dir(parent_fd: int) -> None:
    try:
        os.fsync(parent_fd)
    except OSError as exc:  # the rename is done; only its durability is unknown
        logger.error("workspace_dir_fsync_failed", error_type=type(exc).__name__)


def _replace(parent_fd: int, name: str, data: bytes, mode: int) -> None:
    temp = _stage(parent_fd, data, mode)
    try:
        os.replace(temp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=parent_fd)
        raise
    _fsync_dir(parent_fd)


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


def _encode(content: str) -> bytes:
    try:
        return content.encode("utf-8")
    except UnicodeEncodeError:  # lone surrogates cannot be stored as UTF-8
        raise _bad_request("Content is not valid text") from None


def _read_limit(access: Access) -> int:
    return AGENT_READ_LIMIT if access is Access.AGENT_READ else USER_READ_LIMIT


class Workspace:
    def __init__(self, root: Path, *, lock_dir: Path | None = None) -> None:
        self.root = root
        # Outside the root: sandboxed code that can write the root must not be able to hold the lock.
        self.lock_dir = lock_dir if lock_dir is not None else root.parent / LOCKS_DIRNAME

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
        access = _require(access, _READS)
        return await asyncio.to_thread(self._read_file, rel, access, max_bytes, start_line, end_line)

    async def write_file(
        self, rel: str, content: str, access: Access, *, expected_sha256: str | AnyVersion | None
    ) -> FileChange:
        access = _require(access, _WRITES)
        return await asyncio.to_thread(self._write_file, rel, content, access, expected_sha256)

    async def delete_file(self, rel: str, access: Access, *, expected_sha256: str | AnyVersion) -> FileChange:
        access = _require(access, _WRITES)
        return await asyncio.to_thread(self._delete_file, rel, access, expected_sha256)

    async def make_dir(self, rel: str, access: Access) -> str:
        access = _require(access, _WRITES)
        return await asyncio.to_thread(self._make_dir, rel, access)

    async def tree(self, rel: str, access: Access, *, depth: int = 3, max_entries: int = 500) -> TreeResult:
        access = _require(access, _READS)
        depth = min(max(depth, 1), MAX_TREE_DEPTH)
        max_entries = min(max(max_entries, 1), MAX_TREE_ENTRIES)
        return await asyncio.to_thread(self._tree, rel, access, depth, max_entries)

    async def disk_usage(self) -> int:
        """Bytes in regular files under the root, symlinks not followed (quota input, ADR-005)."""
        return await asyncio.to_thread(self._disk_usage)

    async def apply_patch(self, diff: str, access: Access) -> list[FileChange]:
        """Apply a unified diff all-or-nothing: any conflict → ``PatchConflict`` and no file changes."""
        access = _require(access, _WRITES)
        return await asyncio.to_thread(self._apply_patch, diff, access)

    # --- plumbing ---

    @contextlib.contextmanager
    def _opened(self, *, lock: bool = False) -> Iterator[tuple[int, str]]:
        fd, real = open_root(self.root)
        try:
            if lock:
                with self._locked(real):
                    yield fd, real
            else:
                yield fd, real
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def _locked(self, real: str) -> Iterator[None]:
        os.makedirs(self.lock_dir, mode=0o700, exist_ok=True)
        name = hashlib.sha256(os.fsencode(real)).hexdigest()[:32] + ".lock"
        dir_fd = os.open(self.lock_dir, _DIR_FLAGS)
        try:
            lock_fd = os.open(
                name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dir_fd
            )
        finally:
            os.close(dir_fd)
        try:
            deadline = time.monotonic() + LOCK_WAIT_S
            delay = 0.005
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise WorkspaceBusy() from None
                    time.sleep(delay)
                    delay = min(delay * 2, 0.1)
            yield
        finally:
            os.close(lock_fd)  # releases the lock

    def _policy(self, root_fd: int, access: Access) -> Policy:
        """The project policy; an unreadable or over-cap ``.agentignore`` denies agent access."""
        try:
            fd = _open_file(root_fd, AGENTIGNORE)
        except WorkspaceNotFound:
            return Policy()
        except PathViolation:
            return _invalid_policy(access)
        try:
            if os.fstat(fd).st_size > AGENTIGNORE_LIMIT:
                return _invalid_policy(access)
            data = os.read(fd, AGENTIGNORE_LIMIT + 1)
        finally:
            os.close(fd)
        if len(data) > AGENTIGNORE_LIMIT:
            return _invalid_policy(access)
        try:
            return Policy.from_agentignore(data.decode("utf-8", errors="replace"))
        except AgentignoreInvalid:
            return _invalid_policy(access)

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
        ceiling = _read_limit(access)  # a caller can lower the limit, never raise it
        limit = ceiling if max_bytes is None else min(max(max_bytes, 0), ceiling)
        with self._opened() as (root_fd, real):
            parts = self._target(root_fd, real, rel, access)
            parent = open_parent(root_fd, parts, create=False)
            try:
                fd = _open_file(parent, parts[-1])
            finally:
                os.close(parent)
            try:
                digest, size, head = _read_fd(fd, limit + 1, HASH_LIMIT)
            finally:
                os.close(fd)
        content, encoding = _decode(head, size, limit)
        if content is not None and (start_line is not None or end_line is not None):
            lines = _LINES.findall(content)
            content = "".join(lines[(start_line or 1) - 1 : end_line])
        return FileContent("/".join(parts), content, encoding, size, digest, size > limit)

    def _write_file(
        self, rel: str, content: str, access: Access, expected: str | AnyVersion | None
    ) -> FileChange:
        data = _encode(content)
        if len(data) > WRITE_LIMIT:
            raise FileTooLarge(WRITE_LIMIT)
        with self._opened(lock=True) as (root_fd, real):
            parts = self._target(root_fd, real, rel, access)
            path = "/".join(parts)
            try:
                parent = open_parent(root_fd, parts, create=False)
            except WorkspaceNotFound:
                _check_version(path, None, expected)  # a refused write must not leave folders behind
                parent = open_parent(root_fd, parts, create=True)
            try:
                snapshot = _snapshot(parent, parts[-1])
                current = snapshot[0] if snapshot else None
                _check_version(path, current, expected)
                _replace(parent, parts[-1], data, current.mode if current else 0o644)
            finally:
                os.close(parent)
        return FileChange(
            path,
            "modified" if current else "added",
            hashlib.sha256(data).hexdigest(),
            current.sha256 if current else None,
            len(data),
        )

    def _delete_file(self, rel: str, access: Access, expected: str | AnyVersion) -> FileChange:
        with self._opened(lock=True) as (root_fd, real):
            parts = self._target(root_fd, real, rel, access)
            path = "/".join(parts)
            parent = open_parent(root_fd, parts, create=False)
            try:
                snapshot = _snapshot(parent, parts[-1])
                if snapshot is None:
                    raise WorkspaceNotFound()
                current = snapshot[0]
                _check_version(path, current, expected)
                os.unlink(parts[-1], dir_fd=parent)
                _fsync_dir(parent)
            finally:
                os.close(parent)
        return FileChange(path, "deleted", None, current.sha256, 0)

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
            seen: set[tuple[str, ...]] = set()
            for patch in patches:
                parts = resolve(real, patch.path, access, policy)
                if not parts:
                    raise PathViolation("not_regular")
                if parts in seen:  # "a.py" and "./a.py" are one file: two plans would overwrite each other
                    raise PatchRejected("duplicate_path")
                seen.add(parts)
                plan = _plan(root_fd, parts, patch, fds)
                if isinstance(plan, dict):
                    conflicts.append(plan)
                else:
                    plans.append(plan)
            if conflicts:
                raise PatchConflict(conflicts)  # nothing has been written yet
            _commit(root_fd, plans, fds)
        return [plan.change for plan in plans]

    def _tree(self, rel: str, access: Access, depth: int, max_entries: int) -> TreeResult:
        """Breadth-first, so a truncated listing still shows every entry of the upper levels.

        Policy matching for the whole listing shares one budget; when it runs out the listing stops and is
        reported truncated (a hostile ``.agentignore`` cannot turn one call into minutes of work).
        """
        with self._opened() as (root_fd, real):
            policy = self._policy(root_fd, access)
            budget = Budget(TREE_POLICY_BUDGET)
            start = resolve(real, rel, access, policy)
            top: list[TreeEntry] = []
            queue: deque[tuple[tuple[str, ...], list[TreeEntry], int]] = deque([(start, top, 1)])
            count = 0
            first = True
            while queue:
                parts, into, level = queue.popleft()
                try:
                    listed, exhausted = _list_dir(root_fd, real, parts, access, policy, budget)
                except (WorkspaceNotFound, PathViolation):
                    if first:
                        raise
                    continue  # a sub-folder that vanished or was swapped for a link mid-listing
                first = False
                for entry, child_parts in listed:
                    if count == max_entries:
                        return TreeResult(top, truncated=True)
                    if entry.type == "dir" and child_parts is not None and level < depth and not exhausted:
                        children: list[TreeEntry] = []
                        entry = TreeEntry(entry.path, entry.name, "dir", None, children)
                        queue.append((child_parts, children, level + 1))
                    into.append(entry)
                    count += 1
                if exhausted:
                    return TreeResult(top, truncated=True)  # keep what was already checked
        return TreeResult(top, truncated=False)

    def _disk_usage(self) -> int:
        """Iterative, fd-based walk; at most ``MAX_WALK_DEPTH`` folders open at once."""
        total = 0
        with self._opened() as (root_fd, _):
            stack: list[tuple[int, Iterator[str]]] = []
            try:
                fd = os.dup(root_fd)
                stack.append((fd, iter(_names(fd))))
                while stack:
                    fd, names = stack[-1]
                    name = next(names, None)
                    if name is None:
                        os.close(fd)
                        stack.pop()
                        continue
                    try:
                        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISREG(st.st_mode):
                        total += st.st_size
                    elif stat.S_ISDIR(st.st_mode):
                        if len(stack) >= MAX_WALK_DEPTH:
                            raise PathViolation("too_deep")  # deeper than any path the API can address
                        try:
                            child = os.open(name, _DIR_FLAGS, dir_fd=fd)
                        except OSError:
                            continue  # swapped for a link or removed mid-walk: never followed
                        stack.append((child, iter(_names(child))))
            finally:
                for fd, _ in stack:
                    os.close(fd)
        return total


def _invalid_policy(access: Access) -> Policy:
    if access in (Access.AGENT_READ, Access.AGENT_WRITE):
        raise PathViolation("agentignore")
    return Policy()  # the user can still read, and repair the file


def _names(dir_fd: int) -> list[str]:
    try:
        with os.scandir(dir_fd) as scan:
            return [entry.name for entry in scan]
    except OSError as exc:
        logger.warning("workspace_walk_error", error_type=type(exc).__name__)
        return []


class TaskWorkspace:
    """The agent's view of a task worktree (ARCHITECTURE §4.2): every call is AGENT_READ or AGENT_WRITE."""

    def __init__(self, root: Path, *, lock_dir: Path | None = None) -> None:
        self._workspace = Workspace(root, lock_dir=lock_dir)

    async def read_text(
        self,
        rel: str,
        start_line: int | None = None,
        end_line: int | None = None,
        max_bytes: int | None = None,
    ) -> FileContent:
        return await self._workspace.read_file(
            rel, Access.AGENT_READ, max_bytes=max_bytes, start_line=start_line, end_line=end_line
        )

    async def write_text(self, rel: str, content: str) -> FileChange:
        return await self._workspace.write_file(rel, content, Access.AGENT_WRITE, expected_sha256=ANY_VERSION)

    async def apply_unified_diff(self, diff: str) -> list[FileChange]:
        return await self._workspace.apply_patch(diff, Access.AGENT_WRITE)

    async def delete(self, rel: str) -> FileChange:
        return await self._workspace.delete_file(rel, Access.AGENT_WRITE, expected_sha256=ANY_VERSION)

    async def tree(self, rel: str = ".", depth: int = 3) -> TreeResult:
        return await self._workspace.tree(rel, Access.AGENT_READ, depth=depth)


@dataclass
class _PatchPlan:
    parts: tuple[str, ...]
    parent: int | None  # None: the parent folder does not exist yet (an added file)
    current: Current | None
    old: bytes
    new: bytes | None  # None: delete
    mode: int

    @property
    def change(self) -> FileChange:
        path = "/".join(self.parts)
        old_sha = self.current.sha256 if self.current else None
        if self.new is None:
            return FileChange(path, "deleted", None, old_sha, 0)
        kind: Literal["added", "modified"] = "modified" if self.current else "added"
        return FileChange(path, kind, hashlib.sha256(self.new).hexdigest(), old_sha, len(self.new))


def _plan(
    root_fd: int, parts: tuple[str, ...], patch: FilePatch, fds: contextlib.ExitStack
) -> _PatchPlan | dict[str, object]:
    """What one file patch will write, or its conflict detail ``{path, hunk, reason}``."""

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
        current, old_text, raw = None, None, b""
    elif snapshot is None:
        return conflict("missing")
    else:
        current, raw = snapshot
        if current.sha256 is None:
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
        try:
            new = new_text.encode("utf-8")
        except UnicodeEncodeError:
            raise PatchRejected("malformed") from None
        if len(new) > WRITE_LIMIT:
            return conflict("too_large")
    mode = current.mode if current else (patch.new_mode or 0o644)
    return _PatchPlan(parts, parent, current, raw, new, mode)


def _commit(root_fd: int, plans: list[_PatchPlan], fds: contextlib.ExitStack) -> None:
    """Stage every file, then swap them in. On failure put back what was swapped, then re-raise."""
    staged: list[tuple[_PatchPlan, str | None]] = []
    try:
        for plan in plans:
            if plan.parent is None:
                plan.parent = open_parent(root_fd, plan.parts, create=True)
                fds.callback(os.close, plan.parent)
            staged.append((plan, None if plan.new is None else _stage(plan.parent, plan.new, plan.mode)))
    except BaseException:
        _discard(staged)
        raise
    done: list[_PatchPlan] = []
    try:
        for plan, temp in staged:
            assert plan.parent is not None
            if temp is None:
                os.unlink(plan.parts[-1], dir_fd=plan.parent)
            else:
                os.replace(temp, plan.parts[-1], src_dir_fd=plan.parent, dst_dir_fd=plan.parent)
            done.append(plan)
    except BaseException as exc:
        _discard(staged[len(done) :])
        failed = _rollback(done)
        if failed:
            raise PatchRollbackFailed(failed) from exc
        raise
    for parent in {plan.parent for plan in plans if plan.parent is not None}:
        _fsync_dir(parent)


def _discard(staged: list[tuple[_PatchPlan, str | None]]) -> None:
    for plan, temp in staged:
        if temp is not None and plan.parent is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp, dir_fd=plan.parent)


def _rollback(done: list[_PatchPlan]) -> list[str]:
    """Undo swapped files, newest first; returns the paths that could not be restored."""
    failed: list[str] = []
    for plan in reversed(done):
        assert plan.parent is not None
        try:
            if plan.current is None:
                os.unlink(plan.parts[-1], dir_fd=plan.parent)
            else:
                _replace(plan.parent, plan.parts[-1], plan.old, plan.current.mode)
        except OSError as exc:
            path = "/".join(plan.parts)
            logger.error("patch_rollback_failed", path=path, error_type=type(exc).__name__)
            failed.append(path)
    return failed


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
            try:
                st = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                raise WorkspaceNotFound() from None
            raise PathViolation("symlink" if stat.S_ISLNK(st.st_mode) else "not_regular") from None
        raise
    finally:
        os.close(parent)


def _list_dir(
    root_fd: int, real: str, parts: tuple[str, ...], access: Access, policy: Policy, budget: Budget
) -> tuple[list[tuple[TreeEntry, tuple[str, ...] | None]], bool]:
    """One directory's visible entries, folders first, and whether the policy budget ran out on the way.

    A child to descend into carries its parts. When the budget runs out, the entries checked so far are
    still returned.
    """
    fd = _open_dir_at(root_fd, parts)
    exhausted = False
    try:
        with os.scandir(fd) as scan:
            names = sorted(entry.name for entry in scan)
        listed: list[tuple[TreeEntry, tuple[str, ...] | None]] = []
        for name in names:
            child = (*parts, name)
            try:
                hidden = name.startswith(TEMP_PREFIX) or policy.denied(child, access, budget)
            except PolicyBudgetExceeded:
                exhausted = True
                break
            if hidden:
                continue
            path = "/".join(child)
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue  # removed while listing
            if stat.S_ISDIR(st.st_mode):
                listed.append((TreeEntry(path, name, "dir", None, None), child))
            elif stat.S_ISREG(st.st_mode):
                listed.append((TreeEntry(path, name, "file", st.st_size, None), None))
            elif stat.S_ISLNK(st.st_mode):
                try:
                    target = _link_target(root_fd, real, child, access, policy, budget)
                except PolicyBudgetExceeded:
                    exhausted = True
                    break
                if target:
                    kind, size = target
                    listed.append((TreeEntry(path, name, kind, size, None), None))  # never descended
    finally:
        os.close(fd)
    return sorted(listed, key=lambda pair: pair[0].type != "dir"), exhausted  # stable: names stay sorted


def _link_target(
    root_fd: int, real: str, child: tuple[str, ...], access: Access, policy: Policy, budget: Budget
) -> tuple[Literal["file", "dir"], int | None] | None:
    """What an in-root symlink points at; ``None`` hides escaping, dangling, or denied links."""
    try:
        resolved = resolve(real, "/".join(child), access, policy, budget)
    except PathViolation:
        return None
    if not resolved:
        return "dir", None  # a link to the root itself
    try:
        parent = open_parent(root_fd, resolved, create=False)  # no symlink is followed on the way
    except (WorkspaceNotFound, PathViolation):
        return None
    try:
        st = os.stat(resolved[-1], dir_fd=parent, follow_symlinks=False)
    except OSError:
        return None
    finally:
        os.close(parent)
    if stat.S_ISDIR(st.st_mode):
        return "dir", None
    if stat.S_ISREG(st.st_mode):
        return "file", st.st_size
    return None
