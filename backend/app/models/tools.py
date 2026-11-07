"""Tool calls: one row per ``ToolGateway.execute`` (DATA_MODEL tool_calls, SECURITY_MODEL T10; spec WP-3.1d
Goal 4). Written only by ``app.tools.gateway``.

``running`` is the claim committed before a side-effect handler starts: a row still ``running`` after a crash
means the outcome is unknown, and the handler is never run again for the same ``idempotency_key``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Index, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey, in_values
from app.models.enums import TOOL_CALL_STATUSES

SUMMARY_MAX = 2048


class ToolCall(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint(in_values("status", TOOL_CALL_STATUSES), name="status"),
        CheckConstraint("risk_level BETWEEN 0 AND 4", name="risk_level"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash"),
        CheckConstraint(
            "idempotency_key IS NULL OR idempotency_key ~ '^[0-9a-f]{64}$'", name="idempotency_key"
        ),
        CheckConstraint(f"char_length(output_summary) <= {SUMMARY_MAX}", name="output_summary"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_ms"),
        Index("ix_tool_calls_task_id_created_at", "task_id", "created_at"),
        Index("ix_tool_calls_agent_run_id_tool_name", "agent_run_id", "tool_name"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[str] = mapped_column(String(40), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_use_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    risk_level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(CHAR(64), nullable=True, unique=True)
    input_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    sanitized_input: Mapped[Any] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
