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
from app.workflow.recovery import RecoveryIncomplete, recover_expired

# Shorter than the 30 s beat interval: a hung database must not pin a worker slot (review WP-1.1f).
SWEEP_TIMEOUT_SECONDS = 25.0


@shared_task(name="app.worker.jobs.recovery_sweep")
def recovery_sweep() -> dict[str, int]:
    """Fails (RecoveryIncomplete / TimeoutError) instead of reporting success when tasks stay orphaned."""
    return asyncio.run(run_recovery_sweep())


async def run_recovery_sweep() -> dict[str, int]:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, socket_timeout=2.0, socket_connect_timeout=2.0)
    try:
        async with asyncio.timeout(SWEEP_TIMEOUT_SECONDS):
            result = await recover_expired(
                make_sessionmaker(engine),
                RedisEventBus(redis),
                AuditWriter(Redactor(settings.secret_values())),
            )
    finally:
        await redis.aclose()
        await engine.dispose()
    if result.failed:
        raise RecoveryIncomplete(result)
    return dict(result.__dict__)
