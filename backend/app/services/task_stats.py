"""Read-only task counts for other services. Kept apart so modules that write their own ``.status`` (projects)
never import the Task model (tests/unit/test_status_writer_guard.py)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TASK_STATUSES
from app.models.tasks import Task
from app.workflow.states import FINISHED

# "Open" for a person: work not finished. Not workflow.states.TERMINAL, which only lists statuses with no way
# out (completed and failed can be reopened, yet they are done). The dashboard counts completed and failed
# apart (API_CONTRACTS §3 metrics).
CLOSED_TASK_STATUSES = frozenset(status.value for status in FINISHED)
OPEN_TASK_STATUSES = frozenset(TASK_STATUSES) - CLOSED_TASK_STATUSES


async def open_task_count(session: AsyncSession, project_id: uuid.UUID) -> int:
    """Tasks of the project that are not finished (OPEN_TASK_STATUSES)."""
    count = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.project_id == project_id, Task.status.in_(OPEN_TASK_STATUSES))
    )
    return count or 0
