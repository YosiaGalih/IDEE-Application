"""R0 git builtins over the task worktree (TOOL_CATALOG `git.status`, `git.diff`, `git.log`; spec
3.1d2a Goals 8-10).

Names and contents the agent policy hides (secret names incl. ``*.env``, both ``.agentignore`` files,
dependency folders) and ``.kk/`` never appear. ``git.diff`` lists the changed names first, filters them, and
only then asks git for the content of what is left: a hidden file's content is never diffed (WP-1.2 hand-off),
and the model's ``paths`` can narrow that list but never widen it.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from app.gitops.adapter import GitAdapter
from app.gitops.diffparse import Diff
from app.tools.base import RiskLevel, ToolContext, ToolError, ToolOutput, ToolSpec, Untrusted
from app.tools.builtin._common import git_target, relative, workspace_errors, workspace_of
from app.workspace.fs import TaskWorkspace
from app.workspace.policy import Access, Policy

SANDBOX_DIR = ".kk"
DIFF_MAX_BYTES = 60 * 1024  # below the 64 KB tool_result: the gateway never has to cut a hunk
DIFF_READ_BYTES = 1024 * 1024  # what git may produce before the text is cut at a file boundary
LOG_MAX = 50


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatusInput(_Input):
    pass


class DiffInput(_Input):
    paths: list[str] | None = Field(None, max_length=100)


class LogInput(_Input):
    limit: int = Field(10, ge=1, le=LOG_MAX)


def _visible(path: str, policy: Policy) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and parts[0] != SANDBOX_DIR and not policy.denied(parts, Access.AGENT_READ)


def _hidden_note(hidden: int) -> list[str]:
    return [f"[{hidden} changed paths are not shown: agents may not read them]"] if hidden else []


async def status(ctx: ToolContext, raw: StatusInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    with workspace_errors():
        policy = await workspace.policy()
        got = await GitAdapter().status(git_target(workspace))
    shown = [c for c in got.changes if _visible(c.path, policy)]
    lines = [f"branch: {got.branch or '(detached)'}", f"head: {got.head or '(none)'}"]
    lines += [f"{c.status} {c.path}" for c in shown] or ["clean"]
    lines += _hidden_note(len(got.changes) - len(shown))
    return ToolOutput("\n".join(lines), untrusted=Untrusted("git.status"))


def _within(path: str, scopes: list[str]) -> bool:
    return any(scope == "." or path == scope or path.startswith(f"{scope}/") for scope in scopes)


def _render(diff: Diff) -> list[str]:
    """Unified text, one block per file; the list lets the caller cut at a file boundary."""
    blocks: list[str] = []
    for file in diff.files:
        old = file.old_path or file.path
        head = [f"diff --git a/{old} b/{file.path}"]
        head.append("--- /dev/null" if file.status == "added" else f"--- a/{old}")
        head.append("+++ /dev/null" if file.status == "deleted" else f"+++ b/{file.path}")
        if file.is_binary:
            head.append("Binary file changed")
        body = []
        for hunk in file.hunks:
            body.append(hunk.header)
            sign = {"context": " ", "add": "+", "del": "-"}
            body += [f"{sign[line.type]}{line.text}" for line in hunk.lines]
        blocks.append("\n".join(head + body))
    return blocks


async def _base(workspace: TaskWorkspace) -> str:
    if workspace.base_commit is None:
        raise ToolError("no_base_commit", "this task has no base commit to diff against")
    return workspace.base_commit


async def diff(ctx: ToolContext, raw: DiffInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    base = await _base(workspace)
    scopes = [relative(p) for p in raw.paths] if raw.paths else ["."]
    git, target = GitAdapter(), git_target(workspace)
    with workspace_errors():
        policy = await workspace.policy()
        names = await git.changed_paths(target, base=base)
        allowed = [n for n in names if _visible(n, policy)]
        wanted = [n for n in allowed if _within(n, scopes)]
        got = await git.diff(target, base=base, paths=wanted, max_bytes=DIFF_READ_BYTES) if wanted else None
    hidden = len(names) - len(allowed)
    if got is None:
        return ToolOutput("\n".join(["[no changes]", *_hidden_note(hidden)]), untrusted=Untrusted("git.diff"))
    out: list[str] = []
    used = 0
    blocks = _render(got)
    for index, block in enumerate(blocks):
        size = len(block.encode()) + 1
        if used + size > DIFF_MAX_BYTES:
            out.append(f"[truncated: {len(blocks) - index} more files; pass paths to see them]")
            break
        out.append(block)
        used += size
    if got.truncated:
        out.append("[truncated: the diff is larger than one read; pass paths to see the rest]")
    return ToolOutput("\n".join(out + _hidden_note(hidden)), untrusted=Untrusted("git.diff"))


async def log(ctx: ToolContext, raw: LogInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    with workspace_errors():
        entries = await GitAdapter().log(git_target(workspace), raw.limit)
    lines = [f"{e.commit[:12]}  {e.date}  {e.author}  {e.message}" for e in entries] or ["[no commits]"]
    return ToolOutput("\n".join(lines), untrusted=Untrusted("git.log"))


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "git.status",
            "Branch, head and changed files of the task worktree.",
            StatusInput,
            RiskLevel.R0,
            status,
            timeout_s=30,
        ),
        ToolSpec(
            "git.diff",
            "Unified diff of the task worktree against the task's base commit (optional paths).",
            DiffInput,
            RiskLevel.R0,
            diff,
            timeout_s=30,
        ),
        ToolSpec(
            "git.log",
            "Recent commits of the task branch, newest first.",
            LogInput,
            RiskLevel.R0,
            log,
            timeout_s=30,
        ),
    ]
