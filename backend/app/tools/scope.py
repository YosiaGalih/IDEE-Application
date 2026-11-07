"""Where a tool call applies: the task row it locks and the project it must belong to (spec WP-3.1d Goal 3,
step 4). Read-only: nothing here writes a status (CLAUDE.md #4)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import Project
from app.models.tasks import Task


@dataclass(frozen=True, slots=True)
class TaskScope:
    project_id: uuid.UUID
    task_status: str
    project_status: str | None


async def lock_task_scope(session: AsyncSession, task_id: uuid.UUID) -> TaskScope | None:
    """Lock the task row ``FOR NO KEY UPDATE`` (before any approval row, the order the ``tool_r3`` handler
    takes too) and read its project's status. ``None``: the task does not exist."""
    task = await session.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if task is None:
        return None
    project_status = await session.scalar(select(Project.status).where(Project.id == task.project_id))
    return TaskScope(task.project_id, task.status, project_status)
