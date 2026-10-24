"""Task state machine (ARCHITECTURE §5.1, ADR-006, ADR-021). The only code allowed to write ``tasks.status``.

``transition`` runs in the caller's transaction and never commits. After committing, the caller runs
``core.events.publish_pending`` so subscribers see the ``task.status_changed`` event.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, NoReturn

from sqlalchemy import func, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import set_committed_value

from app.core.audit import AuditWriter
from app.core.errors import AppError
from app.core.events import emit_task_event
from app.models.audit import ACTOR_TYPES
from app.models.tasks import STATUS_WRITER, STATUS_WRITER_SETTING, Task


class TaskStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_CLARIFICATION = "needs_clarification"
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    VALIDATING = "validating"
    NEEDS_CHANGES = "needs_changes"
    WAITING_APPROVAL = "waiting_approval"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


S = TaskStatus
ALLOWED: dict[TaskStatus, frozenset[TaskStatus]] = {
    S.DRAFT: frozenset({S.PLANNED, S.NEEDS_CLARIFICATION, S.FAILED, S.CANCELLED, S.WAITING_APPROVAL}),
    S.NEEDS_CLARIFICATION: frozenset({S.DRAFT, S.CANCELLED}),
    S.PLANNED: frozenset({S.QUEUED, S.NEEDS_CLARIFICATION, S.CANCELLED}),
    S.QUEUED: frozenset({S.RUNNING, S.CANCELLED}),
    S.RUNNING: frozenset({S.VALIDATING, S.WAITING_APPROVAL, S.FAILED, S.INTERRUPTED, S.CANCELLED}),
    S.VALIDATING: frozenset(
        {S.COMPLETED, S.NEEDS_CHANGES, S.WAITING_APPROVAL, S.FAILED, S.INTERRUPTED, S.CANCELLED}
    ),
    S.NEEDS_CHANGES: frozenset({S.QUEUED, S.WAITING_APPROVAL, S.CANCELLED}),
    S.WAITING_APPROVAL: frozenset(
        {S.QUEUED, S.COMPLETED, S.REJECTED, S.CANCELLED, S.NEEDS_CHANGES, S.FAILED, S.DRAFT}
    ),
    S.INTERRUPTED: frozenset({S.QUEUED, S.FAILED, S.CANCELLED}),
    S.FAILED: frozenset({S.QUEUED, S.CANCELLED}),
    S.COMPLETED: frozenset({S.QUEUED}),
    S.CANCELLED: frozenset(),
    S.REJECTED: frozenset(),
}
TERMINAL = frozenset(status for status, targets in ALLOWED.items() if not targets)
# Statuses that end a run; completed_at is stamped on entry.
_FINISHED = frozenset({S.COMPLETED, S.FAILED, S.CANCELLED, S.REJECTED})
# Remediation and reopen start a new iteration (DATA_MODEL tasks.iteration).
_NEW_ITERATION_FROM = frozenset({S.COMPLETED, S.NEEDS_CHANGES})


@dataclass(frozen=True)
class Actor:
    type: Literal["user", "agent", "system"]
    id: str


_REASON_COLUMN_LENGTH = 200
_ACTOR_ID_LENGTH = 80


def _check_inputs(db: AsyncSession, task: Task, actor: Actor) -> None:
    """Refuse calls that could leave a status change without its audit row, or a lying local object."""
    if actor.type not in ACTOR_TYPES or not 1 <= len(actor.id) <= _ACTOR_ID_LENGTH:
        raise ValueError(f"actor must be one of {ACTOR_TYPES} with an id of 1-{_ACTOR_ID_LENGTH} characters")
    if object_session(task) is not db.sync_session:
        raise ValueError("task must belong to the session that runs the transition")
    state = inspect(task)
    if state.attrs.status.history.has_changes() or state.attrs.version.history.has_changes():
        raise ValueError("task.status was modified outside transition")


async def transition(
    db: AsyncSession,
    task: Task,
    to: TaskStatus,
    *,
    reason: str | None,
    actor: Actor,
    audit: AuditWriter,
) -> None:
    """Move ``task`` to ``to`` or raise; the row, its audit entry and its event change together or not at all.

    ``audit`` carries the redactor with the configured secret literals; ``reason`` is redacted with it.
    """
    source = TaskStatus(task.status)
    target = TaskStatus(to)
    if target not in ALLOWED[source]:
        raise AppError(
            "INVALID_TRANSITION",
            f"A task cannot move from {source.value} to {target.value}",
            http_status=409,
            details=[{"from": source.value, "to": target.value}],
        )
    _check_inputs(db, task, actor)
    safe_reason = audit.redactor.redact(reason) if reason else None

    values: dict[str, Any] = {
        "status": target.value,
        # The full (redacted) reason goes to the event and audit; the column keeps what fits.
        "status_reason": safe_reason[:_REASON_COLUMN_LENGTH] if safe_reason else None,
        "version": Task.version + 1,
        "updated_at": func.now(),
    }
    if target is S.RUNNING:
        values["started_at"] = func.coalesce(Task.started_at, func.now())
    if target in _FINISHED:
        values["completed_at"] = func.now()
    if target is S.QUEUED:
        values["completed_at"] = None
        if source in _NEW_ITERATION_FROM:
            values["iteration"] = Task.iteration + 1

    change = {"from": source.value, "to": target.value, "reason": safe_reason}
    # One savepoint: if the audit row or the event cannot be written, the status change is undone with them.
    async with db.begin_nested():
        # Lets the tasks_status_guard trigger accept this write (CLAUDE.md #4 backstop in the database).
        await db.execute(select(func.set_config(STATUS_WRITER_SETTING, STATUS_WRITER, True)))
        row = (
            await db.execute(
                update(Task)
                .where(Task.id == task.id, Task.version == task.version, Task.status == source.value)
                .values(**values)
                .returning(
                    Task.status,
                    Task.status_reason,
                    Task.version,
                    Task.iteration,
                    Task.started_at,
                    Task.completed_at,
                    Task.updated_at,
                )
                .execution_options(synchronize_session=False)
            )
        ).one_or_none()
        await db.execute(select(func.set_config(STATUS_WRITER_SETTING, "", True)))
        if row is None:
            await _raise_conflict(db, task)
        await audit.write(
            db,
            actor_type=actor.type,
            actor_id=actor.id,
            action="task.status_changed",
            result="success",
            resource_type="task",
            resource_id=str(task.id),
            project_id=task.project_id,
            metadata=change,
        )
        seq = await emit_task_event(db, None, task.id, "task.status_changed", change, redactor=audit.redactor)

    assert row is not None
    for name, value in row._mapping.items():
        set_committed_value(task, name, value)
    set_committed_value(task, "event_seq", seq)


async def _raise_conflict(db: AsyncSession, task: Task) -> NoReturn:
    current = (
        await db.execute(
            select(Task.status, Task.version)
            .where(Task.id == task.id)
            .execution_options(populate_existing=False)
        )
    ).one_or_none()
    if current is None:
        raise AppError("NOT_FOUND", "Task not found", http_status=404)
    raise AppError(
        "VERSION_CONFLICT",
        "The task changed meanwhile; reload it and retry",
        http_status=409,
        details=[{"current_status": current.status, "current_version": current.version}],
    )
