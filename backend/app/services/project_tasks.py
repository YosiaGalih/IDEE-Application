"""Ending a project's open work (WP-INT-04): every task that may still be cancelled is, through the state
machine (CLAUDE.md #4), in the caller's transaction."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditWriter
from app.models.tasks import Task
from app.workflow.states import ALLOWED, TaskStatus, transition
from app.workflow.states import Actor as WorkflowActor

CANCELLABLE = tuple(status.value for status, targets in ALLOWED.items() if TaskStatus.CANCELLED in targets)


async def cancel_open_tasks(
    session: AsyncSession, project_id: uuid.UUID, *, reason: str, actor: WorkflowActor, audit: AuditWriter
) -> int:
    tasks = (
        await session.scalars(
            select(Task).where(Task.project_id == project_id, Task.status.in_(CANCELLABLE)).with_for_update()
        )
    ).all()
    for task in tasks:
        await transition(session, task, TaskStatus.CANCELLED, reason=reason, actor=actor, audit=audit)
    return len(tasks)
