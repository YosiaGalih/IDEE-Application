"""Celery jobs. Each job is a thin synchronous shell around one async workflow function."""

from __future__ import annotations

import asyncio

from celery import shared_task
from redis.asyncio import Redis

from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.events import RedisEventBus
from app.core.redaction import Redactor
from app.workflow.recovery import recover_expired


@shared_task(name="app.worker.jobs.recovery_sweep", ignore_result=False)
def recovery_sweep() -> dict[str, int]:
    return asyncio.run(run_recovery_sweep())


async def run_recovery_sweep() -> dict[str, int]:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, socket_timeout=2.0, socket_connect_timeout=2.0)
    try:
        result = await recover_expired(
            make_sessionmaker(engine), RedisEventBus(redis), AuditWriter(Redactor(settings.secret_values()))
        )
    finally:
        await redis.aclose()
        await engine.dispose()
    return {"recovered": result.recovered, "failed": result.failed}
