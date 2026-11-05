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

from sqlalchemy import or_, select
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
        approval.handler_attempts += 1
        approval.updated_at = at  # the backoff clock
        if error is None:
            approval.handler_status, approval.handler_error = "succeeded", None
        else:
            approval.handler_status = "failed"
            approval.handler_error = audit.redactor.redact(error)[:ERROR_MAX]
            metadata = {"action_type": approval.action_type, "attempts": approval.handler_attempts}
            for action in ("approval.handler_failed",) + (
                ("approval.handler_gave_up",) if approval.handler_attempts >= MAX_ATTEMPTS else ()
            ):
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
    raises: a failure is recorded and left to the sweep."""
    at = _now(now)
    async with sessions() as session:
        approval = await session.get(Approval, approval_id)
        if approval is None:
            return "succeeded"
        status, handler_status, action_type = approval.status, approval.handler_status, approval.action_type
    path = _PATHS.get(status)
    if path is None or handler_status in ("succeeded", "not_required"):
        return handler_status  # pending/superseded need no handler; done is done
    handler = handlers.get(action_type)
    try:
        if handler is None:
            raise LookupError(f"no handler registered for {action_type}")
        await getattr(handler, path)(approval_id, now=at)
    except Exception as exc:  # recorded and retried, never lost: ADR-024
        logger.warning("approval_handler_failed", approval_id=str(approval_id), error_type=type(exc).__name__)
        return await _record(sessions, audit, approval_id, error=f"{type(exc).__name__}: {exc}", at=at)
    return await _record(sessions, audit, approval_id, error=None, at=at)


def _due(approval: Approval, at: datetime) -> bool:
    if approval.handler_attempts == 0:
        return True
    wait = BACKOFF[min(approval.handler_attempts, len(BACKOFF)) - 1]
    return approval.updated_at + wait <= at


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
        candidates = (
            await session.scalars(
                select(Approval)
                .where(
                    Approval.handler_status.in_(("pending", "failed")),
                    Approval.status.in_(tuple(_PATHS)),
                    Approval.handler_attempts < MAX_ATTEMPTS,
                    or_(Approval.handler_attempts == 0, Approval.updated_at <= at),
                )
                .order_by(Approval.updated_at)
                .limit(_SWEEP_BATCH)
            )
        ).all()
        due = [approval.id for approval in candidates if _due(approval, at)]
    failed = 0
    for approval_id in due:
        if (
            await dispatch(sessions=sessions, audit=audit, handlers=handlers, approval_id=approval_id, now=at)
            == "failed"
        ):
            failed += 1
    return SweepResult(expired=len(expired), dispatched=len(due), failed=failed)
