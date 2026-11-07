"""The approval side of an R3 tool call: the ``R3Approvals`` port of the ToolGateway (ADR-022; API §11 #6,
§11.1 ``tool_r3``; spec WP-3.1d Goal 5).

Runs in the gateway's policy transaction (task row already locked). One open ``tool_r3`` per task: the same
action (same hash) reuses the pending request or consumes the approved one; another action supersedes it and
asks again. The approval is consumed here, when the tool is about to run, never by its handler.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditWriter
from app.models.approvals import Approval
from app.services import approval_store
from app.tools.base import R3InputNotCanonical, R3Ticket, RiskLevel

REQUESTER = "system:tool_gateway"


class ToolR3Approvals:
    def __init__(self, *, audit: AuditWriter) -> None:
        self._audit = audit

    async def authorize(
        self,
        session: AsyncSession,
        *,
        project_id: uuid.UUID,
        task_id: uuid.UUID,
        agent_id: str,
        payload: Mapping[str, object],
        summary: str,
    ) -> R3Ticket:
        # First: a payload that cannot be hashed canonically (floats) is refused before anything is written.
        try:
            digest = approval_store.payload_hash(
                approval_store.TOOL_R3, project_id=project_id, task_id=task_id, payload=payload
            )
        except ValueError as exc:
            raise R3InputNotCanonical(str(exc)) from None
        current = await session.scalar(
            select(Approval)
            .where(
                Approval.task_id == task_id,
                Approval.action_type == approval_store.TOOL_R3,
                Approval.status.in_(approval_store.OPEN),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = datetime.now(UTC)  # after the lock
        if current is not None:
            live = current.expires_at is None or current.expires_at > now
            if current.payload_hash == digest and live and current.status == "pending":
                return R3Ticket(current.id, consumed=False)
            if current.payload_hash == digest and live and current.status == "approved":
                if await approval_store.consume(session, current.id, payload_hash=digest, now=now):
                    await approval_store.audit_event(
                        session,
                        self._audit,
                        current,
                        "approval.consumed",
                        actor=REQUESTER,
                        metadata={"agent_id": agent_id},
                    )
                    return R3Ticket(current.id, consumed=True)
            # Another action, or this one past its time: the open request no longer stands for it.
            await approval_store.supersede(session, self._audit, current, actor=REQUESTER)
        created = await approval_store.create(
            session,
            self._audit,
            project_id=project_id,
            task_id=task_id,
            action_type=approval_store.TOOL_R3,
            payload=payload,
            summary=summary,
            risk_level=int(RiskLevel.R3),
            requested_by=REQUESTER,
            metadata={"agent_id": agent_id},  # spec Goal 5: the agent is named in the audit
            now=now,
        )
        return R3Ticket(created.id, consumed=False)
