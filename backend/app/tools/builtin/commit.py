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
import os
import stat
from collections.abc import Callable
from pathlib import Path

from pydantic import Field

from app.gitops.adapter import (
    MESSAGE_MAX,
    TASK_BRANCH_PREFIX,
    Author,
    GitAdapter,
    GitTarget,
    below_a_non_folder,
)
from app.gitops.errors import GitError
from app.tools.base import RiskLevel, ToolContext, ToolError, ToolOutput, ToolSpec, Untrusted
from app.tools.builtin._common import (
    StrictInput,
    git_target,
    partition,
    workspace_errors,
    workspace_of,
)
from app.workspace.policy import Access, Budget, Policy

AGENT_AUTHOR = Author("KodeKantor Agent", "agent@kodekantor.invalid")  # never a user's identity (3.1d2b #6)
LIST_MAX = 200  # [Menebak] committed paths named in the answer


class CommitTaskInput(StrictInput):
    message: str = Field(min_length=1, max_length=MESSAGE_MAX)


def _not_committed(count: int, why: str) -> list[str]:
    return [f"[{count} changed paths not committed: {why}]"] if count else []


def _agent_may_not_touch(policy: Policy) -> Callable[[tuple[str, ...], Budget], bool]:
    """What the agent may not read **or** write stays out of its commit: sandboxed code can plant a workflow,
    drop one, or empty the task's .agentignore, all paths the agent itself may not write (AG-13 3.1d2b
    MEDIUM-1; TOOL_CATALOG R4)."""
    return lambda parts, budget: (
        policy.denied(parts, Access.AGENT_READ, budget) or policy.denied(parts, Access.AGENT_WRITE, budget)
    )


def _repositories(root: Path, names: list[str]) -> set[str]:
    """Nested repositories among changed names: untracked ones (``name/``) and tracked gitlinks, whose path
    is a folder holding a repository (``.git``). Committing one would point the project at another
    repository's commit (Lead decision 3.1d2b #5). A file that became a plain folder is an ordinary change
    (``update-index --remove`` drops the file's entry). Parents are not followed (``below_a_non_folder``)."""
    gone = below_a_non_folder(root, names)
    found = {name for name in names if name.endswith("/")}
    for name in names:
        if name in found or name in gone:
            continue
        try:
            if stat.S_ISDIR(os.lstat(root / name).st_mode) and os.path.lexists(root / name / ".git"):
                found.add(name)
        except OSError:
            continue  # deleted: an ordinary change
    return found


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
            if state.branch != workspace.branch:  # a broken worktree, not a refusal (review L4)
                raise ToolError("branch_mismatch", "the worktree is not on the task branch")
            if state.head is None:
                raise ToolError("no_base_commit", "the task branch has no commit to build on")
            policy = await workspace.policy()
            names = await git.changed_paths(target, base=state.head)
            nested = await asyncio.to_thread(_repositories, workspace.root, names.paths)
            files = [name for name in names.paths if name not in nested]
            kept = await asyncio.to_thread(partition, files, _agent_may_not_touch(policy))
            sha = await _commit(git, target, raw.message, kept.shown) if kept.shown else None
            # What the commit holds, read back from git: the answer is evidence, not the request.
            committed = await git.commit_files(target, sha) if sha else []
    if sha:
        lines = [f"commit: {sha}", f"files: {len(committed)}", *committed[:LIST_MAX]]
        if len(committed) > LIST_MAX:
            lines.append(f"[{len(committed) - LIST_MAX} more committed files not named]")
    else:
        lines = ["[nothing to commit]", f"head: {state.head}"]
    unchanged = len(set(kept.shown) - set(committed)) if sha else 0
    if unchanged:
        lines.append(f"[{unchanged} listed paths had no change and are not in the commit]")
    lines += _not_committed(kept.hidden, "agents may not read or write them, or sandbox output")
    lines += _not_committed(kept.unchecked, "not checked against the agent policy (too many or too costly)")
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
