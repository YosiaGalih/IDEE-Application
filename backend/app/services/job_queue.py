"""Background jobs the API asks for (ARCHITECTURE §4: long operations answer 202 and run in a worker).

``JobQueue`` is the port; services depend on it, never on ``app.worker`` (lint-imports layers). The Celery
adapter sends by task name, so the API process does not import the job code. Tests inject a fake queue and
call the job function where the worker would.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Protocol

from celery import Celery

IMPORT_TASK = "app.worker.platform_jobs.import_archive"
INDEX_TASK = "app.worker.platform_jobs.index_repository"
QUEUE = "ide"  # platform_jobs run in worker-ide (IMPLEMENTATION_DESIGN_V2 §2)
SEND_TIMEOUT_S = 10.0


class JobQueue(Protocol):
    async def enqueue_import(
        self, *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID, filename: str
    ) -> None:
        """Hand the import to a worker; raise if it was not handed over."""
        ...

    async def enqueue_index(
        self, *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID, trigger: str
    ) -> None:
        """Hand an index run to a worker (WP-2.1d); raise if it was not handed over."""
        ...


class CeleryJobQueue:
    def __init__(self, broker_url: str) -> None:
        self._celery = Celery("kodekantor-api", broker=broker_url)
        self._celery.conf.update(
            task_serializer="json",
            accept_content=["json"],
            broker_connection_timeout=2.0,
            # A broker that stops answering mid-publish fails the send instead of holding a thread forever.
            broker_transport_options={"socket_timeout": 5.0, "socket_connect_timeout": 2.0},
            # A broker that is down fails the request in seconds (503), never after kombu's long default.
            task_publish_retry_policy={
                "max_retries": 2,
                "interval_start": 0,
                "interval_step": 0.5,
                "interval_max": 1,
            },
        )

    async def enqueue_import(
        self, *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID, filename: str
    ) -> None:
        kwargs = {
            "project_id": str(project_id),
            "job_id": str(job_id),
            "user_id": str(user_id),
            "filename": filename,  # already cleaned (clean_filename): data for the commit message only
        }
        async with asyncio.timeout(SEND_TIMEOUT_S):
            await asyncio.to_thread(self._celery.send_task, IMPORT_TASK, kwargs=kwargs, queue=QUEUE)

    async def enqueue_index(
        self, *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID, trigger: str
    ) -> None:
        kwargs = {
            "project_id": str(project_id),
            "job_id": str(job_id),
            "user_id": str(user_id),
            "trigger": trigger,
        }
        async with asyncio.timeout(SEND_TIMEOUT_S):
            await asyncio.to_thread(self._celery.send_task, INDEX_TASK, kwargs=kwargs, queue=QUEUE)
