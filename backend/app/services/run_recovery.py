"""Recovery of agent runs and tool calls left ``running`` by a worker that is gone (WP-3.1f2b1;
spec 3.1f2b Keputusan Lead #3-#8, ARCHITECTURE §5.3).

The ``recovery_sweep`` job runs this after task recovery (``app.workflow.recovery.recover_expired``). It is
the second writer of ``agent_runs`` (tests/unit/test_agent_run_writer_guard.py) and closes ``tool_calls`` only
through ``app.tools.gateway.close_abandoned_calls``.

**Predicates**, on the DB clock (``clock_timestamp()``), never the worker's:
- lease_dead: ``tasks.lease_expires_at IS NULL OR tasks.lease_expires_at < clock_timestamp()``
  (``app.workflow.recovery.lease_dead``, an EXISTS on the run's or call's task);
- stale: ``agent_runs.started_at + make_interval(secs => timeout + 60) < clock_timestamp()``, the profile
  outer-joined on ``agent_id``; ``timeout`` = ``limits->'timeout_seconds'`` clamped into 0..3600 when it is a
  JSON number, else 3600 (missing profile, missing key, or a hand-edited non-number: never a failed cast that
  would stop the whole sweep; controller ruling R3).

A run is closed when it is ``running`` and (lease_dead or stale). A tool call is closed when it is ``running``
and (lease_dead, or its run is closed in the same transaction, or its run is no longer ``running``).

**Unit: the task, in one transaction.** The task row is locked first (``lock_task_row``: ``FOR NO KEY
UPDATE SKIP LOCKED``, the gateway's order, task before tool call), then its matching runs and tool calls
(``FOR UPDATE SKIP LOCKED``); every UPDATE is conditional on ``running``. A row that another sweep or the
runtime holds is left to the next sweep, and counted and logged as skipped (``run_recovery_skipped``): a
busy task row, or a candidate run or tool call the lock passed over. A skip is not a failure (a live
transaction may hold the task row for a moment). When recovery closes a run first, the runtime's ``finish``
raises ``AgentRunAlreadyFinished``. The transaction commits, then its events are published.

This module writes ``agent_runs.status`` and so never holds the Task model (tests/unit/
test_status_writer_guard.py); the task side comes from ``app.workflow.recovery``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Final

from sqlalchemy import (
    ColumnElement,
    Integer,
    Numeric,
    Select,
    case,
    cast,
    exists,
    func,
    or_,
    select,
    union,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime import ERROR_TEXTS
from app.core.audit import AuditWriter
from app.core.db import bound_transaction
from app.core.events import EventBus, emit_task_event, publish_pending
from app.core.logging import get_logger
from app.core.money import decimal_str, stored_cost
from app.core.redaction import Redactor
from app.models.agents import AgentProfile, AgentRun
from app.models.tools import ToolCall
from app.services.usage import incomplete_run_totals, run_usage
from app.tools.gateway import close_abandoned_calls
from app.workflow.recovery import lease_dead, lock_task_row

RUN_CLOSE_BOUND_S: Final = 60  # above a run's own close-out (ARCHITECTURE §5.3, ±25 s), plus clock margin
MAX_TIMEOUT_S: Final = 3600  # the registry's ceiling (AgentLimits.timeout_seconds, le=3600)
_CODE: Final = "interrupted"
_INT4_MAX: Final = 2_147_483_647  # duration_ms is int4: an orphan older than ±24.8 days keeps this ceiling

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RunRecoveryResult:
    tasks: int  # candidate tasks this sweep looked at
    runs: int  # agent_runs closed failed/interrupted
    tool_calls: int  # tool_calls closed error/outcome_unknown
    failed: int  # tasks whose transaction failed; the next sweep tries them again
    skipped: int  # busy tasks plus candidate runs/tool calls held by another transaction; not a failure


@dataclass(frozen=True, slots=True)
class _TaskRecovery:
    runs: int
    tool_calls: int
    skipped: int


class RunRecoveryIncomplete(RuntimeError):
    """Some tasks' runs or tool calls could not be recovered; raised so the job is marked failed."""

    def __init__(self, result: RunRecoveryResult) -> None:
        super().__init__(f"{result.failed} of {result.tasks} tasks with abandoned runs were not recovered")
        self.result = result

    def __reduce__(self) -> tuple[type[RunRecoveryIncomplete], tuple[RunRecoveryResult]]:
        return (RunRecoveryIncomplete, (self.result,))  # Celery re-creates task exceptions from their args


