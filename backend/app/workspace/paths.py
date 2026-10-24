"""Safe path resolution (ARCHITECTURE §7, FR-028, SECURITY_MODEL T2).

Two layers, both required:
1. **String and containment check** (``resolve``).
   - The relative path is validated: no absolute path, ``..``, NUL, ``\\``, or over-long segment.
   - Its ``realpath`` must stay under the root's ``realpath``.
   - The denylist is applied to the requested path *and* to the resolved one.
   - Writes never pass through a symlink: requested must equal resolved.
2. **Component-wise open** (``open_parent``).
   - The resolved path is opened one component at a time from the root fd, with ``O_NOFOLLOW``.
   - A resolved path contains no symlinks. One met here was swapped in after the check (TOCTOU) and is
     refused.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
from pathlib import Path

from app.workspace.errors import PathViolation, WorkspaceNotFound
from app.workspace.policy import Access, Policy

MAX_SEGMENT_BYTES = 255
MAX_PATH_BYTES = 4096
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_WRITES = (Access.USER_WRITE, Access.AGENT_WRITE)
_realpath = os.path.realpath  # module attribute so the race test can stub the check layer


def split_rel(rel: str) -> tuple[str, ...]:
    """Validate a client-supplied relative path and return its segments (``()`` is the root)."""
    if "\x00" in rel:
        raise PathViolation("nul")
    if not rel or "\\" in rel:
        raise PathViolation("invalid")
    if rel.startswith("/"):
        raise PathViolation("absolute")
    if len(os.fsencode(rel)) > MAX_PATH_BYTES:
        raise PathViolation("too_long")
    parts: list[str] = []
    for segment in rel.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise PathViolation("traversal")
        if len(os.fsencode(segment)) > MAX_SEGMENT_BYTES:
            raise PathViolation("too_long")
        parts.append(segment)
    return tuple(parts)


def resolve(root_real: str, rel: str, access: Access, policy: Policy) -> tuple[str, ...]:
    """The segments to open for ``rel`` under the (already real) root, or ``PathViolation``."""
    requested = split_rel(rel)
    if policy.denied(requested, access):
        raise PathViolation("denied")
    real = _realpath(os.path.join(root_real, *requested))
    if os.path.commonpath([root_real, real]) != root_real:
        raise PathViolation("outside_root")
    resolved = Path(real).relative_to(root_real).parts
    if resolved != requested:
        if access in _WRITES:
            raise PathViolation("symlink")
        if policy.denied(resolved, access):
            raise PathViolation("denied")
    return resolved


def open_root(root: Path) -> tuple[int, str]:
    """An fd on the workspace root and the root's realpath."""
    real = os.path.realpath(root)
    try:
        return os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC), real
    except (FileNotFoundError, NotADirectoryError):
        raise WorkspaceNotFound() from None


def _component_error(dir_fd: int, name: str) -> PathViolation:
    try:
        mode = os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
    except OSError:
        return PathViolation("symlink")
    return PathViolation("symlink" if stat.S_ISLNK(mode) else "not_regular")


def _open_dir(dir_fd: int, name: str) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _component_error(dir_fd, name) from None
        raise


def open_parent(root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
    """An fd on the directory holding ``parts[-1]``, walked without following any symlink."""
    fd = os.dup(root_fd)
    try:
        for name in parts[:-1]:
            try:
                child = _open_dir(fd, name)
            except FileNotFoundError:
                if not create:
                    raise WorkspaceNotFound() from None
                with contextlib.suppress(FileExistsError):
                    os.mkdir(name, 0o755, dir_fd=fd)
                child = _open_dir(fd, name)
                os.fchmod(child, 0o755)  # mkdir's mode is masked by the umask
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise
