"""R0 git builtins over the task worktree (TOOL_CATALOG `git.status`, `git.diff`, `git.log`; spec
3.1d2a Goals 8-10).

Names and contents the agent policy hides (secret names incl. ``*.env``, both ``.agentignore`` files,
dependency folders) and ``.kk/`` never appear; the policy runs with one Budget per call, in a thread (AG-13
WP-3.1d2a HIGH-2). ``git.diff`` lists the changed names without staging anything, filters them, stages only
the exact files left, and keeps only those exact files from git's answer: a literal pathspec ``config`` also
matches ``config/**``, so a folder replaced by a file must not carry what was below it (AG-13 WP-3.1d2a
HIGH-1). The model's ``paths`` narrow the list, never widen it. Every cut, omission and hidden count is said
in the output.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.gitops.adapter import GitAdapter
from app.gitops.diffparse import Diff, DiffFile
from app.tools.base import RiskLevel, ToolContext, ToolError, ToolOutput, ToolSpec, Untrusted
from app.tools.builtin._common import (
    agent_cannot_read,
    counted_notes,
    git_target,
    partition,
    relative,
    within,
    workspace_errors,
    workspace_of,
)
from app.workspace.fs import AGENT_READ_LIMIT

DIFF_MAX_BYTES = 60 * 1024  # below the 64 KB tool_result: the gateway never has to cut the diff
DIFF_READ_BYTES = 1024 * 1024  # what git may produce before the text is cut
LOG_MAX = 50
_SIGN = {"context": " ", "add": "+", "del": "-"}


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatusInput(_Input):
    pass


class DiffInput(_Input):
    paths: list[str] | None = Field(None, max_length=100)


class LogInput(_Input):
    limit: int = Field(10, ge=1, le=LOG_MAX)


async def status(ctx: ToolContext, raw: StatusInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    with workspace_errors():
        policy = await workspace.policy()
        got = await GitAdapter().status(git_target(workspace))
    names = [c.path for c in got.changes]
    kept = await asyncio.to_thread(partition, names, agent_cannot_read(policy))
    shown = set(kept.shown)
    lines = [f"branch: {got.branch or '(detached)'}", f"head: {got.head or '(none)'}"]
    lines += [f"{c.status} {c.path}" for c in got.changes if c.path in shown] or ["clean"]
    lines += counted_notes(kept.hidden, kept.unchecked, 0, "changed paths")
    return ToolOutput("\n".join(lines), untrusted=Untrusted("git.status"))


def _large(root: Path, names: list[str]) -> set[str]:
    """Names whose working-tree file is over AGENT_READ_LIMIT (ARCHITECTURE §7): never diffed for agents."""
    large: set[str] = set()
    for name in names:
        try:
            if os.lstat(root / name).st_size > AGENT_READ_LIMIT:
                large.add(name)
        except OSError:
            continue  # deleted in the working tree: its old content is checked after the diff
    return large


def _block(file: DiffFile) -> list[str]:
    old = file.old_path or file.path
    lines = [f"diff --git a/{old} b/{file.path}"]
    lines.append("--- /dev/null" if file.status == "added" else f"--- a/{old}")
    lines.append("+++ /dev/null" if file.status == "deleted" else f"+++ b/{file.path}")
    if file.is_binary:
        lines.append("Binary file changed")
    elif not file.hunks:
        lines.append("(no text change shown: a mode change, an empty file or a symlink)")
    for hunk in file.hunks:
        lines.append(hunk.header)
        lines += [f"{_SIGN[line.type]}{line.text}" for line in hunk.lines]
    return lines


def _render(files: list[DiffFile], truncated: bool) -> list[str]:
    """Unified text within DIFF_MAX_BYTES. A file too large for the agent is named, not shown; the file that
    does not fit is cut at a line boundary with a marker; the files after it are counted."""
    out: list[str] = []
    used = 0
    for index, file in enumerate(files):
        lines = _block(file)
        size = sum(len(line.encode()) + 1 for line in lines)
        if size > AGENT_READ_LIMIT:
            out.append(f"[diff of {file.path} not shown: larger than the agent read limit]")
            continue
        if used + size > DIFF_MAX_BYTES:
            kept = 0
            for line in lines:
                cost = len(line.encode()) + 1
                if used + cost > DIFF_MAX_BYTES - 200:
                    break
                out.append(line)
                used += cost
                kept += 1
            out.append(f"[diff of {file.path} cut after {kept} of {len(lines)} lines]")
            if index + 1 < len(files):
                out.append(f"[{len(files) - index - 1} more changed files not shown; pass paths to see them]")
            break
        out += lines
        used += size
    if truncated:
        out.append("[git's diff output was larger than one read: some hunks are missing; pass paths]")
    return out


async def diff(ctx: ToolContext, raw: DiffInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    if workspace.base_commit is None:
        raise ToolError("no_base_commit", "this task has no base commit to diff against")
    scopes = [relative(p) for p in raw.paths] if raw.paths else ["."]
    git, target = GitAdapter(), git_target(workspace)
    with workspace_errors():
        policy = await workspace.policy()
        names = await git.changed_paths(target, base=workspace.base_commit)
    kept = await asyncio.to_thread(partition, names.paths, agent_cannot_read(policy))
    in_scope = [n for n in kept.shown if within(n, scopes)]
    large = await asyncio.to_thread(_large, workspace.root, in_scope)
    wanted = [n for n in in_scope if n not in large]
    notes = counted_notes(kept.hidden, kept.unchecked, names.skipped, "changed paths")
    notes += [f"[{n} not shown: larger than the agent read limit]" for n in sorted(large)]
    if not wanted:
        outside = len(kept.shown) - len(in_scope)
        head = (
            f"[no changes within paths; {outside} changed paths outside them]" if outside else "[no changes]"
        )
        return ToolOutput("\n".join([head, *notes]), untrusted=Untrusted("git.diff"))
    with workspace_errors():
        got: Diff = await git.diff(
            target, base=workspace.base_commit, paths=wanted, max_bytes=DIFF_READ_BYTES, exact_files=True
        )
    allowed = set(wanted)
    files = [f for f in got.files if f.path in allowed and (f.old_path is None or f.old_path in allowed)]
    return ToolOutput("\n".join(_render(files, got.truncated) + notes), untrusted=Untrusted("git.diff"))


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
