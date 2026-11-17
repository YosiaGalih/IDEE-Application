"""``context_refs``: what a user attaches to a prompt, checked against the project's scope (WP-3.2a).

A ref names something the agent may later read. It is validated here and stored as a pointer; file contents
are never read into, returned from or stored by this module. Paths go through the ``Workspace`` public API
with the agent read policy, so ``.agentignore``, dependency folders and secret names are refused exactly as
they are for the agent.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.tasks import Task
from app.workspace.errors import PathViolation, WorkspaceNotFound
from app.workspace.fs import Workspace
from app.workspace.paths import split_rel
from app.workspace.policy import Access

MAX_CONTEXT_REFS: Final = 20

UNAVAILABLE: Final = "context_ref_unavailable"
HIDDEN: Final = "context_ref_hidden"
NOT_FOUND: Final = "context_ref_not_found"
INVALID: Final = "context_ref_invalid"

_MESSAGES: Final = {
    UNAVAILABLE: "This kind of reference is not available yet",
    HIDDEN: "This path is not readable by the agent",
    NOT_FOUND: "The referenced item does not exist in this project",
    INVALID: "The reference is not valid",
}
# PathViolation reasons that mean "outside what the agent may read"; the rest are malformed input.
_HIDDEN_REASONS: Final = frozenset({"denied", "agentignore", "outside_root", "symlink"})
_NOT_FOUND_REASONS: Final = frozenset({"not_regular"})


class _Ref(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileRef(_Ref):
    type: Literal["file"]
    path: str = Field(max_length=4096)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


class FolderRef(_Ref):
    type: Literal["folder"]
    path: str = Field(max_length=4096)


class TaskRef(_Ref):
    type: Literal["task"]
    id: uuid.UUID


class DiffRef(_Ref):
    type: Literal["diff"]
    task_id: uuid.UUID


class TestRunRef(_Ref):
    __test__ = False  # not a pytest class

    type: Literal["test_run"]
    id: uuid.UUID


class FindingRef(_Ref):
    type: Literal["finding"]
    id: uuid.UUID


ContextRef = Annotated[
    FileRef | FolderRef | TaskRef | DiffRef | TestRunRef | FindingRef, Field(discriminator="type")
]


def _reason_for(exc: AppError) -> str:
    if isinstance(exc, WorkspaceNotFound):
        return NOT_FOUND
    if isinstance(exc, PathViolation):
        if exc.reason in _HIDDEN_REASONS:
            return HIDDEN
        if exc.reason in _NOT_FOUND_REASONS:
            return NOT_FOUND
    return INVALID


def _error(index: int, reason: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": ["body", "context_refs", index], "msg": _MESSAGES[reason], "reason": reason}],
    )


async def _check_path(workspace: Workspace, ref: FileRef | FolderRef) -> str | None:
    """The reason the path is refused, or None."""
    try:
        parts = split_rel(ref.path)
    except PathViolation:
        return INVALID
    if not parts or "/".join(parts) != ref.path:
        return INVALID  # empty, ".", "a//b", "a/./b", "a/": one spelling per path
    try:
        if isinstance(ref, FileRef):
            if ref.line_start is not None and ref.line_end is not None and ref.line_start > ref.line_end:
                return INVALID
            await workspace.read_file(ref.path, Access.AGENT_READ, max_bytes=1)
        else:
            await workspace.tree(ref.path, Access.AGENT_READ, depth=1, max_entries=1)
    except AppError as exc:
        return _reason_for(exc)
    return None


async def _in_project(session: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID) -> bool:
    found = await session.scalar(select(Task.id).where(Task.id == task_id, Task.project_id == project_id))
    return found is not None


async def validate_refs(
    session: AsyncSession, workspace: Workspace, project_id: uuid.UUID, refs: Sequence[ContextRef]
) -> list[dict[str, object]]:
    """The refs in normalised JSON form (identical duplicates dropped, order kept), or a 422 naming the first
    refused index and why."""
    normalised: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, ref in enumerate(refs):
        reason: str | None = None
        if isinstance(ref, TestRunRef | FindingRef):
            reason = UNAVAILABLE
        elif isinstance(ref, FileRef | FolderRef):
            reason = await _check_path(workspace, ref)
        else:
            task_id = ref.id if isinstance(ref, TaskRef) else ref.task_id
            if not await _in_project(session, project_id, task_id):
                reason = NOT_FOUND
        if reason is not None:
            raise _error(index, reason)
        dumped = ref.model_dump(mode="json", exclude_none=True)
        key = ref.model_dump_json(exclude_none=True)
        if key not in seen:
            seen.add(key)
            normalised.append(dumped)
    return normalised
