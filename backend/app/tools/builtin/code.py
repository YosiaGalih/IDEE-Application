"""R1 builtins that write the task worktree (TOOL_CATALOG `code.write_file`, `code.apply_patch`,
`code.delete_file`; spec 3.1d2b Goal 1).

Every write goes through ``TaskWorkspace`` as AGENT_WRITE (secret names, ``.agentignore``, workflows and
``.kk/`` refused), after the gateway checked ``write_paths`` against the agent's filesystem mode. The answer
is TOOL_CATALOG's ``FileChange`` (``path``, ``change``, ``sha256``) for paths the model gave: no repo
content, so it is not wrapped (Lead decision 3.1d2b #1). Errors carry the model's own paths, hunk numbers
and reason codes, never file content.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator

from pydantic import Field

from app.tools.base import RiskLevel, ToolContext, ToolError, ToolOutput, ToolSpec
from app.tools.builtin._common import PATH_MAX_CHARS, StrictInput, relative, workspace_errors, workspace_of
from app.workspace.errors import PatchConflict, PatchRejected, PatchRollbackFailed
from app.workspace.fs import WRITE_LIMIT, FileChange
from app.workspace.patches import MAX_DIFF_BYTES, FilePatch, parse_unified_diff

DELETE_MODE = "scoped_write"  # code.delete_file's precondition; a patch that deletes needs it too (3.1d2b #3)


class WriteFileInput(StrictInput):
    path: str = Field(min_length=1, max_length=PATH_MAX_CHARS)
    content: str = Field(max_length=WRITE_LIMIT)


class DeleteFileInput(StrictInput):
    path: str = Field(min_length=1, max_length=PATH_MAX_CHARS)


class ApplyPatchInput(StrictInput):
    diff: str = Field(min_length=1, max_length=MAX_DIFF_BYTES)


def _change(change: FileChange) -> dict[str, object]:
    return {"path": change.path, "change": change.change, "sha256": change.sha256}


def _patches(diff: str) -> list[FilePatch]:
    try:
        patches = parse_unified_diff(diff)
    except PatchRejected as exc:
        raise ToolError("invalid_patch", f"the patch cannot be applied ({exc.reason})") from None
    if not patches:
        raise ToolError("invalid_patch", "the patch changes no file")
    return patches


def _patch_paths(raw: ApplyPatchInput) -> list[str]:
    return [patch.path for patch in _patches(raw.diff)]


@contextlib.contextmanager
def _write_errors() -> Iterator[None]:
    try:
        with workspace_errors():
            yield
    except PatchConflict as exc:
        where = "; ".join(
            f"{detail.get('path')}"
            + (f" hunk {int(detail['hunk']) + 1}" if isinstance(detail.get("hunk"), int) else "")
            + f" ({detail.get('reason')})"
            for detail in exc.details or []
        )
        raise ToolError("patch_conflict", f"the patch does not apply, nothing was written: {where}") from None
    except PatchRejected as exc:
        raise ToolError("invalid_patch", f"the patch cannot be applied ({exc.reason})") from None
    except PatchRollbackFailed as exc:
        paths = ", ".join(str(detail.get("path")) for detail in exc.details or [])
        raise ToolError(
            "patch_partly_applied", f"the patch was partly applied and could not be rolled back: {paths}"
        ) from None


async def write_file(ctx: ToolContext, raw: WriteFileInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    with _write_errors():
        change = await workspace.write_text(relative(raw.path), raw.content)
    return ToolOutput(json.dumps(_change(change)))


async def delete_file(ctx: ToolContext, raw: DeleteFileInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    with _write_errors():
        change = await workspace.delete(relative(raw.path))
    return ToolOutput(json.dumps(_change(change)))


async def apply_patch(ctx: ToolContext, raw: ApplyPatchInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    deletes = any(patch.new_path is None for patch in _patches(raw.diff))
    if deletes and ctx.grant.permissions.filesystem != DELETE_MODE:
        raise ToolError(
            "delete_not_allowed", f"deleting a file needs filesystem {DELETE_MODE}, as code.delete_file does"
        )
    with _write_errors():
        changes = await workspace.apply_unified_diff(raw.diff)
    return ToolOutput(json.dumps([_change(change) for change in changes]))


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "code.write_file",
            "Write a whole text file in the task worktree (created or replaced).",
            WriteFileInput,
            RiskLevel.R1,
            write_file,
            timeout_s=30,
            side_effect=True,
            write_paths=lambda raw: [raw.path],
        ),
        ToolSpec(
            "code.apply_patch",
            "Apply a unified diff to the task worktree, all files or none (no renames, no binary).",
            ApplyPatchInput,
            RiskLevel.R1,
            apply_patch,
            timeout_s=30,
            side_effect=True,
            write_paths=_patch_paths,
        ),
        ToolSpec(
            "code.delete_file",
            "Delete one file in the task worktree.",
            DeleteFileInput,
            RiskLevel.R1,
            delete_file,
            timeout_s=30,
            side_effect=True,
            write_paths=lambda raw: [raw.path],
        ),
    ]
