"""`repo.search_symbol` (TOOL_CATALOG; spec 3.1d2b Goal 3, Lead decision 3.1d2b #7).

The index is the project's (``repo/`` HEAD, built by the index job), reached through ``ToolPorts.symbols``
(ADR-022), not the task worktree; the answer names the commit it comes from. The candidates are filtered
with the task's agent policy (both ``.agentignore`` files, one Budget, in a thread): a hit in a path the
agent may not read, in ``.kk/``, in a file over ``AGENT_READ_LIMIT`` in the worktree, or in a file the
worktree no longer has is left out and counted. Symbol names are repo text: the answer is wrapped.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.gitops.adapter import GitAdapter, below_a_non_folder
from app.gitops.errors import GitError
from app.tools.base import (
    RiskLevel,
    SymbolIndex,
    SymbolRow,
    ToolContext,
    ToolOutput,
    ToolSpec,
    Untrusted,
)
from app.tools.builtin._common import (
    StrictInput,
    agent_cannot_read,
    git_target,
    partition,
    workspace_errors,
    workspace_of,
)
from app.workspace.fs import AGENT_READ_LIMIT, TaskWorkspace
from app.workspace.index import SYMBOL_MAX_CHARS

SYMBOL_MAX_RESULTS = 50  # [Menebak] hits shown to the model
SymbolKind = Literal["function", "class", "interface", "type", "enum", "struct", "trait", "module"]


class SearchSymbolInput(StrictInput):
    name: str = Field(min_length=1, max_length=SYMBOL_MAX_CHARS)
    kind: SymbolKind | None = None


def _readable(root: Path, rows: list[SymbolRow], shown: set[str]) -> tuple[list[SymbolRow], int, int]:
    """(rows in files the agent can read now, hits in files over the limit, hits in files gone). A path below
    a symlink or a file is gone: its parents are never followed, so nothing outside the root is measured
    (/code-review 3.1d2b spec)."""
    names = list(dict.fromkeys(row.path for row in rows if row.path in shown))
    below = below_a_non_folder(root, names)
    sizes: dict[str, int | None] = {}
    for name in names:
        try:
            sizes[name] = None if name in below else os.lstat(root / name).st_size
        except OSError:
            sizes[name] = None
    kept: list[SymbolRow] = []
    large = gone = 0
    for row in rows:
        if row.path not in shown:
            continue
        size = sizes[row.path]
        if size is None:
            gone += 1
        elif size > AGENT_READ_LIMIT:
            large += 1
        else:
            kept.append(row)
    return kept, large, gone


async def _worktree_head(workspace: TaskWorkspace) -> str:
    try:
        return await GitAdapter().current_commit(git_target(workspace)) or "(none)"
    except GitError:
        return "(unknown)"


def specs(index: SymbolIndex) -> list[ToolSpec]:
    async def search_symbol(ctx: ToolContext, raw: SearchSymbolInput) -> ToolOutput:
        workspace = workspace_of(ctx)
        with workspace_errors():
            policy = await workspace.policy()
        found = await index.candidates(ctx.project_id, raw.name, raw.kind)
        if found.index_commit is None:
            return ToolOutput(
                "[no symbol index yet: the project has not been indexed]",
                untrusted=Untrusted("repo.search_symbol"),
            )
        paths = list(dict.fromkeys(row.path for row in found.rows))
        allowed = await asyncio.to_thread(partition, paths, agent_cannot_read(policy))
        shown = set(allowed.shown)
        not_checked = set(paths[len(paths) - allowed.unchecked :]) if allowed.unchecked else set()
        unchecked = sum(1 for row in found.rows if row.path in not_checked)
        kept, large, gone = await asyncio.to_thread(_readable, workspace.root, found.rows, shown)
        head = await _worktree_head(workspace)
        lines = [f"index: {found.index_commit} (committed content only; uncommitted edits are not indexed)"]
        if head != found.index_commit:
            lines.append(
                f"[the index is of commit {found.index_commit}; the worktree is at {head}:"
                " line numbers may differ and new symbols are missing]"
            )
        lines += [f"{row.path}:{row.line}: {row.kind} {row.name}" for row in kept[:SYMBOL_MAX_RESULTS]]
        # Hits in hidden paths leave no trace, not even a count: a count answers "does a hidden file define
        # a symbol matching this?" one letter at a time (AG-13 3.1d2b LOW-1), as repo.search_text never does.
        if not kept:
            lines.append("[no symbols shown]" if unchecked or large or gone else "[no symbols]")
        if unchecked:
            lines.append(
                f"[{unchecked} hits not checked against the agent policy (too many or too costly): not shown]"
            )
        if large:
            lines.append(f"[{large} hits in files over the agent read limit were left out]")
        if gone:
            lines.append(f"[{gone} hits in files no longer in the worktree were left out]")
        if found.truncated or len(kept) > SYMBOL_MAX_RESULTS:
            lines.append("[truncated: more symbols match; narrow the name or the kind]")
        if found.index_truncated:
            lines.append("[the index itself stopped at a limit: some symbols are missing]")
        return ToolOutput("\n".join(lines), untrusted=Untrusted("repo.search_symbol"))

    return [
        ToolSpec(
            "repo.search_symbol",
            "Find definitions (functions, classes, ...) by name in the project index: path:line: kind name.",
            SearchSymbolInput,
            RiskLevel.R0,
            search_symbol,
            timeout_s=30,
        )
    ]
