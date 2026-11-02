"""Read-only task counts for other services. Kept apart so modules that write their own ``.status`` (projects)
never import the Task model (tests/unit/test_status_writer_guard.py)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Task
from app.workflow.states import TERMINAL


async def open_task_count(session: AsyncSession, project_id: uuid.UUID) -> int:
    """Tasks of the project that have not reached a terminal status."""
    count = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.project_id == project_id, Task.status.not_in(TERMINAL))
    )
    return count or 0
