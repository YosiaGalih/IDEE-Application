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
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from app.gitops.adapter import GitAdapter, GrepResult
from app.tools.base import RiskLevel, ToolContext, ToolError, ToolOutput, ToolSpec, Untrusted
from app.tools.builtin._common import git_target, workspace_errors, workspace_of
from app.workspace.fs import AGENT_READ_LIMIT
from app.workspace.index import GLOB_MAX_CHARS, check_glob, check_regex, glob_matches, preview
from app.workspace.policy import Access, Budget, Policy, PolicyBudgetExceeded

SANDBOX_DIR = ".kk"
TEXT_TIMEOUT_S = 10.0  # as the user's search (repo_index): git is killed past this
TEXT_OUTPUT_MAX = 1024 * 1024
SEARCH_POLICY_BUDGET = 2_000_000  # [Menebak] the tree's budget: matching cells for one whole search
MAX_AGENT_TEXT_SEARCHES = 2  # [Menebak] agent greps at once in this process; a task never runs two

# Running agent searches, per task. Plain counters: checking then counting has no await in between.
_running: dict[uuid.UUID, int] = {}


class SearchTextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


def _keep(
    found: GrepResult, root: Path, policy: Policy, glob: str | None, limit: int
) -> tuple[list[str], bool]:
    """The hits the agent may see, as ``path:line: preview``. CPU work over many matches: run in a thread."""
    budget = Budget(SEARCH_POLICY_BUDGET)
    sizes: dict[str, bool] = {}
    hits: list[str] = []
    for match in found.matches:
        parts = PurePosixPath(match.path).parts
        if not parts or parts[0] == SANDBOX_DIR:
            continue
        if glob is not None and not glob_matches(glob, match.path):
            continue
        try:
            if policy.denied(parts, Access.AGENT_READ, budget):
                continue
        except PolicyBudgetExceeded:
            return hits, True  # stop here: what is not checked is not shown
        small = sizes.get(match.path)
        if small is None:
            try:
                small = os.lstat(root / match.path).st_size <= AGENT_READ_LIMIT
            except OSError:
                small = False  # gone or unreadable since git read it: not shown
            sizes[match.path] = small
        if not small:
            continue
        if len(hits) >= limit:
            return hits, True
        hits.append(f"{match.path}:{match.line}: {preview(match.text, match.column)}")
    return hits, found.truncated


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
        hits, truncated = await asyncio.to_thread(
            _keep, found, workspace.root, policy, raw.glob, raw.max_results
        )
    lines = hits or ["[no matches]"]
    if truncated:
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
