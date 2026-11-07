"""`repo.search_text` over the task worktree (TOOL_CATALOG; spec 3.1d2a Goal 5, WP-2.1d hand-offs).

Not ``services.repo_index.search``: that one is the user's (RBAC, a per-user slot, ``repo/``). Here git greps
the worktree the agent works in, under the same guards (regex shape, glob cost, timeout, output cap, one
thread, ``prlimit``), and every hit is filtered with the agent policy before it can reach the model:

- one ``Budget`` per search for ``.agentignore`` matching: a hostile pattern file stops the search
  (fail closed);
- hits from files over ``AGENT_READ_LIMIT`` are dropped (ARCHITECTURE §7: those files never reach the LLM);
- ``.kk/`` (sandbox output) is never shown: git already excludes it (`SANDBOX_ARTIFACTS_PATTERN`), and the
  filter below drops it again.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import Field

from app.gitops.adapter import GitAdapter, GrepResult
from app.tools.base import RiskLevel, ToolContext, ToolError, ToolOutput, ToolSpec, Untrusted
from app.tools.builtin._common import SANDBOX_DIR, StrictInput, git_target, workspace_errors, workspace_of
from app.workspace.fs import AGENT_READ_LIMIT
from app.workspace.index import GLOB_MAX_CHARS, check_glob, check_regex, glob_matches, preview
from app.workspace.policy import Access, Budget, Policy, PolicyBudgetExceeded

TEXT_TIMEOUT_S = 10.0  # as the user's search (repo_index): git is killed past this
TEXT_OUTPUT_MAX = 1024 * 1024
SEARCH_POLICY_BUDGET = 2_000_000  # [Menebak] the tree's budget: matching cells for one whole search
MAX_AGENT_TEXT_SEARCHES = 2  # [Menebak] agent greps at once in this process; a task never runs two

# Running agent searches, per task. Plain counters: checking then counting has no await in between.
_running: dict[uuid.UUID, int] = {}


class SearchTextInput(StrictInput):
    query: str = Field(min_length=1, max_length=500)
    glob: str | None = Field(None, max_length=GLOB_MAX_CHARS)
    max_results: int = Field(50, ge=1, le=200)
    regex: bool = False


@contextlib.contextmanager
def _slot(task_id: uuid.UUID) -> Iterator[None]:
    if task_id in _running or sum(_running.values()) >= MAX_AGENT_TEXT_SEARCHES:
        raise ToolError("busy", "another search is still running; retry")
    _running[task_id] = 1
    try:
        yield
    finally:
        _running.pop(task_id, None)


@dataclass
class _Kept:
    hits: list[str] = field(default_factory=list)
    truncated: bool = False
    large: int = 0  # hits in files over AGENT_READ_LIMIT: left out, and said
    unreadable: int = 0  # hits in files gone or unreadable since git read them: left out, and said


def _keep(found: GrepResult, root: Path, policy: Policy, glob: str | None, limit: int) -> _Kept:
    """The hits the agent may see, as ``path:line: preview``. CPU work over many matches: run in a thread.
    The policy is evaluated once per path, all under one Budget (fail closed when it runs out)."""
    budget = Budget(SEARCH_POLICY_BUDGET)
    denied: dict[str, bool] = {}
    sizes: dict[str, bool | None] = {}  # True small, False large, None unreadable
    kept = _Kept()
    for match in found.matches:
        parts = PurePosixPath(match.path).parts
        if not parts or parts[0] == SANDBOX_DIR:
            continue
        if glob is not None and not glob_matches(glob, match.path):
            continue
        hidden = denied.get(match.path)
        if hidden is None:
            try:
                hidden = denied[match.path] = policy.denied(parts, Access.AGENT_READ, budget)
            except PolicyBudgetExceeded:
                kept.truncated = True  # stop here: what is not checked is not shown
                return kept
        if hidden:
            continue
        if match.path not in sizes:
            try:
                sizes[match.path] = os.lstat(root / match.path).st_size <= AGENT_READ_LIMIT
            except OSError:
                sizes[match.path] = None
        small = sizes[match.path]
        if small is None:
            kept.unreadable += 1
            continue
        if not small:
            kept.large += 1
            continue
        if len(kept.hits) >= limit:
            kept.truncated = True
            return kept
        kept.hits.append(f"{match.path}:{match.line}: {preview(match.text, match.column)}")
    kept.truncated = kept.truncated or found.truncated
    return kept


async def search_text(ctx: ToolContext, raw: SearchTextInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    if any(ch in raw.query for ch in "\0\n\r"):
        raise ToolError("invalid_query", "the query must be one line")
    try:
        if raw.glob is not None:
            check_glob(raw.glob)
        if raw.regex:
            check_regex(raw.query)
    except ValueError as exc:
        raise ToolError("invalid_query", str(exc)) from None
    with _slot(ctx.task_id), workspace_errors():
        policy = await workspace.policy()
        found = await GitAdapter().grep(
            git_target(workspace),
            [raw.query],
            commit=None,
            fixed=not raw.regex,
            ignore_case=False,
            only_matching=False,
            timeout_s=TEXT_TIMEOUT_S,
            max_output=TEXT_OUTPUT_MAX,
            threads=1,
        )
        kept = await asyncio.to_thread(_keep, found, workspace.root, policy, raw.glob, raw.max_results)
    partial = kept.large or kept.unreadable or kept.truncated or found.skipped_paths
    lines = kept.hits or ["[no matches shown]" if partial else "[no matches]"]
    if kept.large:
        lines.append(f"[{kept.large} hits in files over the agent read limit were left out]")
    if kept.unreadable:
        lines.append(f"[{kept.unreadable} hits in files gone or unreadable since the search were left out]")
    if kept.truncated:
        lines.append("[truncated: more matches than shown; narrow the query or the glob]")
    if found.skipped_paths:
        lines.append(f"[{found.skipped_paths} hits in files whose names are not UTF-8 were left out]")
    return ToolOutput("\n".join(lines), untrusted=Untrusted("repo.search_text"))


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "repo.search_text",
            "Search text (literal, or POSIX ERE with regex=true) in the task worktree: path:line: text.",
            SearchTextInput,
            RiskLevel.R0,
            search_text,
            timeout_s=30,
        )
    ]
