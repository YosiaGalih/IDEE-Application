"""The ``Approval`` response body, shared by ``/approvals`` and ``DELETE /projects/{id}``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.services.approvals import ApprovalView


class ApprovalOut(BaseModel):
    """``Approval`` (API §8) + ``consumed_at``, ``handler_status`` and ``can_decide`` for the caller."""

    id: uuid.UUID
    project_id: uuid.UUID
    task_id: uuid.UUID | None
    action_type: str
    risk_level: int
    summary: str
    payload: dict[str, Any]
    payload_hash: str
    requested_by: str
    status: str
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    expires_at: datetime | None
    consumed_at: datetime | None
    handler_status: str
    created_at: datetime
    can_decide: bool

    @classmethod
    def of(cls, view: ApprovalView) -> ApprovalOut:
        a = view.approval
        return cls(
            id=a.id,
            project_id=a.project_id,
            task_id=a.task_id,
            action_type=a.action_type,
            risk_level=a.risk_level,
            summary=a.summary,
            payload=dict(a.payload),
            payload_hash=a.payload_hash,
            requested_by=a.requested_by,
            status=a.status,
            decided_by=a.decided_by,
            decided_at=a.decided_at,
            decision_note=a.decision_note,
            expires_at=a.expires_at,
            consumed_at=a.consumed_at,
            handler_status=a.handler_status,
            created_at=a.created_at,
            can_decide=view.can_decide,
        )
