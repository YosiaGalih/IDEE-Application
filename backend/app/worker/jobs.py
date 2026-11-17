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
from app.core.logging import get_logger
from app.core.redaction import Redactor
from app.services.run_recovery import RunRecoveryIncomplete, RunRecoveryResult, recover_runs
from app.workflow.recovery import RecoveryIncomplete, RecoveryResult, recover_expired

# Shorter than the 30 s beat interval: a hung database must not pin a worker slot (review WP-1.1f). Each of
# the two phases has half of it (ruling R8), so a slow task phase never eats the run phase's time.
SWEEP_TIMEOUT_SECONDS = 25.0

logger = get_logger(__name__)


@shared_task(name="app.worker.jobs.recovery_sweep")
def recovery_sweep() -> dict[str, int]:
    """Fails (RecoveryIncomplete / TimeoutError) instead of reporting success when tasks stay orphaned."""
    return asyncio.run(run_recovery_sweep())


async def sweep_recoveries() -> tuple[RecoveryResult, RunRecoveryResult]:
    """Task recovery, then run recovery, each within half of ``SWEEP_TIMEOUT_SECONDS`` (ruling R8). An
    exception or a timeout of the task phase is held and logged, and run recovery still runs: its predicates
    do not depend on task recovery (spec 3.1f2b §6.2). Then one exception is raised, in this order: the task
    phase's, ``RecoveryIncomplete`` (tasks failed), the run phase's. ``RunRecoveryIncomplete`` comes last,
    from the results (``run_recovery_sweep``); the CLI prints the counts when both phases returned."""
    settings = load_settings()
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, socket_timeout=2.0, socket_connect_timeout=2.0)
    phase_timeout = SWEEP_TIMEOUT_SECONDS / 2
    tasks: RecoveryResult | None = None
    runs: RunRecoveryResult | None = None
    tasks_error: Exception | None = None
    runs_error: Exception | None = None
    try:
        sessions = make_sessionmaker(engine)
        bus = RedisEventBus(redis)
        audit = AuditWriter(Redactor(settings.secret_values()))
        try:
            async with asyncio.timeout(phase_timeout):
                tasks = await recover_expired(sessions, bus, audit)
        except Exception as error:  # held: the run phase still runs
            tasks_error = error
            logger.error("recovery_phase_failed", phase="tasks", error_type=type(error).__name__)
        try:
            async with asyncio.timeout(phase_timeout):
                runs = await recover_runs(sessions, bus, audit)
        except Exception as error:
            runs_error = error
            logger.error("recovery_phase_failed", phase="runs", error_type=type(error).__name__)
    finally:
        await redis.aclose()
        await engine.dispose()
    if tasks_error is not None:
        raise tasks_error
    assert tasks is not None
    if runs_error is not None:
        if tasks.failed:
            raise RecoveryIncomplete(tasks) from runs_error
        raise runs_error
    assert runs is not None
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
        "runs_skipped": runs.skipped,
    }
