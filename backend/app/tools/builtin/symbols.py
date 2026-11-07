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
    partition,
    workspace_errors,
    workspace_of,
)
from app.workspace.fs import AGENT_READ_LIMIT
from app.workspace.index import SYMBOL_MAX_CHARS

SYMBOL_MAX_RESULTS = 50  # [Menebak] hits shown to the model
SymbolKind = Literal["function", "class", "interface", "type", "enum", "struct", "trait", "module"]


class SearchSymbolInput(StrictInput):
    name: str = Field(min_length=1, max_length=SYMBOL_MAX_CHARS)
    kind: SymbolKind | None = None


def _readable(root: Path, rows: list[SymbolRow], shown: set[str]) -> tuple[list[SymbolRow], int, int]:
    """(rows in files the agent can read now, hits in files over the limit, hits in files gone)."""
    sizes: dict[str, int | None] = {}
    kept: list[SymbolRow] = []
    large = gone = 0
    for row in rows:
        if row.path not in shown:
            continue
        if row.path not in sizes:
            try:
                sizes[row.path] = os.lstat(root / row.path).st_size
            except OSError:
                sizes[row.path] = None
        size = sizes[row.path]
        if size is None:
            gone += 1
        elif size > AGENT_READ_LIMIT:
            large += 1
        else:
            kept.append(row)
    return kept, large, gone


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
        hidden = sum(1 for row in found.rows if row.path not in shown)
        kept, large, gone = await asyncio.to_thread(_readable, workspace.root, found.rows, shown)
        lines = [f"index: {found.index_commit}"]
        lines += [f"{row.path}:{row.line}: {row.kind} {row.name}" for row in kept[:SYMBOL_MAX_RESULTS]]
        if not kept:
            lines.append("[no symbols shown]" if hidden or large or gone else "[no symbols]")
        if hidden:
            lines.append(f"[{hidden} hits not shown: agents may not read them, or sandbox output]")
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
