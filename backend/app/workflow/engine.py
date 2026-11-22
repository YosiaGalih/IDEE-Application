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
    LEASE_SAFETY_SECONDS,
    ClaimResult,
    LeaseGuard,
    LeaseLost,
    claim_lease,
    lease_duration,
    release_lease,
    renew_lease,
    revoke_lease_owner,
)
from app.workflow.states import Actor, TaskStatus, check_error_code, fail_task, transition

ACTOR = Actor("system", "engine")
MAX_PLANNING_ATTEMPTS = 3
# Spec §16.4 #76: a wall-clock bound on every engine transaction, over the server-side lock and statement
# timeouts (a black-holed connection never answers either). Read at call time.
ENGINE_WRITE_TIMEOUT_S = 15
# AG-13 C1: how long a step may keep cleaning up after the delivery-limit cancel. Past it the heartbeat stops,
# the lease lapses and recovery takes the task. Margin (tested in test_celery_app):
# GRACE + LEASE_SECONDS + RECOVERY_SWEEP_SECONDS + ENGINE_WRITE_TIMEOUT_S < DELIVERY_MARGIN_SECONDS.
STEP_CLEANUP_GRACE_S = 300
# A step still cleaning up this long after its cancel is logged (step_cleanup_waiting); quicker ones are not.
CLEANUP_NOTICE_S = 1.0
# Spec §16.4 #74, §16.5a #3: what a task that ran past the delivery limit says. Fixed; it holds no values.
DURATION_EXCEEDED = "duration_exceeded"
DURATION_EXCEEDED_MESSAGE = "The task ran past its delivery time limit"
CLAIMABLE = frozenset({TaskStatus.DRAFT.value, TaskStatus.QUEUED.value})
PIPELINE: Mapping[str, tuple[str, ...]] = {"draft": ("intake", "context", "plan"), "running": ("implement",)}
# Spec §16.4 #75: where a step's result may move a task, per pipeline; narrower than states.ALLOWED. Never to
# completed, cancelled or failed (failed only through StepResult.fail), and interrupt only from running.
# WP-4.1 adds validating to running, 3.2g waiting_approval. Anything else fails the task step_result_invalid.
ALLOWED_STEP_TARGETS: Mapping[str, frozenset[TaskStatus]] = {
    "draft": frozenset({TaskStatus.PLANNED, TaskStatus.NEEDS_CLARIFICATION}),
    "running": frozenset({TaskStatus.INTERRUPTED}),
}

Pipeline = Literal["draft", "running"]  # the keys of PIPELINE; an adapter picks fail or interrupt from it


def _pipeline_of(status: str) -> Pipeline:
    """The pipeline a claim's status runs; never guesses (``_claim`` hands over only these two)."""
    if status == "draft":
        return "draft"
    if status == "running":
        return "running"
    raise ValueError("pipeline must be draft or running")


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
    "duration_exceeded",
]

logger = get_logger(__name__)


@dataclass(frozen=True)
class StepContext:
    task_id: uuid.UUID
    project_id: uuid.UUID
    step: str
    checkpoint: Mapping[str, Any]
    lease: LeaseGuard  # for a write of the step's own (§16.5a #8); read its rules before use
    pipeline: Pipeline  # which pipeline runs the step: draft (planning) or running (§16.6.2 #83)

    def __post_init__(self) -> None:
        if self.pipeline not in ("draft", "running"):
            raise ValueError("pipeline must be draft or running")


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
        if self.kind not in ("done", "fail", "transition", "interrupt"):
            raise ValueError("kind must be done, fail, transition or interrupt")
        if self.to is not None:
            object.__setattr__(self, "to", TaskStatus(self.to))  # a bad string raises ValueError here
        if self.kind == "transition" and self.to is None:
            raise ValueError("a transition needs a target status (to)")
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
    lease_safety_s: float = LEASE_SAFETY_SECONDS
    delivery_limit_s: float = 3600.0  # per delivery, on the loop clock, from the claim (spec §16.4 #74)
    cleanup_grace_s: float = STEP_CLEANUP_GRACE_S  # a step's cleanup after the limit cancel (AG-13 C1)


@dataclass
class EngineOutcome:
    claim: ClaimResult
    ran: list[str] = field(default_factory=list)  # steps started in this delivery
    stopped: Stopped | None = None


class _Claim(NamedTuple):
    result: ClaimResult
    status: str | None  # the status the pipeline runs for; None when nothing is left to run
    todo: tuple[str, ...]


class _DeliveryLimit(Exception):
    """The delivery limit passed while a step ran; the step has been cancelled once and has ended."""


