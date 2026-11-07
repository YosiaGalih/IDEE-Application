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

from app.gitops.adapter import GitAdapter, below_a_non_folder
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
STAGE_MAX_BYTES = 8 * 1024 * 1024  # [Menebak] working-tree bytes one diff stages on the worker's tmpfs
DIFF_MAX_FILES = 2000  # [Menebak] far more than DIFF_MAX_BYTES of text can show
ARGV_MAX_BYTES = 256 * 1024  # names on one git command line, well below ARG_MAX (2 MiB with the environment)
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


def _select(root: Path, names: list[str]) -> tuple[list[str], list[str], int]:
    """(to diff, over AGENT_READ_LIMIT or unreadable, deferred): a working-tree file over the limit is never
    diffed for the agent (ARCHITECTURE §7). One call stages at most STAGE_MAX_BYTES, because the scratch index
    lives on the worker's tmpfs (AG-13 WP-3.1d2a MEDIUM-4), and passes git at most DIFF_MAX_FILES names and
    ARGV_MAX_BYTES of them (LOW-D2: never E2BIG); the rest waits for another call. A deleted file, or one
    below a symlink or a file (git sees it as deleted, LOW-D1/LOW-D3), stages nothing."""
    gone = below_a_non_folder(root, names)
    wanted: list[str] = []
    refused: list[str] = []
    deferred = 0
    staged = 0
    argv = 0
    for name in names:
        size = 0  # deleted in the working tree: its old content is checked after the diff
        if name not in gone:
            try:
                size = os.lstat(root / name).st_size
            except FileNotFoundError:
                pass
            except OSError:
                refused.append(name)
                continue
        cost = len(os.fsencode(name)) + 1
        if size > AGENT_READ_LIMIT:
            refused.append(name)
        elif wanted and (
            staged + size > STAGE_MAX_BYTES or len(wanted) >= DIFF_MAX_FILES or argv + cost > ARGV_MAX_BYTES
        ):
            deferred += 1
        else:
            staged += size
            argv += cost
            wanted.append(name)
    return wanted, refused, deferred


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
    wanted, large, deferred = await asyncio.to_thread(_select, workspace.root, in_scope)
    notes = counted_notes(kept.hidden, kept.unchecked, names.skipped, "changed paths")
    notes += [f"[{n} not shown: larger than the agent read limit, or unreadable]" for n in large]
    if deferred:
        notes.append(f"[{deferred} more changed files not diffed in one call; pass paths to see them]")
    if not wanted:
        outside = len(kept.shown) - len(in_scope)
        if outside:
            head = f"[no changes within paths; {outside} changed paths outside them]"
        else:
            head = "[no changes shown]" if large or deferred else "[no changes]"
        return ToolOutput("\n".join([head, *notes]), untrusted=Untrusted("git.diff"))
    with workspace_errors():
        got: Diff = await git.diff(
            target, base=workspace.base_commit, paths=wanted, max_bytes=DIFF_READ_BYTES, exact_files=True
        )
    allowed = set(wanted)
    files = [f for f in got.files if f.path in allowed and (f.old_path is None or f.old_path in allowed)]
    body = _render(files, got.truncated) if files else ["[no text diff for the requested paths]"]
    return ToolOutput("\n".join(body + notes), untrusted=Untrusted("git.diff"))


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
