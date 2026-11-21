"""CeleryJobQueue sends by task name; no broker is needed because send_task is replaced."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.job_queue import QUEUE, RUN_TASK, CeleryJobQueue


@pytest.mark.asyncio
async def test_enqueue_task_sends_run_task_by_name_on_ide_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = CeleryJobQueue("redis://localhost:6379/15")
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_send_task(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(queue._celery, "send_task", fake_send_task)
    task_id = uuid.uuid4()

    await queue.enqueue_task(task_id)

    assert RUN_TASK == "app.worker.jobs.run_task"
    assert calls == [((RUN_TASK,), {"args": [str(task_id)], "queue": QUEUE})]
