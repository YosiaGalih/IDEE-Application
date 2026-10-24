"""Celery application (ARCHITECTURE §2): ``celery -A app.worker.celery_app worker`` / ``... beat``.

- **Broker only.** Redis is the broker; there is no result backend, because PostgreSQL is the record
  (CLAUDE.md #4).
- **JSON only.** Pickle is never accepted.
- **At-least-once delivery.** ``acks_late`` with ``reject_on_worker_lost`` redelivers a task when its
  worker dies; the task lease (``workflow.lease``) makes a duplicate delivery harmless (NFR-05).
"""

from __future__ import annotations

import asyncio
from typing import Any

from celery import Celery
from celery.signals import worker_ready

from app.core.config import Settings, load_settings

RECOVERY_SWEEP_SECONDS = 30.0  # ARCHITECTURE §5.3


def create_celery(settings: Settings) -> Celery:
    app = Celery("kodekantor", broker=settings.redis_url, include=["app.worker.jobs"])
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_default_queue="ide",
        task_always_eager=settings.celery_task_always_eager,
        broker_connection_retry_on_startup=True,
        timezone="UTC",
        beat_schedule={
            "recovery-sweep": {"task": "app.worker.jobs.recovery_sweep", "schedule": RECOVERY_SWEEP_SECONDS},
        },
    )
    return app


@worker_ready.connect
def _recover_on_start(**_: Any) -> None:
    # ARCHITECTURE §5.3: the sweep also runs when a worker starts, before it takes new work.
    from app.worker.jobs import run_recovery_sweep

    asyncio.run(run_recovery_sweep())


_app: Celery | None = None


def __getattr__(name: str) -> Celery:
    # Built on first use so importing this module does not require a configured environment.
    global _app
    if name in {"celery_app", "app", "celery"}:
        if _app is None:
            _app = create_celery(load_settings())
        return _app
    raise AttributeError(name)
