"""What every builtin shares: the task worktree from the context, path checks, and workspace failures turned
into ``ToolError`` codes the model can act on (never a path outside the worktree, never file content)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict

from app.gitops.adapter import GitTarget
from app.gitops.errors import GitError, GitInputError
from app.tools.base import ToolContext, ToolError
from app.workspace.errors import FileTooLarge, PathViolation, WorkspaceBusy, WorkspaceNotFound
from app.workspace.fs import TaskWorkspace
from app.workspace.policy import SANDBOX_OUTPUT_DIR, Access, Budget, Policy, PolicyBudgetExceeded

PATH_MAX_CHARS = 1000


class StrictInput(BaseModel):
    """Every builtin's input: unknown fields are an invalid input, never ignored."""

    model_config = ConfigDict(extra="forbid")


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


SANDBOX_DIR = (
    SANDBOX_OUTPUT_DIR  # sandbox output (SECURITY_MODEL §4): never listed for the model, never in git
)
POLICY_BUDGET = 2_000_000  # [Menebak] .agentignore matching cells for one whole tool call (WP-2.1d hand-off)
MAX_NAMES = 20_000  # [Menebak] names one tool call checks; the rest is reported as not checked


def within(path: str, scopes: list[str]) -> bool:
    """``scopes`` from ``relative()``: "." is everything, otherwise the path itself or what is below it."""
    return any(scope == "." or path == scope or path.startswith(f"{scope}/") for scope in scopes)


@dataclass(frozen=True)
class Partition:
    shown: list[str]
    hidden: int  # refused by the test
    unchecked: int  # past the name cap or the policy budget: not shown (fail closed)


def partition(
    names: list[str],
    refused: Callable[[tuple[str, ...], Budget], bool],
    *,
    budget_units: int | None = None,
    cap: int | None = None,
) -> Partition:
    """Split ``names`` with one ``Budget`` for the whole call (AG-13 WP-3.1d2a HIGH-2). CPU work over many
    names: callers run it in a thread. ``.kk/`` is hidden like a denied name."""
    budget = Budget(POLICY_BUDGET if budget_units is None else budget_units)
    cap = MAX_NAMES if cap is None else cap
    shown: list[str] = []
    hidden = 0
    for index, name in enumerate(names):
        if index >= cap:
            return Partition(shown, hidden, len(names) - index)
        parts = PurePosixPath(name).parts
        if not parts:
            continue
        if parts[0] == SANDBOX_DIR:
            hidden += 1  # sandbox output: never shown to the model, counted (spec 3.1d2a Goal 8)
            continue
        try:
            if refused(parts, budget):
                hidden += 1
                continue
        except PolicyBudgetExceeded:
            return Partition(shown, hidden, len(names) - index)
        shown.append(name)
    return Partition(shown, hidden, 0)


def agent_cannot_read(policy: Policy) -> Callable[[tuple[str, ...], Budget], bool]:
    return lambda parts, budget: policy.denied(parts, Access.AGENT_READ, budget)


def counted_notes(hidden: int, unchecked: int, skipped: int, what: str) -> list[str]:
    notes = []
    if hidden:
        notes.append(f"[{hidden} {what} not shown: agents may not read them]")
    if unchecked:
        notes.append(
            f"[{unchecked} {what} not checked against the agent policy (too many or too costly): not shown]"
        )
    if skipped:
        notes.append(f"[{skipped} {what} with names that are not UTF-8: not shown]")
    return notes
