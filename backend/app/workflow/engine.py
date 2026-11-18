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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, NoReturn

from sqlalchemy import ColumnElement, Integer, Text, func, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.events import EventBus, commit_and_publish, emit_task_event
from app.core.logging import get_logger
from app.models.tasks import Task
from app.workflow.lease import (
    HEARTBEAT_SECONDS,
    ClaimResult,
    LeaseLost,
    claim_lease,
    release_lease,
    renew_lease,
)
from app.workflow.states import Actor, TaskStatus, fail_task, transition

ACTOR = Actor("system", "engine")
MAX_PLANNING_ATTEMPTS = 3
CLAIMABLE = frozenset({TaskStatus.DRAFT.value, TaskStatus.QUEUED.value})
PIPELINE: Mapping[str, tuple[str, ...]] = {"draft": ("intake", "context", "plan"), "running": ("implement",)}

Stopped = Literal[
    "transitioned",
    "failed",
    "step_unavailable",
    "planning_interrupted",
    "lease_lost",
    "status_changed",
    "pipeline_exhausted",
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
    kind: Literal["done", "fail", "transition"]
    error_code: str = ""
    message: str = ""
    to: TaskStatus | None = None
    reason: str | None = None

    @classmethod
    def done(cls) -> StepResult:
        return cls("done")

    @classmethod
    def fail(cls, error_code: str, message: str) -> StepResult:
        return cls("fail", error_code=error_code, message=message)

    @classmethod
    def transition(cls, to: TaskStatus, reason: str | None) -> StepResult:
        return cls("transition", to=TaskStatus(to), reason=reason)


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


def _path(key: str) -> ColumnElement[Any]:
    return literal([key], ARRAY(Text))


class TaskEngine:
    def __init__(self, ports: WorkflowPorts) -> None:
        self._ports = ports

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
        except (Exception, asyncio.CancelledError):
            await self._release_after_error(task_id)
            raise
        return outcome

    async def _claim(self, task_id: uuid.UUID) -> _Claim:
        ports = self._ports
        async with ports.sessions() as db:
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
            elif await self._count_planning_attempt(db, task_id) > MAX_PLANNING_ATTEMPTS:
                await fail_task(
                    db,
                    task,
                    error_code="planning_interrupted",
                    message=f"Planning was interrupted {MAX_PLANNING_ATTEMPTS} times without finishing",
                    actor=ACTOR,
                    audit=ports.audit,
                )
                await release_lease(db, task_id, ports.worker_id)
                status = None
            else:
                status = TaskStatus.DRAFT.value
            await commit_and_publish(db, ports.bus)
        todo = tuple(step for step in PIPELINE.get(status or "", ()) if step not in completed)
        return _Claim(result, status, todo)

    async def _count_planning_attempt(self, db: AsyncSession, task_id: uuid.UUID) -> int:
        attempts = func.jsonb_extract_path_text(Task.checkpoint, "dispatch", "planning_attempts").cast(
            Integer
        )
        # jsonb_set does not create a missing parent: build dispatch first (drafts made before 3.2d lack it).
        dispatch = func.coalesce(Task.checkpoint["dispatch"], literal({}, JSONB)).op("||")(
            func.jsonb_build_object(literal("planning_attempts", Text), func.coalesce(attempts, 0) + 1)
        )
        counted = await db.scalar(
            update(Task)
            .where(Task.id == task_id)
            .values(checkpoint=func.jsonb_set(Task.checkpoint, _path("dispatch"), dispatch))
            .returning(attempts)
            .execution_options(synchronize_session=False)
        )
        assert counted is not None
        return counted

    async def _drive(self, task_id: uuid.UUID, status: str, todo: tuple[str, ...], ran: list[str]) -> Stopped:
        for step in todo:
            context = await self._start(task_id, status, step)
            if context is None:
                return "step_unavailable"
            ran.append(step)
            result = await self._run_step(task_id, self._ports.steps[step], context)
            stopped = await self._finish(task_id, status, step, result)
            if stopped is not None:
                return stopped
        async with self._ports.sessions() as db:
            await release_lease(db, task_id, self._ports.worker_id)
            await db.commit()
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
            await db.commit()
            logger.info(
                "task_status_changed_outside", task_id=str(task_id), expected=status, found=task.status
            )
            raise _StatusChanged
        return task

    async def _start(self, task_id: uuid.UUID, status: str, step: str) -> StepContext | None:
        ports = self._ports
        async with ports.sessions() as db:
            task = await self._locked(db, task_id, status)
            if step not in ports.steps:
                await fail_task(
                    db,
                    task,
                    error_code="step_unavailable",
                    message=f"No step is registered as {step}",
                    actor=ACTOR,
                    audit=ports.audit,
                )
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

    async def _run_step(self, task_id: uuid.UUID, step: Step, context: StepContext) -> StepResult:
        work = asyncio.ensure_future(step(context))
        beat = asyncio.create_task(self._heartbeat(task_id))
        try:
            await asyncio.wait((work, beat), return_when=asyncio.FIRST_COMPLETED)
            if beat.done():
                beat.result()  # the heartbeat only ends by raising, LeaseLost above all
            return work.result()
        finally:
            work.cancel()
            beat.cancel()
            await asyncio.gather(work, beat, return_exceptions=True)

    async def _heartbeat(self, task_id: uuid.UUID) -> NoReturn:
        while True:
            await asyncio.sleep(self._ports.heartbeat_s)
            try:
                async with self._ports.sessions() as db:
                    await renew_lease(db, task_id, self._ports.worker_id)
                    await db.commit()
            except LeaseLost:
                raise
            except Exception:
                # A missed beat is survivable: the lease outlasts several, and a real loss raises LeaseLost.
                logger.warning("lease_heartbeat_failed", task_id=str(task_id), exc_info=True)

    async def _finish(self, task_id: uuid.UUID, status: str, step: str, result: StepResult) -> Stopped | None:
        ports = self._ports
        async with ports.sessions() as db:
            task = await self._locked(db, task_id, status)
            stopped: Stopped | None = None
            # One savepoint: a refused transition or fail_task also undoes the step's finish (ruling R-T3f).
            async with db.begin_nested():
                match result.kind:
                    case "done":
                        await self._step_finished(db, task_id, step, "done")
                    case "fail":
                        await self._step_finished(db, task_id, step, "failed")
                        await fail_task(
                            db,
                            task,
                            error_code=result.error_code,
                            message=result.message,
                            actor=ACTOR,
                            audit=ports.audit,
                        )
                        stopped = "failed"
                    case "transition":
                        assert result.to is not None
                        await self._step_finished(db, task_id, step, "done")
                        await transition(
                            db, task, result.to, reason=result.reason, actor=ACTOR, audit=ports.audit
                        )
                        stopped = "transitioned"
            if stopped is not None:
                await release_lease(db, task_id, ports.worker_id)
            await commit_and_publish(db, ports.bus)
        return stopped

    async def _step_finished(
        self, db: AsyncSession, task_id: uuid.UUID, step: str, outcome: Literal["done", "failed"]
    ) -> None:
        """Emit ``task.step_finished``. Only a done step joins ``completed_steps``: a failed one runs again on
        retry."""
        if outcome == "done":
            completed = func.coalesce(Task.checkpoint["completed_steps"], literal([], JSONB))
            await db.execute(
                update(Task)
                .where(Task.id == task_id, ~Task.checkpoint.contains({"completed_steps": [step]}))
                .values(
                    checkpoint=func.jsonb_set(
                        Task.checkpoint, _path("completed_steps"), completed.op("||")(literal([step], JSONB))
                    )
                )
                .execution_options(synchronize_session=False)
            )
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
            async with self._ports.sessions() as db:
                await release_lease(db, task_id, self._ports.worker_id)
                await db.commit()
        except Exception:
            logger.error("lease_release_failed", task_id=str(task_id), exc_info=True)
