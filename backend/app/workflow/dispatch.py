"""Port the engine uses to hand a task to a worker (the Celery adapter lives in app.services.job_queue)."""

from __future__ import annotations

import uuid
from typing import Protocol


class Dispatcher(Protocol):
    async def enqueue_task(self, task_id: uuid.UUID) -> None:
        """Hand the task to a worker; raise if it was not handed over."""
        ...
