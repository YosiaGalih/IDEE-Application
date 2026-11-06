"""Approval primitives, without authorisation (spec WP-3.1c Goals 2 and 3; SECURITY_MODEL §5, FR-063).

The only writer of ``approvals.status``. Callers decide who may act; this module decides what an action does
to an approval: create it bound to a payload hash, supersede it when the payload changes, consume it once,
expire it. Every function works in the caller's transaction and never commits.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditWriter
from app.models.approvals import Approval

# API_CONTRACTS §11.1, bound by ADR-034. waive_criterion has no entry: "bukan approval", never created.
EXPIRY: Mapping[str, timedelta] = MappingProxyType(
    {
        "apply_changes": timedelta(days=7),
        "budget_increase": timedelta(hours=24),
        "tool_r3": timedelta(hours=24),
        "git_push": timedelta(hours=24),
        "deploy_staging": timedelta(hours=24),
        "deploy_production": timedelta(hours=4),
        "project_delete": timedelta(days=7),
    }
)
OPEN = ("pending", "approved")
PROJECT_DELETE = "project_delete"
# The action types that can be requested today: each has a handler (approval_registry). 3.1d adds tool_r3.
REQUESTABLE = frozenset({PROJECT_DELETE})


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _check(value: object, where: str) -> None:
    # bool before int: True is an int in Python but true in JSON, and the two must not collide.
    if value is None or isinstance(value, bool | str | int):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            _check(item, f"{where}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(key, str):
                raise ValueError(f"not canonical JSON: a non-string key at {where}")
            _check(item, f"{where}.{key}")
        return
    # Floats are refused: their text form is not the same in every language that may recompute the hash.
    raise ValueError(f"not canonical JSON: {type(value).__name__} at {where}")


def canonical_json(value: object) -> bytes:
    """Sorted keys, no spaces, UTF-8 as is; only objects with string keys, arrays, strings, integers,
    booleans and null."""
    _check(value, "$")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def payload_hash(
    action_type: str, *, project_id: uuid.UUID, task_id: uuid.UUID | None, payload: Mapping[str, object]
) -> str:
    """``sha256(canonical_json({action_type, target, payload}))`` (SECURITY_MODEL §5); ``target`` is where the
    action applies, so the same payload for another project or task is another action."""
    target = {"project_id": str(project_id), "task_id": str(task_id) if task_id is not None else None}
    document = {"action_type": action_type, "target": target, "payload": dict(payload)}
    return hashlib.sha256(canonical_json(document)).hexdigest()


def _actor(requested_by: str) -> tuple[str, str]:
    kind, _, name = requested_by.partition(":")
    return ("user" if kind == "user" else "system"), name


async def audit_event(
    session: AsyncSession,
    audit: AuditWriter,
    approval: Approval,
    action: str,
    *,
    actor: str,
    metadata: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    actor_type, actor_id = _actor(actor)
    await audit.write(
        session,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        result="success",
        resource_type="approval",
        resource_id=str(approval.id),
        project_id=approval.project_id,
        metadata={
            "action_type": approval.action_type,
            "payload_hash": approval.payload_hash,
            **(metadata or {}),
        },
        ip=ip,
    )


async def create(
    session: AsyncSession,
    audit: AuditWriter,
    *,
    project_id: uuid.UUID,
    task_id: uuid.UUID | None,
    action_type: str,
    payload: Mapping[str, object],
    summary: str,
    risk_level: int,
    requested_by: str,
    actor_ip: str | None = None,
    audit_actor: str | None = None,
    renewed_from: uuid.UUID | None = None,
    now: datetime | None = None,
) -> Approval:
    """A ``pending`` approval bound to the hash of exactly this action, expiring per §11.1. ``audit_actor``:
    who caused this request when it is not the requester (a renewal)."""
    if action_type not in EXPIRY or action_type not in REQUESTABLE:
        raise ValueError(f"{action_type} cannot be requested as an approval (no handler)")
    at = _now(now)
    approval = Approval(
        project_id=project_id,
        task_id=task_id,
        action_type=action_type,
        risk_level=risk_level,
        summary=summary,
        payload=dict(payload),
        payload_hash=payload_hash(action_type, project_id=project_id, task_id=task_id, payload=payload),
        requested_by=requested_by,
        expires_at=at + EXPIRY[action_type],
    )
    session.add(approval)
    await session.flush()
    await audit_event(
        session,
        audit,
        approval,
        "approval.requested",
        actor=audit_actor or requested_by,
        metadata={
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            "requested_by": requested_by,
            **({"renewed_from": str(renewed_from)} if renewed_from else {}),
        },
        ip=actor_ip,
    )
    return approval


async def find_open(
    session: AsyncSession, *, project_id: uuid.UUID, action_type: str, lock: bool = False
) -> Approval | None:
    query = (
        select(Approval)
        .where(
            Approval.project_id == project_id, Approval.action_type == action_type, Approval.status.in_(OPEN)
        )
        .order_by(Approval.created_at.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


async def renew_open(
    session: AsyncSession,
    audit: AuditWriter,
    *,
    project_id: uuid.UUID,
    action_type: str,
    changes: Mapping[str, object],
    actor_id: str,
    actor_ip: str | None = None,
    now: datetime | None = None,
) -> Approval | None:
    """The payload changed: the open approval is superseded and a new one asks again (AC-13). The request
    keeps its requester; the audit records who changed the payload. ``None`` when nothing was open."""
    old = await find_open(session, project_id=project_id, action_type=action_type, lock=True)
    if old is None:
        return None
    old.status = "superseded"
    old.handler_status = "not_required"  # nothing was decided, so nothing has to run
    await session.flush()  # before the insert: one open approval per action is enforced by an index
    await audit_event(session, audit, old, "approval.superseded", actor=actor_id, ip=actor_ip)
    return await create(
        session,
        audit,
        project_id=old.project_id,
        task_id=old.task_id,
        action_type=old.action_type,
        payload={**old.payload, **changes},
        summary=old.summary,
        risk_level=old.risk_level,
        requested_by=old.requested_by,
        actor_ip=actor_ip,
        audit_actor=actor_id,
        renewed_from=old.id,
        now=now,
    )


async def consume(
    session: AsyncSession, approval_id: uuid.UUID, *, payload_hash: str, now: datetime | None = None
) -> bool:
    """One use of an approved action with this exact hash, before it expires (SECURITY_MODEL §5). ``False``:
    the action must not run."""
    at = _now(now)
    consumed = await session.scalar(
        update(Approval)
        .where(
            Approval.id == approval_id,
            Approval.status == "approved",
            Approval.payload_hash == payload_hash,
            # NULL: never expires (ARCHITECTURE §10)
            or_(Approval.expires_at.is_(None), Approval.expires_at > at),
        )
        .values(status="consumed", consumed_at=at)
        .returning(Approval.id)
    )
    return consumed is not None


async def decide(
    session: AsyncSession,
    audit: AuditWriter,
    approval: Approval,
    *,
    approve: bool,
    user_id: uuid.UUID,
    note: str | None,
    ip: str | None = None,
    now: datetime | None = None,
) -> None:
    """Record a person's decision on a locked, ``pending``, unexpired approval whose hash the caller checked.
    The handler runs after the caller commits (ADR-024)."""
    at = _now(now)
    approval.status = "approved" if approve else "rejected"
    approval.decided_by, approval.decided_at, approval.decision_note = user_id, at, note
    approval.handler_status, approval.updated_at = "pending", at
    await session.flush()
    await audit_event(
        session,
        audit,
        approval,
        "approval.decided",
        actor=f"user:{user_id}",
        metadata={"decision": "approve" if approve else "reject"},
        ip=ip,
    )


async def expire(
    session: AsyncSession, audit: AuditWriter, approval: Approval, *, actor: str, now: datetime | None = None
) -> None:
    """``pending`` (or approved but not consumed) past its time: ``expired``, with its handler still to run so
    the action's owner can undo what the request did (a pending delete restores the project)."""
    approval.status = "expired"
    approval.handler_status, approval.updated_at = "pending", _now(now)
    await session.flush()
    await audit_event(session, audit, approval, "approval.expired", actor=actor)


async def expire_due(
    session: AsyncSession, audit: AuditWriter, *, now: datetime | None = None, limit: int = 100
) -> list[uuid.UUID]:
    at = _now(now)
    due = (
        await session.scalars(
            select(Approval)
            .where(Approval.status == "pending", Approval.expires_at <= at)
            .order_by(Approval.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for approval in due:
        await expire(session, audit, approval, actor="system:approvals_sweep", now=at)
    return [approval.id for approval in due]
