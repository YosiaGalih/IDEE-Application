"""What every builtin shares: the task worktree from the context, path checks, and workspace failures turned
into ``ToolError`` codes the model can act on (never a path outside the worktree, never file content)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import PurePosixPath

from app.gitops.adapter import GitTarget
from app.gitops.errors import GitError, GitInputError
from app.tools.base import ToolContext, ToolError
from app.workspace.errors import FileTooLarge, PathViolation, WorkspaceBusy, WorkspaceNotFound
from app.workspace.fs import TaskWorkspace

PATH_MAX_CHARS = 1000


def workspace_of(ctx: ToolContext) -> TaskWorkspace:
    if ctx.workspace is None:
        raise ToolError("no_workspace", "this task has no worktree yet")
    return ctx.workspace


def git_target(workspace: TaskWorkspace) -> GitTarget:
    return GitTarget(workspace.repo, worktree=workspace.root)


def relative(path: str) -> str:
    """A worktree-relative POSIX path, or ``ToolError`` (the workspace resolves it again, safely)."""
    if not path or len(path) > PATH_MAX_CHARS or "\0" in path or "\\" in path:
        raise ToolError("invalid_path", "the path must be a relative path inside the worktree")
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ToolError("invalid_path", "the path must be a relative path inside the worktree")
    normal = str(pure)
    return "." if normal in ("", ".") else normal


@contextlib.contextmanager
def workspace_errors() -> Iterator[None]:
    try:
        yield
    except PathViolation:
        raise ToolError("path_denied", "agents may not read or write this path") from None
    except WorkspaceNotFound:
        raise ToolError("not_found", "no such file or folder in the worktree") from None
    except FileTooLarge:
        raise ToolError("file_too_large", "the file is too large for agents") from None
    except WorkspaceBusy:
        raise ToolError("busy", "the worktree is busy; retry") from None
    except GitInputError as exc:
        raise ToolError("invalid_input", f"git refused the input ({exc.reason})") from None
    except GitError as exc:
        raise ToolError("git_failed", f"git failed ({exc.reason})") from None
