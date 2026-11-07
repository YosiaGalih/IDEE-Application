"""`git.commit_task` (TOOL_CATALOG; spec 3.1d2b Goal 2; Lead decisions 3.1d2b #4-#6).

Under the worktree lock every write takes (WP-2.1c I-b), on the task branch only:
1. the names that changed against HEAD are listed without staging anything;
2. they are filtered with the agent policy (one Budget, in a thread). Names agents may not read, ``.kk/``,
   nested repositories, names that are not UTF-8 and names past the cap are not committed, and counted;
3. ``GitAdapter.commit(files=...)`` stages exactly those files and refuses when the index then differs from
   HEAD anywhere else, e.g. a folder holding a hidden file replaced by a file. Better no commit than a
   silent commit of what the agent may not see.

The answer names the committed files (repo names: wrapped as untrusted) and the commit.
"""

from __future__ import annotations

import asyncio

from pydantic import Field

from app.gitops.adapter import TASK_BRANCH_PREFIX, Author, GitAdapter, GitTarget
from app.gitops.errors import GitError
from app.tools.base import RiskLevel, ToolContext, ToolError, ToolOutput, ToolSpec, Untrusted
from app.tools.builtin._common import (
    StrictInput,
    agent_cannot_read,
    git_target,
    partition,
    workspace_errors,
    workspace_of,
)

AGENT_AUTHOR = Author("KodeKantor Agent", "agent@kodekantor.invalid")  # never a user's identity (3.1d2b #6)
LIST_MAX = 200  # committed paths named in the answer


class CommitTaskInput(StrictInput):
    message: str = Field(min_length=1, max_length=5000)


def _not_committed(count: int, why: str) -> list[str]:
    return [f"[{count} changed paths not committed: {why}]"] if count else []


async def _commit(git: GitAdapter, target: GitTarget, message: str, files: list[str]) -> str | None:
    try:
        return await git.commit(target, message, AGENT_AUTHOR, files=files)
    except GitError as exc:
        if exc.reason != "staged_outside_files":
            raise
        raise ToolError(
            "hidden_paths_affected",
            "a visible change would also change paths agents may not read (e.g. a folder replaced by a file);"
            " nothing was committed",
        ) from None


async def commit_task(ctx: ToolContext, raw: CommitTaskInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    if not workspace.branch.startswith(TASK_BRANCH_PREFIX):
        raise ToolError("not_task_branch", f"commits go to the task branch only ({TASK_BRANCH_PREFIX}*)")
    git, target = GitAdapter(), git_target(workspace)
    with workspace_errors():
        async with workspace.exclusive():
            state = await git.status(target)
            if state.branch != workspace.branch:
                raise ToolError("not_task_branch", "the worktree is not on the task branch")
            if state.head is None:
                raise ToolError("no_base_commit", "the task branch has no commit to build on")
            policy = await workspace.policy()
            names = await git.changed_paths(target, base=state.head)
            nested = [name for name in names.paths if name.endswith("/")]
            files = [name for name in names.paths if not name.endswith("/")]
            kept = await asyncio.to_thread(partition, files, agent_cannot_read(policy))
            sha = await _commit(git, target, raw.message, kept.shown) if kept.shown else None
    lines = ["[nothing to commit]"]
    if sha:
        lines = [f"commit: {sha}", f"files: {len(kept.shown)}", *kept.shown[:LIST_MAX]]
    if sha and len(kept.shown) > LIST_MAX:
        lines.append(f"[{len(kept.shown) - LIST_MAX} more committed files not named]")
    lines += _not_committed(kept.hidden, "agents may not read them, or sandbox output")
    lines += _not_committed(kept.unchecked, "not checked against the agent policy (too many, too costly)")
    lines += _not_committed(len(nested), "nested repositories")
    lines += _not_committed(names.skipped, "names that are not UTF-8")
    return ToolOutput("\n".join(lines), untrusted=Untrusted("git.commit_task"))


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "git.commit_task",
            "Commit the task worktree's changes to the task branch (paths agents may not read are left out).",
            CommitTaskInput,
            RiskLevel.R1,
            commit_task,
            timeout_s=60,
            side_effect=True,
        )
    ]
