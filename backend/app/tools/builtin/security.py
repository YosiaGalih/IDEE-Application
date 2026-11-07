"""`security.scan_secrets`: a deterministic scan of what the task would commit (SECURITY_MODEL T5; spec
3.1d2a Goal 11). No subprocess besides git (``ls-files``), no model, no value in the result.

Scanned: tracked files plus untracked ones git would not ignore (what a commit of the worktree takes). Names
are filtered first with one policy Budget, in a thread: a path hidden by ``.agentignore`` or a dependency
folder is counted, never named; a secret name (``.env*``, ``*.env``, ``*.pem``, ...) where nothing else hides
it is reported by name and never read. Every other file is read through ``TaskWorkspace`` (AGENT_READ, at most
``AGENT_READ_LIMIT``) and matched in a thread with the redaction patterns (``Redactor.find``), within
MAX_FILES and MAX_SCAN_BYTES. Configured platform secrets (the gateway's literal list) are not looked for:
handing their values to a scan would make it an oracle; the gateway still redacts them from every output.
Everything not scanned is counted in the result, and "no secrets" is only said for what was scanned.
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from app.core.redaction import Redactor
from app.gitops.adapter import GitAdapter
from app.tools.base import RiskLevel, ToolContext, ToolOutput, ToolSpec, Untrusted
from app.tools.builtin._common import git_target, partition, relative, within, workspace_errors, workspace_of
from app.workspace.errors import PathViolation, WorkspaceNotFound
from app.workspace.policy import Access, Budget, Policy, is_secret_name

MAX_FINDINGS = 200
MAX_FILES = 5000  # [Menebak] files read per scan
MAX_SCAN_BYTES = (
    32 * 1024 * 1024
)  # [Menebak] bytes matched per scan (the loop is never held: matching runs in a thread)


class ScanSecretsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] | None = Field(None, max_length=100)


def _plural(count: int, what: str) -> str:
    return f"[{count} files not scanned: {what}]"


async def scan_secrets(ctx: ToolContext, raw: ScanSecretsInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    scopes = [relative(p) for p in raw.paths] if raw.paths else ["."]
    with workspace_errors():
        policy: Policy = await workspace.policy()
        listing = await GitAdapter().worktree_files(git_target(workspace))
    names = [n for n in listing.paths if within(n, scopes)]

    def hidden(parts: tuple[str, ...], budget: Budget) -> bool:
        return policy.hides(parts, Access.AGENT_READ, budget)

    kept = await asyncio.to_thread(partition, names, hidden)
    secret_files = [n for n in kept.shown if is_secret_name(PurePosixPath(n).parts)]
    to_read = [n for n in kept.shown if not is_secret_name(PurePosixPath(n).parts)]
    finder = Redactor()
    findings = [f"{name}: secret_file" for name in secret_files]
    scanned = unread = over_budget = 0
    used = 0
    for index, name in enumerate(to_read):
        if index >= MAX_FILES or used >= MAX_SCAN_BYTES:
            over_budget = len(to_read) - index
            break
        try:
            got = await workspace.read_text(name)
        except (PathViolation, WorkspaceNotFound):
            unread += 1  # gone since git listed it, or not a regular file (a symlink)
            continue
        if got.content is None:
            unread += 1  # binary, or over the agent read limit
            continue
        used += len(got.content.encode())
        scanned += 1
        found = await asyncio.to_thread(finder.find, got.content)
        findings += [f"{name}:{f.line}: {f.rule}" for f in found]
    lines = findings[:MAX_FINDINGS]
    if len(findings) > MAX_FINDINGS:
        lines.append(f"[truncated: {len(findings) - MAX_FINDINGS} more findings]")
    if not names and listing.paths:
        lines.append("[nothing scanned: no file within paths]")
    elif not findings:
        lines.append(f"[no secrets found in {scanned} scanned files]" if scanned else "[nothing scanned]")
    if kept.hidden:
        lines.append(_plural(kept.hidden, "agents may not read them"))
    if kept.unchecked:
        lines.append(_plural(kept.unchecked, "not checked against the agent policy (too many, too costly)"))
    if listing.skipped:
        lines.append(f"[{listing.skipped} files with names that are not UTF-8: not scanned]")
    if unread:
        lines.append(_plural(unread, "binary, over the agent read limit, gone, or not regular files"))
    if over_budget:
        lines.append(_plural(over_budget, "over the byte budget of one scan; narrow with paths"))
    return ToolOutput("\n".join(lines), untrusted=Untrusted("security.scan_secrets"))


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "security.scan_secrets",
            "Find secret-looking values and secret files in what the task would commit (path:line: rule).",
            ScanSecretsInput,
            RiskLevel.R0,
            scan_secrets,
            timeout_s=60,
        )
    ]