class _StatusChanged(Exception):
    """The task left the status its pipeline runs for; the lease has been released."""


class TaskEngine:
    def __init__(self, ports: WorkflowPorts) -> None:
        lease = lease_duration().total_seconds()
        if not (
            ports.heartbeat_s > 0
            and ports.lease_safety_s > 0
            and ports.heartbeat_s + ports.lease_safety_s < lease
        ):
            raise ValueError("heartbeat_s and lease_safety_s must be > 0 and sum to less than the lease")
        if not ports.delivery_limit_s > 0:
            raise ValueError("delivery_limit_s must be > 0")
        if not ports.cleanup_grace_s > 0:
            raise ValueError("cleanup_grace_s must be > 0")
        self._ports = ports

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        """A session whose first statement bounds lock and statement time, all within ENGINE_WRITE_TIMEOUT_S.

        On expiry the body is cancelled and ``asyncio.timeout`` raises ``TimeoutError`` (an ``Exception``).
        A ``commit_and_publish`` in the body publishes inside the same bound (spec §16.5a #7, F28): a hung
        publish ends the transaction like a hung statement.
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
        # The delivery limit runs from the committed claim (spec §16.4 #74).
        deadline = asyncio.get_running_loop().time() + self._ports.delivery_limit_s
        try:
            outcome.stopped = await self._drive(task_id, claim.status, claim.todo, outcome.ran, deadline)
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

    async def _drive(
        self, task_id: uuid.UUID, status: str, todo: tuple[str, ...], ran: list[str], deadline: float
    ) -> Stopped:
        loop = asyncio.get_running_loop()
        for step in todo:
            if loop.time() >= deadline:  # no step starts: an honest status, no step_finished (§16.5a #2)
                self._log_limit(task_id, None, cancelled=False)
                return await self._stop_before_step(task_id, status)
            # Before _start's renewal, so the heartbeat's H errs early.
            renewed_at = loop.time()
            context = await self._start(task_id, status, step)
            if context is None:
                return "step_unavailable"
            ran.append(step)
            over = loop.time() >= deadline  # past the limit after _start: finish the step honestly
            result = self._limit_result(status)
            if over:
                self._log_limit(task_id, step, cancelled=False)
            else:
                try:
                    result = await self._run_step(
                        task_id, self._ports.steps[step], context, renewed_at, deadline
                    )
                except _DeliveryLimit:
                    over = True
            stopped = await self._finish(task_id, status, step, result)
            if over:
                return DURATION_EXCEEDED
            if stopped is not None:
                return stopped
        async with self._transaction() as db:
            await release_lease(db, task_id, self._ports.worker_id)
            await commit_and_publish(db, self._ports.bus)
        logger.warning("pipeline_exhausted", task_id=str(task_id), status=status)
        return "pipeline_exhausted"

    @staticmethod
    def _log_limit(task_id: uuid.UUID, step: str | None, *, cancelled: bool) -> None:
        """The delivery limit ended the task's run (M1): ids, step and a flag only, never the owner."""
        logger.warning("task_delivery_limit", task_id=str(task_id), step=step, cancelled=cancelled)

    @staticmethod
    def _limit_result(status: str) -> StepResult:
        if status == TaskStatus.RUNNING.value:
            return StepResult.interrupt(DURATION_EXCEEDED)
        return StepResult.fail(DURATION_EXCEEDED, DURATION_EXCEEDED_MESSAGE)

    async def _stop_before_step(self, task_id: uuid.UUID, status: str) -> Stopped:
        """The limit passed between steps: write the honest status, with no step events (none started)."""
        ports = self._ports
        async with self._transaction() as db:
            task = await self._locked(db, task_id, status)
            if status == TaskStatus.RUNNING.value:
                await transition(
                    db, task, TaskStatus.INTERRUPTED, reason=DURATION_EXCEEDED, actor=ACTOR, audit=ports.audit
                )
            else:
                await self._fail(db, task, DURATION_EXCEEDED, DURATION_EXCEEDED_MESSAGE)
            await release_lease(db, task_id, ports.worker_id)
            await commit_and_publish(db, ports.bus)
        return DURATION_EXCEEDED

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
            context = StepContext(
                task_id=task_id,
                project_id=task.project_id,
                step=step,
                checkpoint=task.checkpoint,
                lease=LeaseGuard(task_id, ports.worker_id),
                pipeline=_pipeline_of(status),
            )
            await commit_and_publish(db, ports.bus)
        return context

    async def _run_step(
        self, task_id: uuid.UUID, step: Step, context: StepContext, renewed_at: float, deadline: float
    ) -> StepResult:
        """Run the step beside the heartbeat. At the deadline the step is cancelled ONCE (a second cancel
        would count against a later ``shield``, Python 3.11+) and the heartbeat keeps the lease while it
        cleans up; ``LeaseLost`` wins over the limit (spec §16.5a #3)."""
        loop = asyncio.get_running_loop()
        work = asyncio.ensure_future(step(context))
        beat = asyncio.create_task(self._heartbeat(task_id, renewed_at))
        beat_won = True  # also on an outer cancel: the step's own outcome is then dropped too
        work_cancelled = False
        overrun = False
        try:
            await asyncio.wait(
                (work, beat), timeout=max(deadline - loop.time(), 0), return_when=asyncio.FIRST_COMPLETED
            )
            if not work.done() and not beat.done():
                work_cancelled = True
                self._log_limit(task_id, context.step, cancelled=True)
                work.cancel()
                await asyncio.wait(
                    (work, beat), timeout=self._ports.cleanup_grace_s, return_when=asyncio.FIRST_COMPLETED
                )
                if not work.done() and not beat.done():
                    # AG-13 C1: the step hangs in its cleanup. Only the heartbeat is cancelled (never the step
                    # again): the lease lapses and recovery takes the task, so the slot is not pinned.
                    overrun = True
                    revoke_lease_owner(self._ports.worker_id)  # first: the step's holds stop at once
                    logger.error("step_cleanup_overrun", task_id=str(task_id), step=context.step)
                    beat.cancel()
            beat_won = beat.done()
        except asyncio.CancelledError:
            if not work.done():
                # R-C1: the step may swallow the cancel and is awaited below; its holds and the runtime
                # heartbeat of this owner must not keep renewing the lease meanwhile.
                revoke_lease_owner(self._ports.worker_id)
            raise
        finally:
            if not work_cancelled:
                work.cancel()  # the engine's only cancel of the step on this exit
            beat.cancel()
            held = await self._settle(task_id, context.step, work, beat)
            if (
                (beat_won or work_cancelled)
                and work.done()
                and not work.cancelled()
                and (error := work.exception())
            ):
                # Its type only: the text of a step's error may carry secrets.
                logger.error(
                    "step_error_dropped",
                    task_id=str(task_id),
                    step=context.step,
                    error_type=type(error).__name__,
                )
        if held:  # an outer cancel was held until the step ended; it wins over every outcome below
            raise asyncio.CancelledError
        if overrun:  # before beat.result(): the cancelled heartbeat would raise CancelledError there
            raise LeaseLost("the step overran its cleanup grace; the lease was let go")
        if beat_won:
            beat.result()  # the heartbeat only ends by raising LeaseLost
        if work_cancelled:
            if not work.cancelled() and work.exception() is None:
                # The step absorbed the cancel and handed a result back: the limit wins.
                logger.warning(
                    "step_result_dropped",
                    task_id=str(task_id),
                    step=context.step,
                    kind=(
                        returned.kind
                        if isinstance(returned := work.result(), StepResult)
                        else f"foreign:{type(returned).__name__}"
                    ),
                )
            raise _DeliveryLimit
        return work.result()

    async def _settle(
        self, task_id: uuid.UUID, step: str, work: asyncio.Future[StepResult], beat: asyncio.Task[NoReturn]
    ) -> int:
        """Wait until the step and the heartbeat have ended and return how many outer cancels were held.

        The wait for the step is unbounded by design (it must end before ``_run_step`` returns). Each outer
        ``CancelledError`` is counted and held, never passed on to the step: ``asyncio.wait`` does not cancel
        what it waits for (``gather`` did, a second cancel of the step: LOW-D2). The first one revokes the
        owner (R-C1), so no hold of the step renews the lease while it is awaited. A cleanup that outlasts
        ``CLEANUP_NOTICE_S`` leaves one line, so a hung cleanup is never silent.
        """
        loop = asyncio.get_running_loop()
        notice_at = loop.time() + CLEANUP_NOTICE_S
        noticed = False
        held = 0
        while not (work.done() and beat.done()):
            try:
                await asyncio.wait(
                    (work, beat),
                    timeout=None if noticed else max(notice_at - loop.time(), 0),
                    return_when=asyncio.ALL_COMPLETED,
                )
            except asyncio.CancelledError:
                held += 1
                if not work.done():
                    revoke_lease_owner(self._ports.worker_id)
            if not noticed and not work.done() and loop.time() >= notice_at:
                noticed = True
                logger.warning("step_cleanup_waiting", task_id=str(task_id), step=step)
        for finished in (work, beat):
            if not finished.cancelled():
                finished.exception()  # retrieved here: nobody else reads a dropped error
        return held

    async def _heartbeat(self, task_id: uuid.UUID, last_ok: float) -> NoReturn:
        """Renew the lease every ``heartbeat_s``; give up ``lease_safety_s`` early (R-T3h, §16.5a #5).

        ``H = last_ok + lease - lease_safety_s``, on the loop clock (``timeout_at`` reads it). Each try is
        bound by ``min(attempt + beat, H, attempt + ENGINE_WRITE_TIMEOUT_S)``, so no wait runs past H. The
        engine gives up (``LeaseLost``) when H has passed or when the timeout that fired was H itself, which
        does not depend on a clock reading. ``last_ok`` is taken before its renewal, so the database lease
        cannot have run out before ``last_ok + lease``; the safety also covers the close of a cancelled
        connection (about 2 s).
        """
        loop = asyncio.get_running_loop()
        beat = self._ports.heartbeat_s
        last_error: Exception | None = None  # the give-up names the failure that spent the margin
        while True:
            give_up_at = last_ok + lease_duration().total_seconds() - self._ports.lease_safety_s
            if give_up_at - loop.time() <= 0:
                self._give_up(task_id, last_error)
            await asyncio.sleep(min(beat, give_up_at - loop.time()))
            attempt = loop.time()
            bound = min(attempt + beat, give_up_at, attempt + ENGINE_WRITE_TIMEOUT_S)
            bounded = asyncio.timeout_at(bound)
            try:
                async with bounded:
                    async with self._transaction() as db:
                        await renew_lease(db, task_id, self._ports.worker_id)
                        await commit_and_publish(db, self._ports.bus)
            except LeaseLost:
                raise
            except Exception as exc:
                if loop.time() >= give_up_at or (bounded.expired() and bound >= give_up_at):
                    self._give_up(task_id, exc)
                logger.warning("lease_heartbeat_failed", task_id=str(task_id), error_type=type(exc).__name__)
                last_error = exc
            else:
                last_ok = attempt
                last_error = None

    def _give_up(self, task_id: uuid.UUID, cause: Exception | None) -> NoReturn:
        """``cause`` is None only when no renewal failed before H passed (the step began past its margin)."""
        revoke_lease_owner(self._ports.worker_id)  # first: a step's hold must not renew what we give up
        why = {"error_type": type(cause).__name__} if cause else {"reason": "lease_margin_spent"}
        logger.warning("lease_heartbeat_gave_up", task_id=str(task_id), **why)
        raise LeaseLost(f"the lease of task {task_id} could not be renewed in time") from cause

    async def _finish(self, task_id: uuid.UUID, status: str, step: str, result: StepResult) -> Stopped | None:
        ports = self._ports
        async with self._transaction() as db:
            task = await self._locked(db, task_id, status)
            stopped: Stopped | None = None
            allowed = ALLOWED_STEP_TARGETS.get(status, frozenset())
            # A step may hand back anything: a value that is not a StepResult, or a kind past __post_init__,
            # falls to ``case _`` and fails the task (L2, M5). The type name stands for a foreign value.
            # A foreign value's kind is never read from it, nor from its class name: "foreign:<type>" matches
            # no case, so a lookalike class named ``done`` or ``interrupt`` is refused too (AG-13 INFO-D4).
            kind: str = result.kind if isinstance(result, StepResult) else f"foreign:{type(result).__name__}"
            target = TaskStatus.INTERRUPTED if kind == "interrupt" else getattr(result, "to", None)
            # One savepoint: a refused transition or fail_task also undoes the step's finish (ruling R-T3f).
            async with db.begin_nested():
                match kind:
                    case "done":
                        await self._step_finished(db, task_id, step, "done")
                    case "fail":
                        await self._step_finished(db, task_id, step, "failed")
                        await self._fail(db, task, result.error_code, result.message)
                        stopped = "failed"
                    case "transition" | "interrupt" if target not in allowed:  # before any done write
                        stopped = await self._fail_invalid_result(db, task, status, step, kind, target)
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
                    case _:
                        stopped = await self._fail_invalid_result(db, task, status, step, kind, None)
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
        if kind not in ("transition", "interrupt"):  # not a result the engine knows (L2, M5)
            message = f"Step {step} returned a result the engine does not understand"
        else:
            message = (
                f"Step {step} asked to move a {status} task to {shown or 'no status'}, which it may not do"
            )
        logger.error("step_result_invalid", task_id=str(task.id), step=step, kind=kind, target=shown)
        await self._step_finished(db, task.id, step, "failed")
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
