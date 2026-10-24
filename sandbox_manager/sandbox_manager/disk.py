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
    """Remove ``path`` without recursion, opening up folders the sandbox locked. False (logged) if any stays.

    Call it only once nothing else writes into ``path`` (the container is gone): it works on path names.
    """
    if not os.path.lexists(path):
        return True
    if not os.path.isdir(path) or os.path.islink(path):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        return not os.path.lexists(path)
    with contextlib.suppress(OSError):  # not ours: the removal below reports what stays
        os.chmod(path, 0o700)
    for folder, dirs, _files in os.walk(path):  # top-down: each folder is opened before it is listed
        for name in dirs:
            child = os.path.join(folder, name)
            if not os.path.islink(child):
                with contextlib.suppress(OSError):
                    os.chmod(child, 0o700)
    failures = 0
    for folder, dirs, files in os.walk(path, topdown=False):
        for name in files:
            failures += not _unlink(os.path.join(folder, name))
        for name in dirs:
            child = os.path.join(folder, name)
            failures += not (_unlink(child) if os.path.islink(child) else _rmdir(child))
    failures += not _rmdir(str(path))
    if failures or os.path.lexists(path):
        log.error("could not remove %s (%d entries left)", path, failures)
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
