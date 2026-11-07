"""`security.scan_secrets`: a deterministic scan of what the task would commit (SECURITY_MODEL T5; spec
3.1d2a Goal 11). No subprocess besides git (``ls-files``), no model, no content in the result.

Scanned: tracked files plus untracked ones git would not ignore (what ``git.commit_task`` stages). A file with
a secret name (``.env*``, ``*.env``, ``*.pem``, ...) is reported by name and never read. Every other file is
read through ``TaskWorkspace`` (AGENT_READ, at most ``AGENT_READ_LIMIT``) and matched with the redaction
patterns (``Redactor.find``). Files the agent may not read for another reason (``.agentignore``, dependency
folders) are counted, not shown. Findings are ``path:line: rule``; storing them is the review stage's job.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from app.core.redaction import Redactor
from app.gitops.adapter import GitAdapter
from app.tools.base import RiskLevel, ToolContext, ToolOutput, ToolSpec, Untrusted
from app.tools.builtin._common import git_target, relative, workspace_errors, workspace_of
from app.workspace.errors import PathViolation, WorkspaceNotFound
from app.workspace.policy import Access, is_secret_name

SANDBOX_DIR = ".kk"
MAX_FINDINGS = 200
MAX_FILES = 5000  # [Menebak] files read per scan; the rest is reported as not scanned


class ScanSecretsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] | None = Field(None, max_length=100)


def _within(path: str, scopes: list[str]) -> bool:
    return any(scope == "." or path == scope or path.startswith(f"{scope}/") for scope in scopes)


async def scan_secrets(ctx: ToolContext, raw: ScanSecretsInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    scopes = [relative(p) for p in raw.paths] if raw.paths else ["."]
    with workspace_errors():
        policy = await workspace.policy()
        names = await GitAdapter().worktree_files(git_target(workspace))
    finder = Redactor()
    findings: list[str] = []
    hidden = unread = read = 0
    for name in names:
        parts = PurePosixPath(name).parts
        if not parts or parts[0] == SANDBOX_DIR or not _within(name, scopes):
            continue
        if is_secret_name(parts):
            findings.append(f"{name}: secret_file")
            continue
        if policy.denied(parts, Access.AGENT_READ):
            hidden += 1
            continue
        if read >= MAX_FILES:
            unread += 1
            continue
        read += 1
        try:
            got = await workspace.read_text(name)
        except (PathViolation, WorkspaceNotFound):
            unread += 1  # a symlink, or gone since git listed it
            continue
        if got.content is None:
            unread += 1  # binary or over the agent read limit
            continue
        findings += [f"{name}:{f.line}: {f.rule}" for f in finder.find(got.content)]
    lines = findings[:MAX_FINDINGS] or ["[no secrets found]"]
    if len(findings) > MAX_FINDINGS:
        lines.append(f"[truncated: {len(findings) - MAX_FINDINGS} more findings]")
    if hidden:
        lines.append(f"[{hidden} files not scanned: agents may not read them]")
    if unread:
        lines.append(f"[{unread} files not scanned: binary, too large, or not regular files]")
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
