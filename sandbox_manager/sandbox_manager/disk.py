"""Measuring and removing trees a sandbox could write to (stdlib only).

A sandbox runs as uid 10001, like the manager in compose. It can make folders the manager cannot list
(``chmod 000``), very deep trees, and millions of empty files. So:
- a measurement never reads such a tree as smaller than it is;
- a removal never recurses (``shutil.rmtree`` does, and hits ``RecursionError`` near depth 1000 on 3.12);
- a removal opens locked folders first, and reports what it could not remove.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import stat
from pathlib import Path

log = logging.getLogger(__name__)
ENTRY_COST = 512  # [Menebak] every file or folder counts at least this much: empty files still use inodes


class Unmeasurable(Exception):
    """Part of the tree could not be read, so its size is unknown."""


def _cost(st: os.stat_result) -> int:
    return max(st.st_size if stat.S_ISREG(st.st_mode) else 0, st.st_blocks * 512, ENTRY_COST)


def tree_bytes(root: Path) -> int:
    """Disk taken by everything under ``root`` (links not followed); ``Unmeasurable`` on any gap.

    ``os.walk`` is iterative on Python 3.12, so depth is not a problem.
    """

    def refuse(error: OSError) -> None:
        raise Unmeasurable(type(error).__name__) from error

    total = 0
    for folder, dirs, files in os.walk(root, onerror=refuse, followlinks=False):
        for name in (*dirs, *files):
            try:
                total += _cost(os.lstat(os.path.join(folder, name)))
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise Unmeasurable(type(exc).__name__) from exc
    return total


def remove_tree(path: Path) -> bool:
    """Remove ``path`` however deep it is, opening up folders the sandbox locked. False (logged) if any stays.

    Works with folder descriptors and short names only, never with long paths: a sandbox can nest folders
    past PATH_MAX (4096 bytes) by ``chdir``, which ``os.walk`` and ``shutil.rmtree`` cannot remove. Each
    pass lifts the contents of a non-empty sub-folder into the top folder (``rename`` between two
    descriptors), so at most two descriptors are open and no path ever grows.

    Call it only once nothing else writes into ``path`` (the container is gone).
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return True
    if not stat.S_ISDIR(st.st_mode):
        return _unlink(str(path))
    with contextlib.suppress(OSError):  # not ours: the removal below reports what stays
        os.chmod(path, 0o700)
    try:
        top = os.open(path, _DIR_FLAGS)
    except OSError as exc:
        log.error("could not open %s for removal: %s", path, type(exc).__name__)
        return False
    try:
        left = _empty(top)
    finally:
        os.close(top)
    if left or not _rmdir(str(path)):
        log.error("could not remove %s (%d entries left)", path, left)
        return False
    return True


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_MAX_PASSES = 1_000_000  # [Menebak] a bound on the lifting passes; far above any real tree


def _empty(top: int) -> int:
    """Empty the folder open as ``top``; returns how many entries could not be removed."""
    prefix = f".kk-rm-{secrets.token_hex(6)}-"  # unguessable: the sandbox cannot pre-create a clash
    lifted = 0
    for _ in range(_MAX_PASSES):
        stuck = 0
        progress = False
        for name in os.listdir(top):
            try:
                st = os.stat(name, dir_fd=top, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(st.st_mode):
                if _unlink_at(name, top):
                    progress = True
                else:
                    stuck += 1
                continue
            try:
                os.rmdir(name, dir_fd=top)
                progress = True
                continue
            except FileNotFoundError:
                continue
            except OSError:
                pass  # not empty (or locked): lift its contents one level up
            with contextlib.suppress(OSError):
                os.chmod(name, 0o700, dir_fd=top)  # a folder (checked above), not a link
            try:
                child = os.open(name, _DIR_FLAGS, dir_fd=top)
            except OSError:
                stuck += 1
                continue
            try:
                for inner in os.listdir(child):
                    lifted += 1
                    with contextlib.suppress(
                        OSError
                    ):  # moving a folder rewrites its "..": it must be writable
                        if stat.S_ISDIR(os.stat(inner, dir_fd=child, follow_symlinks=False).st_mode):
                            os.chmod(inner, 0o700, dir_fd=child)
                    try:
                        os.rename(inner, f"{prefix}{lifted}", src_dir_fd=child, dst_dir_fd=top)
                        progress = True
                    except OSError:
                        stuck += 1
            finally:
                os.close(child)
        if stuck == 0 and not os.listdir(top):
            return 0
        if not progress:
            return max(stuck, len(os.listdir(top)))
    return len(os.listdir(top))


def _unlink_at(name: str, folder: int) -> bool:
    try:
        os.unlink(name, dir_fd=folder)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def _unlink(path: str) -> bool:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def _rmdir(path: str) -> bool:
    try:
        os.rmdir(path)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True
