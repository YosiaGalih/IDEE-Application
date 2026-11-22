"""Task engine (spec WP-3.2 §14 #40, §14.4a): runs a task's registered steps; it calls no LLM itself.

One delivery:
- claims a ``draft`` or ``queued`` task and, in the claim transaction, moves ``queued`` to ``running`` or
  counts a planning attempt (the fourth fails the task with ``planning_interrupted``);
- runs, in order, each step of ``PIPELINE[status]`` that is not in ``checkpoint.completed_steps``, while a
  background heartbeat renews the lease.

After the claim, each write around a step is a short transaction that first renews the lease and re-reads the
status, so no session is open while a step runs, nothing is written once the lease is lost (``LeaseLost``: the
recovery sweep owns the task), and a status changed from outside only releases the lease.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, NoReturn

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.db import bound_transaction
from app.core.events import EventBus, commit_and_publish, emit_task_event
from app.core.logging import get_logger
from app.models.tasks import Task
from app.workflow.checkpoint import add_completed_step, count_planning_attempt
from app.workflow.lease import (
    HEARTBEAT_SECONDS,
    ClaimResult,
    LeaseLost,
    claim_lease,
    lease_duration,
    release_lease,
    renew_lease,
)
from app.workflow.states import Actor, TaskStatus, check_error_code, fail_task, transition

ACTOR = Actor("system", "engine")
MAX_PLANNING_ATTEMPTS = 3
# Spec §16.4 #76: a wall-clock bound on every engine transaction, over the server-side lock and statement
# timeouts (a black-holed connection never answers either). Read at call time.
ENGINE_WRITE_TIMEOUT_S = 15
CLAIMABLE = frozenset({TaskStatus.DRAFT.value, TaskStatus.QUEUED.value})
PIPELINE: Mapping[str, tuple[str, ...]] = {"draft": ("intake", "context", "plan"), "running": ("implement",)}
# Spec §16.4 #75: where a step's result may move a task, per pipeline; narrower than states.ALLOWED. Never to
# completed, cancelled or failed (failed only through StepResult.fail), and interrupt only from running.
# WP-4.1 adds validating to running, 3.2g waiting_approval. Anything else fails the task step_result_invalid.
ALLOWED_STEP_TARGETS: Mapping[str, frozenset[TaskStatus]] = {
    "draft": frozenset({TaskStatus.PLANNED, TaskStatus.NEEDS_CLARIFICATION}),
    "running": frozenset({TaskStatus.INTERRUPTED}),
}

Stopped = Literal[
    "transitioned",
    "failed",
    "step_unavailable",
    "planning_interrupted",
    "lease_lost",
    "status_changed",
    "pipeline_exhausted",
    "interrupted",
    "abandoned",
    "step_result_invalid",
]

logger = get_logger(__name__)


@dataclass(frozen=True)
class StepContext:
    task_id: uuid.UUID
    project_id: uuid.UUID
    step: str
    checkpoint: Mapping[str, Any]


@dataclass(frozen=True)
class StepResult:
    """What a step asks the engine to write when it ends (spec §16.5):

    - ``done``: the step joins ``completed_steps`` and the pipeline goes on;
    - ``fail(error_code, message)``: the task fails; the step stays undone, so a retry runs it again;
    - ``transition(to, reason)``: the step is done and the task moves to ``to``, which must be in
      ``ALLOWED_STEP_TARGETS`` for the pipeline;
    - ``interrupt(reason)``: ``running -> interrupted`` with the step left undone, so a resume runs it again.

    The ``fail`` code and the ``interrupt`` reason are checked when the result is made (§16.5a #6): a bad one
    raises inside the step, a bug of the step (ruling R-T3c), never a status write.
    """

    kind: Literal["done", "fail", "transition", "interrupt"]
    error_code: str = ""
    message: str = ""
    to: TaskStatus | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "fail":
            check_error_code(self.error_code, "error_code")
        elif self.kind == "interrupt":
            check_error_code(self.reason or "", "reason")

    @classmethod
    def done(cls) -> StepResult:
        return cls("done")

    @classmethod
    def fail(cls, error_code: str, message: str) -> StepResult:
        return cls("fail", error_code=error_code, message=message)

    @classmethod
    def transition(cls, to: TaskStatus, reason: str | None) -> StepResult:
        return cls("transition", to=TaskStatus(to), reason=reason)

    @classmethod
    def interrupt(cls, reason: str) -> StepResult:
        return cls("interrupt", reason=reason)


class StepAbandoned(Exception):
    """Raised by a step that gives up without a result (spec §16.5).

    The engine releases the lease, logs ``step_abandoned`` and writes nothing for the step: no status, event
    or checkpoint. The delivery then returns normally. A ``draft`` task is sent again by the dispatch sweep,
    and that claim counts a planning attempt. A ``running`` task is taken by the recovery sweep
    (``interrupted lease_expired``, N49). ``reason`` is logged, so it follows the ``error_code`` pattern.
    """

    def __init__(self, reason: str) -> None:
        check_error_code(reason, "reason")
        super().__init__(reason)
        self.reason = reason


Step = Callable[[StepContext], Awaitable[StepResult]]


@dataclass
class WorkflowPorts:
    sessions: async_sessionmaker[AsyncSession]
    bus: EventBus
    audit: AuditWriter
    worker_id: str
    max_active: int
    steps: Mapping[str, Step]
    heartbeat_s: float = HEARTBEAT_SECONDS


@dataclass
class EngineOutcome:
    claim: ClaimResult
    ran: list[str] = field(default_factory=list)  # steps started in this delivery
    stopped: Stopped | None = None


class _Claim(NamedTuple):
    result: ClaimResult
    status: str | None  # the status the pipeline runs for; None when nothing is left to run
    todo: tuple[str, ...]


class _StatusChanged(Exception):
    """The task left the status its pipeline runs for; the lease has been released."""


class TaskEngine:
    def __init__(self, ports: WorkflowPorts) -> None:
        self._ports = ports

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        """A session whose first statement bounds lock and statement time, all within ENGINE_WRITE_TIMEOUT_S.

        On expiry the body is cancelled and ``asyncio.timeout`` raises ``TimeoutError`` (an ``Exception``).
        """
        async with asyncio.timeout(ENGINE_WRITE_TIMEOUT_S):
            async with self._ports.sessions() as db:
                await bound_transaction(db)
                yield db

    async def run(self, task_id: uuid.UUID) -> EngineOutcome:
        claim = await self._claim(task_id)
        outcome = EngineOutcome(claim.result)
        if claim.result is not ClaimResult.ACQUIRED:
            return outcome
        if claim.status is None:
            outcome.stopped = "planning_interrupted"
            return outcome
        try:
            outcome.stopped = await self._drive(task_id, claim.status, claim.todo, outcome.ran)
        except LeaseLost:
            logger.warning("task_lease_lost", task_id=str(task_id), worker_id=self._ports.worker_id)
            outcome.stopped = "lease_lost"
        except _StatusChanged:
            outcome.stopped = "status_changed"
        except asyncio.CancelledError:
            step = outcome.ran[-1] if outcome.ran else None
            logger.warning("task_engine_cancelled", task_id=str(task_id), step=step)
            await self._release_after_error(task_id)
            raise
        except StepAbandoned as abandoned:
            # Spec §16.5: nothing is written for the step; the sweeps take the task over (see StepAbandoned).
            step = outcome.ran[-1] if outcome.ran else None
            logger.warning("step_abandoned", task_id=str(task_id), step=step, reason=abandoned.reason)
            await self._release_after_error(task_id)
            outcome.stopped = "abandoned"
        except Exception as exc:
            # Spec #43: logged with the task id, then raised again. The type only: the text may carry secrets.
            logger.error(
                "task_engine_failed",
                task_id=str(task_id),
                worker_id=self._ports.worker_id,
                step=outcome.ran[-1] if outcome.ran else None,
                error_type=type(exc).__name__,
            )
            await self._release_after_error(task_id)
            raise
        return outcome

    async def _claim(self, task_id: uuid.UUID) -> _Claim:
        ports = self._ports
        async with self._transaction() as db:
            result = await claim_lease(
                db, task_id, ports.worker_id, max_active=ports.max_active, claimable=CLAIMABLE
            )
            if result is not ClaimResult.ACQUIRED:
                return _Claim(result, None, ())
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None  # the claim just locked the row
            completed = task.checkpoint.get("completed_steps", [])
            status: str | None
            if task.status == TaskStatus.QUEUED:
                await transition(db, task, TaskStatus.RUNNING, reason=None, actor=ACTOR, audit=ports.audit)
                status = TaskStatus.RUNNING.value
            elif await count_planning_attempt(db, task_id) > MAX_PLANNING_ATTEMPTS:
                interrupted = f"Planning was interrupted {MAX_PLANNING_ATTEMPTS} times without finishing"
                await self._fail(db, task, "planning_interrupted", interrupted)
                await release_lease(db, task_id, ports.worker_id)
                status = None
            else:
                status = TaskStatus.DRAFT.value
            await commit_and_publish(db, ports.bus)
        todo = tuple(step for step in PIPELINE.get(status or "", ()) if step not in completed)
        return _Claim(result, status, todo)

    async def _drive(self, task_id: uuid.UUID, status: str, todo: tuple[str, ...], ran: list[str]) -> Stopped:
        for step in todo:
            renewed_at = time.monotonic()  # before _start's renewal, so the heartbeat deadline errs early
            context = await self._start(task_id, status, step)
            if context is None:
                return "step_unavailable"
            ran.append(step)
            result = await self._run_step(task_id, self._ports.steps[step], context, renewed_at)
            stopped = await self._finish(task_id, status, step, result)
            if stopped is not None:
                return stopped
        async with self._transaction() as db:
            await release_lease(db, task_id, self._ports.worker_id)
            await commit_and_publish(db, self._ports.bus)
        logger.warning("pipeline_exhausted", task_id=str(task_id), status=status)
        return "pipeline_exhausted"

    async def _locked(self, db: AsyncSession, task_id: uuid.UUID, status: str) -> Task:
        """Renew the lease (raises ``LeaseLost``) and load the task, which must still be in ``status``.

        The renewal locks the row until the caller commits, so the status read stays true for its writes.
        """
        await renew_lease(db, task_id, self._ports.worker_id)
        task = await db.scalar(select(Task).where(Task.id == task_id))
        assert task is not None  # the renewal found the row
        if task.status != status:
            await release_lease(db, task_id, self._ports.worker_id)
            await commit_and_publish(db, self._ports.bus)
            logger.info(
                "task_status_changed_outside", task_id=str(task_id), expected=status, found=task.status
            )
            raise _StatusChanged
        return task

    async def _start(self, task_id: uuid.UUID, status: str, step: str) -> StepContext | None:
        ports = self._ports
        async with self._transaction() as db:
            task = await self._locked(db, task_id, status)
            if step not in ports.steps:
                await self._fail(db, task, "step_unavailable", f"No step is registered as {step}")
                await release_lease(db, task_id, ports.worker_id)
                await commit_and_publish(db, ports.bus)
                return None
            await db.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(current_step=step)
                .execution_options(synchronize_session=False)
            )
            await emit_task_event(
                db, None, task_id, "task.step_started", {"step": step}, redactor=ports.audit.redactor
            )
            context = StepContext(task_id, task.project_id, step, task.checkpoint)
            await commit_and_publish(db, ports.bus)
        return context

    async def _run_step(
        self, task_id: uuid.UUID, step: Step, context: StepContext, renewed_at: float
    ) -> StepResult:
        work = asyncio.ensure_future(step(context))
        beat = asyncio.create_task(self._heartbeat(task_id, renewed_at))
        beat_won = True  # also on an outer cancel: the step's own outcome is then dropped too
        try:
            await asyncio.wait((work, beat), return_when=asyncio.FIRST_COMPLETED)
            beat_won = beat.done()
        finally:
            work.cancel()
            beat.cancel()
            await asyncio.gather(work, beat, return_exceptions=True)
            if beat_won and work.done() and not work.cancelled() and (error := work.exception()):
                # Its type only: the text of a step's error may carry secrets.
                logger.error(
                    "step_error_dropped",
                    task_id=str(task_id),
                    step=context.step,
                    error_type=type(error).__name__,
                )
        if beat_won:
            beat.result()  # the heartbeat only ends by raising LeaseLost
        return work.result()

    async def _heartbeat(self, task_id: uuid.UUID, last_ok: float) -> NoReturn:
        """Renew the lease every ``heartbeat_s`` (ruling R-T3h).

        A failed or hung renewal is retried until ``last_ok + lease - heartbeat_s``; past it, ``LeaseLost``.
        No wait runs past that deadline, so the last try ends (``asyncio.timeout``) by ``last_ok + lease``.
        ``last_ok`` is taken before its renewal, so the database lease cannot have run out before then.
        """
        beat = self._ports.heartbeat_s
        while True:
            deadline = last_ok + lease_duration().total_seconds() - beat
            await asyncio.sleep(min(beat, max(0.0, deadline - time.monotonic())))
            attempt = time.monotonic()
            try:
                async with asyncio.timeout(beat):
                    async with self._transaction() as db:
                        await renew_lease(db, task_id, self._ports.worker_id)
                        await commit_and_publish(db, self._ports.bus)
            except LeaseLost:
                raise
            except Exception as exc:
                if time.monotonic() >= deadline:
                    logger.warning(
                        "lease_heartbeat_gave_up", task_id=str(task_id), error_type=type(exc).__name__
                    )
                    raise LeaseLost(f"the lease of task {task_id} could not be renewed in time") from exc
                logger.warning("lease_heartbeat_failed", task_id=str(task_id), error_type=type(exc).__name__)
            else:
                last_ok = attempt

    async def _finish(self, task_id: uuid.UUID, status: str, step: str, result: StepResult) -> Stopped | None:
        ports = self._ports
        async with self._transaction() as db:
            task = await self._locked(db, task_id, status)
            stopped: Stopped | None = None
            target = TaskStatus.INTERRUPTED if result.kind == "interrupt" else result.to
            allowed = ALLOWED_STEP_TARGETS.get(status, frozenset())
            # One savepoint: a refused transition or fail_task also undoes the step's finish (ruling R-T3f).
            async with db.begin_nested():
                match result.kind:
                    case "done":
                        await self._step_finished(db, task_id, step, "done")
                    case "fail":
                        await self._step_finished(db, task_id, step, "failed")
                        await self._fail(db, task, result.error_code, result.message)
                        stopped = "failed"
                    case "transition" | "interrupt" if target not in allowed:  # before any done write
                        stopped = await self._fail_invalid_result(db, task, status, step, result.kind, target)
                    case "transition":
                        assert result.to is not None
                        await self._step_finished(db, task_id, step, "done")
                        await transition(
                            db, task, result.to, reason=result.reason, actor=ACTOR, audit=ports.audit
                        )
                        stopped = "transitioned"
                    case "interrupt":
                        # Left out of completed_steps: the interrupted step runs again on resume.
                        await self._step_finished(db, task_id, step, "interrupted")
                        await transition(
                            db,
                            task,
                            TaskStatus.INTERRUPTED,
                            reason=result.reason,
                            actor=ACTOR,
                            audit=ports.audit,
                        )
                        stopped = "interrupted"
            if stopped is not None:
                await release_lease(db, task_id, ports.worker_id)
            await commit_and_publish(db, ports.bus)
        return stopped

    async def _fail_invalid_result(
        self, db: AsyncSession, task: Task, status: str, step: str, kind: str, target: TaskStatus | None
    ) -> Stopped:
        """Spec §16.4 #75: a target outside ``ALLOWED_STEP_TARGETS[status]`` fails the task honestly and
        terminally, the step unfinished. Never raised: a raise would spend a planning attempt and send the
        task again, to the same refused result."""
        shown = target.value if target is not None else None
        logger.error("step_result_invalid", task_id=str(task.id), step=step, kind=kind, target=shown)
        await self._step_finished(db, task.id, step, "failed")
        message = f"Step {step} asked to move a {status} task to {shown or 'no status'}, which it may not do"
        await self._fail(db, task, "step_result_invalid", message)
        return "step_result_invalid"

    async def _fail(self, db: AsyncSession, task: Task, error_code: str, message: str) -> None:
        await fail_task(
            db, task, error_code=error_code, message=message, actor=ACTOR, audit=self._ports.audit
        )

    async def _step_finished(
        self,
        db: AsyncSession,
        task_id: uuid.UUID,
        step: str,
        outcome: Literal["done", "failed", "interrupted"],
    ) -> None:
        """Emit ``task.step_finished``. Only a done step joins ``completed_steps``: a failed or interrupted
        one runs again on retry or resume."""
        if outcome == "done":
            await add_completed_step(db, task_id, step)
        await emit_task_event(
            db,
            None,
            task_id,
            "task.step_finished",
            {"step": step, "status": outcome},
            redactor=self._ports.audit.redactor,
        )

    async def _release_after_error(self, task_id: uuid.UUID) -> None:
        """Best effort: a failed release is logged and the original error goes on."""
        try:
            async with self._transaction() as db:
                await release_lease(db, task_id, self._ports.worker_id)
                await commit_and_publish(db, self._ports.bus)
        except Exception as exc:
            logger.error("lease_release_failed", task_id=str(task_id), error_type=type(exc).__name__)
