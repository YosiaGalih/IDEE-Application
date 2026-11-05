"""Reading and deciding approvals for the API (API §8, §11.1; ADR-024, ADR-034; spec WP-3.1c Goal 6).

Only a signed-in person with ``approval:decide`` **and** the action's own permission on the project decides
(FR-067; CLAUDE.md #5): nothing else in the system calls ``decide``. The decision is committed first; the
handler runs after it (ADR-024).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.approvals import Approval
from app.models.projects import Project
from app.services import approval_handlers, approval_store, project_delete
from app.services.approval_handlers import Handlers
from app.services.projects import Actor, ProjectEnv, not_found, permissions_for, visible_projects_filter

# The permission an action needs on top of approval:decide (API §11.1, ADR-034).
DECIDE_PERMISSION: Mapping[str, str] = MappingProxyType({project_delete.ACTION: "project:delete"})
ListStatus = Literal["pending", "approved", "rejected", "expired", "consumed", "superseded", "all"]
Decision = Literal["approve", "reject"]


@dataclass(frozen=True, slots=True)
class ApprovalView:
    approval: Approval
    can_decide: bool


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def may_decide(approval: Approval, permissions: frozenset[str]) -> bool:
    """``approval:decide`` and the action's own permission on the approval's project (ADR-034)."""
    needed = DECIDE_PERMISSION.get(approval.action_type)
    return "approval:decide" in permissions and needed is not None and needed in permissions


def can_decide(approval: Approval, permissions: frozenset[str], at: datetime) -> bool:
    return (
        approval.status == "pending"
        and (approval.expires_at is None or approval.expires_at > at)
        and may_decide(approval, permissions)
    )


def view_of(approval: Approval, permissions: frozenset[str], *, now: datetime | None = None) -> ApprovalView:
    return ApprovalView(approval, can_decide(approval, permissions, _now(now)))


async def list_approvals(
    env: ProjectEnv,
    actor: Actor,
    *,
    status: ListStatus,
    project_id: uuid.UUID | None,
    page: int,
    page_size: int,
    now: datetime | None = None,
) -> tuple[list[ApprovalView], int]:
    """Approvals in projects the actor can see. ``pending``: only those the actor can decide now (§8)."""
    at = _now(now)
    query = (
        select(Approval, Project)
        .join(Project, Project.id == Approval.project_id)
        .where(visible_projects_filter(actor))
    )
    if project_id is not None:
        query = query.where(Approval.project_id == project_id)
    if status != "all":
        query = query.where(Approval.status == status)
    async with env.sessions() as session:
        if status == "pending":  # few by nature; decidability is per row, so filter, then page
            rows = (await session.execute(query.order_by(Approval.expires_at, Approval.id))).all()
            views = [view_of(a, permissions_for(actor, p), now=at) for a, p in rows]
            decidable = [view for view in views if view.can_decide]
            start = (page - 1) * page_size
            return decidable[start : start + page_size], len(decidable)
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = (
            await session.execute(
                query.order_by(Approval.created_at.desc(), Approval.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    return [view_of(a, permissions_for(actor, p), now=at) for a, p in rows], total


async def _visible(
    session: AsyncSession, actor: Actor, approval_id: uuid.UUID, *, lock: bool
) -> tuple[Approval, frozenset[str]]:
    approval = await session.get(Approval, approval_id)
    project = await session.get(Project, approval.project_id) if approval is not None else None
    permissions = permissions_for(actor, project) if project is not None else frozenset()
    if approval is None or "project:read" not in permissions:
        raise not_found()  # out of scope reads as unknown (§1)
    if lock:
        # Only after the scope check, so a stranger never holds (or waits on) a lock on someone else's row.
        locked = await session.scalar(
            select(Approval)
            .where(Approval.id == approval_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise not_found()
        approval = locked
    return approval, permissions


async def get_approval(
    env: ProjectEnv, actor: Actor, approval_id: uuid.UUID, *, now: datetime | None = None
) -> ApprovalView:
    async with env.sessions() as session:
        approval, permissions = await _visible(session, actor, approval_id, lock=False)
    return view_of(approval, permissions, now=now)


def _not_pending(status: str) -> AppError:
    return AppError(
        "INVALID_TRANSITION",
        f"The approval is {status}, not pending",
        http_status=409,
        details=[{"reason": status}],
    )


async def decide(
    env: ProjectEnv,
    handlers: Handlers,
    actor: Actor,
    approval_id: uuid.UUID,
    *,
    decision: Decision,
    payload_hash: str,
    note: str | None,
    now: datetime | None = None,
) -> ApprovalView:
    lapsed = False
    async with env.sessions() as session:
        approval, permissions = await _visible(session, actor, approval_id, lock=True)
        at = _now(now)  # read after the lock wait: an approval is not decided past its time
        if not may_decide(approval, permissions):
            raise AppError("FORBIDDEN", "Not allowed", http_status=403)
        if approval.status != "pending":
            raise _not_pending(approval.status)
        if approval.expires_at is not None and approval.expires_at <= at:
            await approval_store.expire(session, env.audit, approval, actor=f"user:{actor.user_id}", now=at)
            lapsed = True
        elif payload_hash != approval.payload_hash:
            raise AppError(
                "APPROVAL_PAYLOAD_MISMATCH",
                "The approval no longer matches the action you reviewed",
                http_status=409,
            )
        else:
            await approval_store.decide(
                session,
                env.audit,
                approval,
                approve=decision == "approve",
                user_id=actor.user_id,
                note=note,
                ip=actor.ip,
                now=at,
            )
        await session.commit()
    # ADR-024: the decision (or the lapse) is committed; the handler runs on its own transactions.
    await approval_handlers.dispatch(
        sessions=env.sessions, audit=env.audit, handlers=handlers, approval_id=approval_id, now=at
    )
    if lapsed:
        raise _not_pending("expired")
    async with env.sessions() as session:
        # Read directly: the caller was authorised for this decision, and an approved delete has just made
        # the project invisible to everyone.
        decided = await session.get(Approval, approval_id)
    if decided is None:
        raise not_found()
    return ApprovalView(decided, can_decide=False)