def _stale() -> ColumnElement[bool]:
    raw = AgentProfile.limits["timeout_seconds"]
    clamped = func.least(func.greatest(raw.astext.cast(Numeric), 0), MAX_TIMEOUT_S)
    timeout = case((func.jsonb_typeof(raw) == "number", cast(clamped, Integer)), else_=MAX_TIMEOUT_S)
    # make_interval(years, months, weeks, days, hours, mins, secs): positional for make_interval(secs => ...)
    bound = func.make_interval(0, 0, 0, 0, 0, 0, timeout + RUN_CLOSE_BOUND_S)
    return AgentRun.started_at + bound < func.clock_timestamp()


def _closable_runs() -> Select[uuid.UUID, uuid.UUID, str]:
    """``running`` runs whose task lease is dead, or that are stale: (id, task_id, agent_id)."""
    return (
        select(AgentRun.id, AgentRun.task_id, AgentRun.agent_id)
        .outerjoin(AgentProfile, AgentProfile.agent_id == AgentRun.agent_id)
        .where(AgentRun.status == "running", or_(lease_dead(AgentRun.task_id), _stale()))
    )


def _closable_calls(closing: Sequence[uuid.UUID]) -> Select[ToolCall]:
    """``running`` tool calls whose task lease is dead, whose run is in ``closing`` (closed in this
    transaction), or whose run is no longer ``running``."""
    run_ended = exists().where(AgentRun.id == ToolCall.agent_run_id, AgentRun.status != "running")
    return select(ToolCall).where(
        ToolCall.status == "running",
        or_(lease_dead(ToolCall.task_id), ToolCall.agent_run_id.in_(closing), run_ended),
    )


async def recover_runs(
    sessions: async_sessionmaker[AsyncSession], bus: EventBus, audit: AuditWriter
) -> RunRecoveryResult:
    """Close what the predicates select, one task per transaction. A failing task is counted and logged by
    its error type only; the others go on."""
    candidates_query = union(
        _closable_runs().with_only_columns(AgentRun.task_id),
        _closable_calls(()).with_only_columns(ToolCall.task_id),
    )
    async with sessions() as db:
        candidates = (await db.scalars(candidates_query)).all()
    runs = tool_calls = failed = skipped = 0
    for task_id in candidates:
        try:
            done = await _recover_task(sessions, bus, audit, task_id)
        except Exception as exc:
            # Type only: an exception text may carry row values. The next sweep tries this task again.
            failed += 1
            logger.error("run_recovery_failed", task_id=str(task_id), error_type=type(exc).__name__)
            continue
        runs += done.runs
        tool_calls += done.tool_calls
        skipped += done.skipped
    result = RunRecoveryResult(len(candidates), runs, tool_calls, failed, skipped)
    if failed:
        logger.warning("run_recovery_sweep", **asdict(result))
    elif runs or tool_calls or skipped:
        logger.info("run_recovery_sweep", **asdict(result))
    return result


