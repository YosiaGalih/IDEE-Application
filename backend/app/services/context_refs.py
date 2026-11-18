"""``context_refs``: what a user attaches to a prompt, checked against the project's scope (WP-3.2a).

A ref names something the agent may later read. It is validated here and stored as a pointer; file contents
are never read into, returned from or stored by this module. Paths go through ``Workspace.probe`` with the
agent read policy, so ``.agentignore``, dependency folders and secret names are refused exactly as they are
for the agent, and nothing is read or hashed.

Two steps, so the disk is never touched while a database transaction is open (Ruling R11):
1. ``check_paths``, with no session: identical refs are dropped, then kinds and file/folder paths are checked.
2. ``check_task_refs``, in the inserting transaction: task and diff refs must name a task of the project.
The 422 always names the first refused index the client sent.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.tasks import Task
from app.services.projects import has_hidden_characters
from app.workspace.errors import PathViolation, WorkspaceNotFound
from app.workspace.fs import Workspace
from app.workspace.paths import split_rel
from app.workspace.policy import Access

logger = get_logger(__name__)

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


@dataclass(frozen=True)
class RefCheck:
    """What ``check_paths`` found: the unique refs, each with the index the client sent it at, and the first
    refusal it found (index, reason). A refusal waits for ``check_task_refs`` only when a task or diff ref
    comes before it; ``unique`` then stops at the refused index."""

    unique: tuple[tuple[int, ContextRef], ...]
    refused: tuple[int, str] | None = None


def has_path_refs(refs: Sequence[ContextRef]) -> bool:
    return any(isinstance(ref, FileRef | FolderRef) for ref in refs)


def _error(index: int, reason: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": ["body", "context_refs", index], "msg": _MESSAGES[reason], "reason": reason}],
    )


def _unique(refs: Sequence[ContextRef]) -> list[tuple[int, ContextRef]]:
    """Identical refs dropped (the first index kept), before any IO."""
    seen: set[str] = set()
    unique: list[tuple[int, ContextRef]] = []
    for index, ref in enumerate(refs):
        key = ref.model_dump_json(exclude_none=True)
        if key not in seen:
            seen.add(key)
            unique.append((index, ref))
    return unique


def _violation_reason(exc: PathViolation) -> str:
    if exc.reason in _HIDDEN_REASONS:
        return HIDDEN
    if exc.reason in _NOT_FOUND_REASONS:
        return NOT_FOUND
    return INVALID


async def _path_rejection(
    workspace: Workspace, project_id: uuid.UUID, ref: FileRef | FolderRef
) -> str | None:
    """The reason the path is refused, or None. Checks without IO come first; the probe reads no content."""
    if has_hidden_characters(ref.path):
        return INVALID  # R12: such a name would reach A04 prompts and the UI
    try:
        parts = split_rel(ref.path)
    except PathViolation:
        return INVALID
    if not parts or "/".join(parts) != ref.path:
        return INVALID  # empty, ".", "a//b", "a/./b", "a/": one spelling per path
    if isinstance(ref, FileRef) and ref.line_start is not None and ref.line_end is not None:
        if ref.line_start > ref.line_end:
            return INVALID
    try:
        kind = await workspace.probe(ref.path, Access.AGENT_READ)
    except WorkspaceNotFound:
        # The same exception names a missing subpath (the user's mistake) and a missing root (ours).
        if not await asyncio.to_thread(workspace.root.is_dir):
            logger.error("context_refs_repository_root_missing", project_id=str(project_id))
            raise AppError("INTERNAL_ERROR", "Internal error", http_status=500) from None
        return NOT_FOUND
    except PathViolation as exc:  # any other AppError is not input: it propagates
        if exc.reason == "agentignore":  # unreadable, oversize or invalid: every path is hidden (fail closed)
            logger.warning("context_refs_agentignore_invalid", project_id=str(project_id))
        return _violation_reason(exc)
    return None if kind == ("file" if isinstance(ref, FileRef) else "dir") else NOT_FOUND


async def check_paths(workspace: Workspace, project_id: uuid.UUID, refs: Sequence[ContextRef]) -> RefCheck:
    """Everything about the refs that needs no database; call it with no session open (R11).

    Identical refs are dropped first. Then, in order: kinds not available yet, and file/folder paths (one
    spelling, no hidden characters, present as the kind named, readable by the agent). Stops at the first
    refusal, which is raised here unless a task or diff ref before it still has to be checked.
    """
    unique = _unique(refs)
    for position, (index, ref) in enumerate(unique):
        if isinstance(ref, TestRunRef | FindingRef):
            reason: str | None = UNAVAILABLE
        elif isinstance(ref, FileRef | FolderRef):
            reason = await _path_rejection(workspace, project_id, ref)
        else:
            continue
        if reason is not None:
            before = unique[:position]
            if not any(isinstance(earlier, TaskRef | DiffRef) for _, earlier in before):
                raise _error(index, reason)
            return RefCheck(tuple(before), (index, reason))
    return RefCheck(tuple(unique))


async def _in_project(session: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID) -> bool:
    found = await session.scalar(select(Task.id).where(Task.id == task_id, Task.project_id == project_id))
    return found is not None


async def check_task_refs(
    session: AsyncSession, project_id: uuid.UUID, check: RefCheck
) -> list[dict[str, object]]:
    """The refs in normalised JSON form (order kept), or a 422 naming the first refused index: a task or diff
    ref must name a task of this project; a refusal ``check_paths`` held comes after them."""
    for index, ref in check.unique:
        if isinstance(ref, TaskRef | DiffRef):
            task_id = ref.id if isinstance(ref, TaskRef) else ref.task_id
            if not await _in_project(session, project_id, task_id):
                raise _error(index, NOT_FOUND)
    if check.refused is not None:
        raise _error(*check.refused)
    return [ref.model_dump(mode="json", exclude_none=True) for _, ref in check.unique]
