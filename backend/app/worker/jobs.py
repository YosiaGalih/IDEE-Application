"""Celery jobs. Each job is a thin synchronous shell around one async workflow function."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from celery import shared_task
from redis.asyncio import Redis

from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.events import RedisEventBus
from app.core.redaction import Redactor
from app.services.run_recovery import RunRecoveryIncomplete, RunRecoveryResult, recover_runs
from app.workflow.recovery import RecoveryIncomplete, RecoveryResult, recover_expired

# Shorter than the 30 s beat interval: a hung database must not pin a worker slot (review WP-1.1f).
SWEEP_TIMEOUT_SECONDS = 25.0


@shared_task(name="app.worker.jobs.recovery_sweep")
def recovery_sweep() -> dict[str, int]:
    """Fails (RecoveryIncomplete / TimeoutError) instead of reporting success when tasks stay orphaned."""
    return asyncio.run(run_recovery_sweep())


async def sweep_recoveries() -> tuple[RecoveryResult, RunRecoveryResult]:
    """Task recovery, then run recovery, both inside one timeout. Neither failure starves the other."""
    settings = load_settings()
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, socket_timeout=2.0, socket_connect_timeout=2.0)
    try:
        async with asyncio.timeout(SWEEP_TIMEOUT_SECONDS):
            sessions = make_sessionmaker(engine)
            bus = RedisEventBus(redis)
            audit = AuditWriter(Redactor(settings.secret_values()))
            tasks = await recover_expired(sessions, bus, audit)
            runs = await recover_runs(sessions, bus, audit)
    finally:
        await redis.aclose()
        await engine.dispose()
    return tasks, runs


async def run_recovery_sweep() -> dict[str, int]:
    tasks, runs = await sweep_recoveries()
    if tasks.failed:
        raise RecoveryIncomplete(tasks)
    if runs.failed:
        raise RunRecoveryIncomplete(runs)
    return recovery_counts(tasks, runs)


def recovery_counts(tasks: RecoveryResult, runs: RunRecoveryResult) -> dict[str, int]:
    return {
        **asdict(tasks),
        "runs_recovered": runs.runs,
        "tool_calls_closed": runs.tool_calls,
        "runs_failed": runs.failed,
    }
