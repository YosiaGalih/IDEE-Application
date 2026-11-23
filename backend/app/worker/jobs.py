"""Celery jobs. Each job is a thin synchronous shell around one async workflow function."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict
from typing import Final

from celery import shared_task
from redis.asyncio import Redis

from app.agents.runtime import CLOSE_TIMEOUT_S
from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.events import RedisEventBus
from app.core.logging import get_logger
from app.core.redaction import Redactor
from app.services.job_queue import CeleryJobQueue
from app.services.orchestrator import build_orchestrator
from app.services.run_recovery import RunRecoveryIncomplete, RunRecoveryResult, recover_runs
from app.worker.identity import delivery_owner
from app.worker.pricing import worker_pricing
from app.worker.registry import worker_registry
from app.workflow.dispatch import Dispatcher, DispatchIncomplete, DispatchResult, redispatch_orphans
from app.workflow.engine import EngineOutcome
from app.workflow.lease import ClaimResult
from app.workflow.recovery import RecoveryIncomplete, RecoveryResult, recover_expired

# Shorter than the 30 s beat interval: a hung database must not pin a worker slot (review WP-1.1f). Each of
# the two phases has half of it (ruling R8), so a slow task phase never eats the run phase's time.
SWEEP_TIMEOUT_SECONDS = 25.0
DISPATCH_SWEEP_TIMEOUT_SECONDS = 25.0
_MAX_LOGGED_ID = 80
# What a delivery may wait for its own leftover tasks before it disposes the engine: ``_record_end`` of the
# runtime is two writes and one ``fail``, each bounded by ``CLOSE_TIMEOUT_S``. Read at call time.
RUNTIME_DRAIN_S: Final = 3 * CLOSE_TIMEOUT_S

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


class RunTaskFailed(Exception):
    """``run_task`` failed with an unexpected error. Carries the error type only: Celery logs a failed job's
    exception text and chained traceback, and the engine's own error may hold a secret (it logged already)."""

    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type

    def __reduce__(self) -> tuple[type[RunTaskFailed], tuple[str]]:
        return (RunTaskFailed, (self.error_type,))  # Celery re-creates task exceptions from their args


@shared_task(name="app.worker.jobs.run_task")
def run_task(task_id: str) -> dict[str, object]:
    """One delivery of the task engine. Engine exceptions propagate to Celery (ruling R-T3c)."""
    try:
        parsed = uuid.UUID(task_id)
    except (ValueError, AttributeError, TypeError):
        # Not retried: redelivering an unparseable id would only poison the queue (ruling R-T5a).
        logger.error("run_task_invalid_id", value=repr(task_id)[:_MAX_LOGGED_ID])
        return {"status": "invalid_task_id"}
    try:
        outcome = asyncio.run(execute_task(parsed))
    except Exception as exc:
        # Covers setup and claim failures too, which the engine never logs; a step crash is logged twice.
        logger.error("run_task_failed", task_id=str(parsed), error_type=type(exc).__name__)
        raise RunTaskFailed(type(exc).__name__) from None
    if outcome.claim in (ClaimResult.AT_CAPACITY, ClaimResult.BUSY):
        # No retry or countdown: the dispatch sweep sends the task again after its grace period.
        logger.info("task_claim_deferred", task_id=str(parsed), reason=outcome.claim.value)
    elif outcome.claim is ClaimResult.NOT_FOUND:
        logger.warning("task_not_found", task_id=str(parsed), reason=outcome.claim.value)
    elif outcome.claim in (ClaimResult.HELD, ClaimResult.NOT_CLAIMABLE):
        logger.info("task_claim_skipped", task_id=str(parsed), reason=outcome.claim.value)
    return {
        "task_id": str(parsed),
        "claim": outcome.claim.value,
        "ran": list(outcome.ran),
        "stopped": outcome.stopped,
    }


async def execute_task(task_id: uuid.UUID) -> EngineOutcome:
    """One delivery. The registry and the pricing are required on every delivery, also while no agent step
    is registered: a worker without them fails here, before any connection or claim, not on its first step."""
    settings = load_settings()
    registry = worker_registry()  # RegistryNotLoaded: before any connection or claim (deliberate, see above)
    pricing = worker_pricing()  # PricingNotLoaded: likewise
    before = set(asyncio.all_tasks())
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, socket_timeout=2.0, socket_connect_timeout=2.0)
    try:
        orchestrator = build_orchestrator(
            sessions=make_sessionmaker(engine),
            bus=RedisEventBus(redis),
            audit=AuditWriter(Redactor(settings.secret_values())),
            settings=settings,
            worker_id=delivery_owner(),
            registry=registry,
            pricing=pricing,
        )
        return await orchestrator.run(task_id)
    finally:
        try:
            await _drain(task_id, before)
        finally:
            try:
                await redis.aclose()
            finally:
                await engine.dispose()  # also when the Redis close raises


def _task_name(task: asyncio.Task[object]) -> str:
    """The qualified name of what a task runs (a code name, never a value), else the task's own name."""
    coro = task.get_coro()
    name = getattr(coro, "__qualname__", None)
    return name if isinstance(name, str) else task.get_name()


async def _drain(task_id: uuid.UUID, before: set[asyncio.Task[object]]) -> None:
    """Waits for the tasks this delivery created (a run's close, for one) before the engine is disposed.
    Never raises (BC-17). A finished leftover that failed has its exception retrieved here, so asyncio does
    not log it later with its text and traceback, outside redaction: it is logged by task and error type. A
    task that outlives the wait is logged by name and left to the loop."""
    current = asyncio.current_task()
    left = {t for t in asyncio.all_tasks() if t not in before and t is not current}
    if not left:
        return
    done, pending = await asyncio.wait(left, timeout=RUNTIME_DRAIN_S)
    for task in sorted(done, key=_task_name):
        if task.cancelled():
            continue
        error = task.exception()
        if error is not None:
            logger.warning(
                "delivery_task_failed",
                task_id=str(task_id),
                task=_task_name(task),
                error_type=type(error).__name__,
            )
    if pending:
        logger.warning(
            "delivery_tasks_abandoned",
            task_id=str(task_id),
            count=len(pending),
            tasks=sorted(_task_name(t) for t in pending),
        )


@shared_task(name="app.worker.jobs.dispatch_sweep")
def dispatch_sweep() -> dict[str, int]:
    """Separate from ``recovery_sweep``: an error in one never blocks the other. Fails (DispatchIncomplete)
    instead of reporting success when an enqueue failed."""
    return asyncio.run(run_dispatch_sweep())


async def run_dispatch_sweep() -> dict[str, int]:
    result = await sweep_dispatch()
    if result.failed:
        raise DispatchIncomplete(result)
    return asdict(result)


async def sweep_dispatch(*, dispatcher: Dispatcher | None = None) -> DispatchResult:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    try:
        if dispatcher is None:
            dispatcher = CeleryJobQueue(settings.redis_url)
        async with asyncio.timeout(DISPATCH_SWEEP_TIMEOUT_SECONDS):
            return await redispatch_orphans(
                make_sessionmaker(engine), dispatcher, max_active=settings.max_active_tasks
            )
    finally:
        await engine.dispose()
