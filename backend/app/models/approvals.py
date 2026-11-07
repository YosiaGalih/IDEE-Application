"""Approvals: one human decision about one exact R3 action (DATA_MODEL approvals, ADR-024, ADR-034).

``payload_hash`` binds the decision to the payload; ``consumed`` makes it single use. Only
``app.services.approval_store`` writes ``status``: the ``approvals_status_guard`` trigger (migration 0008)
refuses any other writer and any move outside the approval cycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey, in_values
from app.models.enums import APPROVAL_ACTION_TYPES, APPROVAL_STATUSES, HANDLER_STATUSES

NOTE_MAX = 2000
# The approvals_status_guard trigger (migration 0008) accepts a status change only when this transaction-local
# setting holds APPROVAL_WRITER; approval_store sets it around each write.
APPROVAL_WRITER_SETTING = "kk.approval_status_writer"
APPROVAL_WRITER = "approval_store"
REQUESTED_BY_PATTERN = r"^(user:[0-9a-f-]{36}|system:[a-z_]{1,40})$"


class Approval(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(in_values("action_type", APPROVAL_ACTION_TYPES), name="action_type"),
        CheckConstraint(in_values("status", APPROVAL_STATUSES), name="status"),
        CheckConstraint(in_values("handler_status", HANDLER_STATUSES), name="handler_status"),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="payload_hash"),
        CheckConstraint(f"requested_by ~ '{REQUESTED_BY_PATTERN}'", name="requested_by"),
        CheckConstraint("risk_level BETWEEN 0 AND 4", name="risk_level"),
        CheckConstraint(f"char_length(decision_note) <= {NOTE_MAX}", name="decision_note"),
        Index("ix_approvals_project_id_status", "project_id", "status"),
        # ADR-024's sweep index, widened to expired (an expired delete must still restore its project) and
        # consumed (a handler that died between consuming and committing is run again).
        Index(
            "ix_approvals_handler_due",
            "handler_status",
            postgresql_where=text(
                "handler_status IN ('pending', 'failed') "
                "AND status IN ('approved', 'consumed', 'rejected', 'expired')"
            ),
        ),
        # One open tool_r3 per task: a new R3 input supersedes the open one (spec WP-3.1d Goal 5).
        Index(
            "uq_approvals_open_tool_r3",
            "task_id",
            unique=True,
            postgresql_where=text("action_type = 'tool_r3' AND status IN ('pending', 'approved')"),
        ),
        # Two concurrent DELETEs of one project open one approval, never two.
        Index(
            "uq_approvals_open_project_delete",
            "project_id",
            unique=True,
            postgresql_where=text("action_type = 'project_delete' AND status IN ('pending', 'approved')"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    handler_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    handler_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    handler_error: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
