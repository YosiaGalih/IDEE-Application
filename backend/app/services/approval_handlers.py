"""What happens after a decision (ADR-024; spec WP-3.1c Goal 4).

``decide()`` commits the decision first; ``dispatch()`` then runs the action's handler in transactions of its
own. A handler is idempotent and commits itself. A failure is recorded on the approval and audited, never
undoes the decision, and ``sweep()`` retries it with backoff until ``MAX_ATTEMPTS``, after which it stays
``failed`` for an operator (RUNBOOK). The API runs ``dispatch`` right after a decision; the worker runs
``sweep`` on beat.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.logging import get_logger
from app.models.approvals import Approval
from app.services import approval_store

logger = get_logger(__name__)

MAX_ATTEMPTS = 5
# The wait after the n-th failed attempt (index n-1). A first attempt is due at once: running it twice (the
# API's inline dispatch and a sweep at the same moment) is safe because handlers are idempotent.
BACKOFF = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10), timedelta(hours=1))
ERROR_MAX = 500
_SWEEP_BATCH = 100
# Which handler path a decided approval takes. consumed: the handler committed, the mark did not (crash).
_PATHS = {"approved": "approved", "consumed": "approved", "rejected": "rejected", "expired": "expired"}


class ApprovalHandler(Protocol):
    """The action behind one ``action_type``. Each path opens its own session, is idempotent, and commits."""

    async def approved(self, approval_id: uuid.UUID, *, now: datetime) -> None: ...
    async def rejected(self, approval_id: uuid.UUID, *, now: datetime) -> None: ...
    async def expired(self, approval_id: uuid.UUID, *, now: datetime) -> None: ...


Handlers = Mapping[str, ApprovalHandler]


@dataclass(frozen=True, slots=True)
class SweepResult:
    expired: int
    dispatched: int
    failed: int


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _error_text(exc: BaseException) -> str:
    """Type and the first line only: driver errors put SQL parameters (payload text) on later lines."""
    first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {first}"[:ERROR_MAX]


async def _record(
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    approval_id: uuid.UUID,
    *,
    error: str | None,
    at: datetime,
) -> str:
    async with sessions() as session:
        approval = await session.get(Approval, approval_id, with_for_update=True)
        if approval is None:  # the project was purged meanwhile: nothing left to record
            return "succeeded"
        if approval.status == "superseded" or approval.handler_status == "not_required":
            return approval.handler_status  # superseded while the handler ran: nothing was owed
        if approval.handler_status == "succeeded":
            return "succeeded"  # a concurrent run already finished it; a late failure does not undo that
        approval.handler_attempts += 1
        approval.updated_at = at  # the backoff clock
        if error is None:
            approval.handler_status, approval.handler_error = "succeeded", None
        else:
            approval.handler_status = "failed"
            approval.handler_error = audit.redactor.redact(error)[:ERROR_MAX]
            gave_up = approval.handler_attempts == MAX_ATTEMPTS
            metadata = {"action_type": approval.action_type, "attempts": approval.handler_attempts}
            for action in ("approval.handler_failed",) + (("approval.handler_gave_up",) if gave_up else ()):
                await audit.write(
                    session,
                    actor_type="system",
                    actor_id="approval_handlers",
                    action=action,
                    result="error",
                    resource_type="approval",
                    resource_id=str(approval.id),
                    project_id=approval.project_id,
                    metadata=metadata,
                )
            if gave_up:
                logger.error(
                    "approval_handler_gave_up",
                    approval_id=str(approval.id),
                    action_type=approval.action_type,
                    attempts=approval.handler_attempts,
                )
        status = approval.handler_status
        await session.commit()
    return status


async def dispatch(
    *,
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    handlers: Handlers,
    approval_id: uuid.UUID,
    now: datetime | None = None,
) -> str:
    """Run the handler path the approval's status calls for; return the resulting ``handler_status``. Never
    raises: a failure is recorded and left to the sweep; a failure to record is logged and retried there."""
    at = _now(now)
    try:
        async with sessions() as session:
            approval = await session.get(Approval, approval_id)
            if approval is None:
                return "succeeded"
            status, handler_status, action_type = (
                approval.status,
                approval.handler_status,
                approval.action_type,
            )
        path = _PATHS.get(status)
        if path is None or handler_status in ("succeeded", "not_required"):
            return handler_status  # pending/superseded need no handler; done is done
        handler = handlers.get(action_type)
        try:
            if handler is None:
                raise LookupError(f"no handler registered for {action_type}")
            run = {"approved": handler.approved, "rejected": handler.rejected, "expired": handler.expired}[
                path
            ]
            await run(approval_id, now=at)
        except Exception as exc:  # recorded and retried, never lost: ADR-024
            logger.warning(
                "approval_handler_failed",
                approval_id=str(approval_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return await _record(sessions, audit, approval_id, error=_error_text(exc), at=at)
        return await _record(sessions, audit, approval_id, error=None, at=at)
    except Exception:  # the database itself failed: nothing could be recorded; the sweep will see it again
        logger.error("approval_dispatch_unrecorded", approval_id=str(approval_id), exc_info=True)
        return "failed"


def _due_clause(at: datetime) -> ColumnElement[bool]:
    """Due now: a first attempt at once, a retry after its backoff (computed in SQL, so rows waiting on a
    long backoff never crowd out due ones in the batch)."""
    waits = [Approval.handler_attempts == 0] + [
        and_(Approval.handler_attempts == n, Approval.updated_at <= at - BACKOFF[n - 1])
        for n in range(1, MAX_ATTEMPTS)
    ]
    return or_(*waits)


async def sweep(
    *,
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    handlers: Handlers,
    now: datetime | None = None,
) -> SweepResult:
    """Expire what is past its time, then (re)run every handler that is due."""
    at = _now(now)
    async with sessions() as session:
        expired = await approval_store.expire_due(session, audit, now=at)
        await session.commit()
    async with sessions() as session:
        due = (
            await session.scalars(
                select(Approval.id)
                .where(
                    Approval.handler_status.in_(("pending", "failed")),
                    Approval.status.in_(tuple(_PATHS)),
                    _due_clause(at),
                )
                .order_by(Approval.updated_at)
                .limit(_SWEEP_BATCH)
            )
        ).all()
    failed = 0
    for approval_id in due:
        # The clock is read again per dispatch (``now`` is only ever fixed by tests).
        result = await dispatch(
            sessions=sessions, audit=audit, handlers=handlers, approval_id=approval_id, now=now
        )
        failed += result == "failed"
    return SweepResult(expired=len(expired), dispatched=len(due), failed=failed)