async def _recover_task(
    sessions: async_sessionmaker[AsyncSession], bus: EventBus, audit: AuditWriter, task_id: uuid.UUID
) -> _TaskRecovery:
    async with sessions() as db:
        await bound_transaction(db)
        project_id = await lock_task_row(db, task_id)
        if project_id is None:  # another sweep or a worker holds the task row: the next sweep sees it again
            logger.warning("run_recovery_skipped", task_id=str(task_id), reason="task_locked")
            return _TaskRecovery(0, 0, 1)
        runs = (
            await db.execute(
                _closable_runs()
                .where(AgentRun.task_id == task_id)
                .with_for_update(of=AgentRun, skip_locked=True)
            )
        ).all()
        run_ids = [run.id for run in runs]
        calls = (
            await db.scalars(
                _closable_calls(run_ids)
                .where(ToolCall.task_id == task_id)
                .with_for_update(of=ToolCall, skip_locked=True)
            )
        ).all()
        held_runs, held_calls = await _held(db, task_id, run_ids, [call.id for call in calls])
        closed: list[str] = []
        for run in runs:
            if await _close_run(db, bus, audit.redactor, task_id, run.id, run.agent_id):
                closed.append(str(run.id))
        closed_calls = await close_abandoned_calls(db, audit, calls, project_id=project_id)
        await db.commit()
        await publish_pending(db, bus)
    if held_runs or held_calls:
        logger.warning(
            "run_recovery_skipped",
            task_id=str(task_id),
            reason="rows_locked",
            runs=held_runs,
            tool_calls=held_calls,
        )
    if closed or closed_calls:
        logger.info(
            "agent_runs_recovered", task_id=str(task_id), agent_run_ids=closed, tool_calls=closed_calls
        )
    return _TaskRecovery(len(closed), closed_calls, held_runs + held_calls)


async def _held(
    db: AsyncSession, task_id: uuid.UUID, run_ids: Sequence[uuid.UUID], call_ids: Sequence[uuid.UUID]
) -> tuple[int, int]:
    """The task's runs and tool calls that match the predicates but were not locked (another transaction
    holds them): read in the same transaction, right after the locking reads."""
    held_runs = _closable_runs().where(AgentRun.task_id == task_id, AgentRun.id.not_in(run_ids))
    held_calls = (
        _closable_calls(run_ids)
        .with_only_columns(ToolCall.id)
        .where(ToolCall.task_id == task_id, ToolCall.id.not_in(call_ids))
    )
    runs = await db.scalar(select(func.count()).select_from(held_runs.subquery()))
    calls = await db.scalar(select(func.count()).select_from(held_calls.subquery()))
    return runs or 0, calls or 0


async def _close_run(
    db: AsyncSession,
    bus: EventBus,
    redactor: Redactor,
    task_id: uuid.UUID,
    run_id: uuid.UUID,
    agent_id: str,
) -> bool:
    """The UPDATE of ``AgentRunWriter.finish`` for ``failed``/``interrupted``: usage never complete
    (``incomplete_run_totals``), ``finished_at`` and ``duration_ms`` from the DB clock, ``output`` NULL; then
    ``agent.run_finished`` in finish's envelope. False when the row is no longer ``running``."""
    run_totals = incomplete_run_totals(await run_usage(db, run_id))
    totals = run_totals.totals
    cost = stored_cost(totals.estimated_cost) if totals.estimated_cost is not None else None
    closed = await db.scalar(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == "running")
        .values(
            status="failed",
            error_code=_CODE,
            error=ERROR_TEXTS[_CODE],
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cache_read_tokens=run_totals.cache_read_tokens,
            cache_creation_tokens=run_totals.cache_creation_tokens,
            estimated_cost=cost,
            cost_status=totals.cost_status,
            finished_at=func.now(),
            duration_ms=cast(
                func.least(func.extract("epoch", func.now() - AgentRun.started_at) * 1000, _INT4_MAX), Integer
            ),
        )
        .returning(AgentRun.id)
        .execution_options(synchronize_session=False)
    )
    if closed is None:
        return False
    usage = {
        "input_tokens": totals.input_tokens,
        "output_tokens": totals.output_tokens,
        "estimated_cost": decimal_str(cost) if cost is not None else None,
        "cost_status": totals.cost_status,
    }
    await emit_task_event(
        db,
        bus,
        task_id,
        "agent.run_finished",
        {
            "agent_run_id": str(run_id),
            "agent_id": agent_id,
            "status": "failed",
            "error_code": _CODE,
            "usage": usage,
        },
        redactor=redactor,
    )
    return True
