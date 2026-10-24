"""Task state machine (ARCHITECTURE §5.1, ADR-006, ADR-021). The only code allowed to write ``tasks.status``.

``transition`` runs in the caller's transaction and never commits. After committing, the caller runs
``core.events.publish_pending`` so subscribers see the ``task.status_changed`` event.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.audit import AuditWriter
from app.core.errors import AppError
from app.core.events import emit_task_event
from app.core.redaction import Redactor
from app.models.tasks import Task


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


async def transition(
    db: AsyncSession,
    task: Task,
    to: TaskStatus,
    *,
    reason: str | None,
    actor: Actor,
    audit: AuditWriter | None = None,
) -> None:
    source = TaskStatus(task.status)
    target = TaskStatus(to)
    if target not in ALLOWED[source]:
        raise AppError(
            "INVALID_TRANSITION",
            f"A task cannot move from {source.value} to {target.value}",
            http_status=409,
            details=[{"from": source.value, "to": target.value}],
        )

    values: dict[str, Any] = {
        "status": target.value,
        "status_reason": reason,
        "version": Task.version + 1,
        "updated_at": func.now(),
    }
    if target is S.RUNNING:
        values["started_at"] = func.coalesce(Task.started_at, func.now())
    if target in _FINISHED:
        values["completed_at"] = func.now()
    if target is S.QUEUED and source in _NEW_ITERATION_FROM:
        values["iteration"] = Task.iteration + 1
        values["completed_at"] = None

    # Optimistic lock: the row must still be at the version and status this object was read with.
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
    if row is None:
        raise AppError("VERSION_CONFLICT", "The task changed meanwhile; reload it and retry", http_status=409)
    for name, value in row._mapping.items():
        set_committed_value(task, name, value)

    change = {"from": source.value, "to": target.value, "reason": reason}
    await (audit or AuditWriter(Redactor())).write(
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
    await emit_task_event(db, None, task.id, "task.status_changed", change)
