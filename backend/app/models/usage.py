"""The usage ledger: one row per model attempt that reached a provider (FR-133; spec WP-3.1f Keputusan Lead
#3, #4). Written only by ``app.services.usage`` (tests/unit/test_usage_writer_guard.py), each row in its own
transaction, and never updated.

``call_id`` names one logical model call; its retries share it, and ``(call_id, attempt)`` is unique, so
writing the same attempt again never doubles a row. Failed and cancelled attempts are rows too: their cost is
``partial`` (usage is a lower bound) or ``unavailable`` (no usage), never a zero that reads as a price.
``estimated_cost`` keeps ten decimal places so a tiny call is never stored as 0 (Keputusan Lead #4);
``actual_cost`` is reserved for provider-reported costs (ADR-025) and stays NULL.

A purged project takes its ledger with it (CASCADE); ``project.deleted`` stays in the audit trail.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey, in_values
from app.models.enums import COST_STATUSES, LLM_ERROR_KINDS, USAGE_ATTEMPT_STATUSES

TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
REQUEST_ID_MAX = 120


class UsageRecord(UUIDPrimaryKey, Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint("attempt >= 1", name="attempt"),
        CheckConstraint("retry_count = attempt - 1", name="retry_count"),
        CheckConstraint(in_values("status", USAGE_ATTEMPT_STATUSES), name="status"),
        CheckConstraint(
            f"error_kind IS NULL OR {in_values('error_kind', LLM_ERROR_KINDS)}", name="error_kind"
        ),
        CheckConstraint("status <> 'ok' OR error_kind IS NULL", name="ok_has_no_error"),
        # The retry loop always records kind "cancelled" with status "cancelled", and only then.
        CheckConstraint(
            "(status = 'cancelled') = (coalesce(error_kind, '') = 'cancelled')", name="cancelled_kind"
        ),
        CheckConstraint(" AND ".join(f"({c} IS NULL OR {c} >= 0)" for c in TOKEN_COLUMNS), name="tokens"),
        CheckConstraint(
            f"({' AND '.join(f'{c} IS NULL' for c in TOKEN_COLUMNS)}) "
            f"OR ({' AND '.join(f'{c} IS NOT NULL' for c in TOKEN_COLUMNS)})",
            name="tokens_together",
        ),
        CheckConstraint("input_token_bound >= 0", name="input_token_bound"),
        CheckConstraint("output_token_cap > 0", name="output_token_cap"),
        CheckConstraint("estimated_cost IS NULL OR estimated_cost >= 0", name="estimated_cost"),
        CheckConstraint("actual_cost IS NULL", name="actual_cost"),
        CheckConstraint("currency = 'USD'", name="currency"),
        CheckConstraint(in_values("cost_status", COST_STATUSES), name="cost_status"),
        CheckConstraint(
            "(estimated_cost IS NULL) = (cost_status = 'unavailable')", name="cost_matches_status"
        ),
        # A failed or cancelled attempt's usage is a lower bound: never a plain estimate (AG-13 3.1a M2).
        CheckConstraint("status = 'ok' OR cost_status <> 'estimated'", name="failed_not_estimated"),
        CheckConstraint(
            "input_tokens IS NOT NULL OR cost_status = 'unavailable'", name="no_tokens_unavailable"
        ),
        CheckConstraint("duration_ms >= 0", name="duration_ms"),
        CheckConstraint(
            "agent_run_id IS NULL OR (task_id IS NOT NULL AND agent_id IS NOT NULL)", name="run_has_task"
        ),
        UniqueConstraint("call_id", "attempt"),
        Index("ix_usage_records_project_id_created_at", "project_id", "created_at"),
        Index("ix_usage_records_task_id_created_at", "task_id", "created_at"),
        Index("ix_usage_records_agent_run_id", "agent_run_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)  # no FK, like tool_calls.agent_id
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False)  # attempt - 1
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(REQUEST_ID_MAX), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)  # None: a non-LLMError failure
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_token_bound: Mapped[int] = mapped_column(Integer, nullable=False)
    output_token_cap: Mapped[int] = mapped_column(Integer, nullable=False)  # LLMRequest.max_tokens
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    actual_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)  # reserved (ADR-025)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default=text("'USD'"))
    cost_status: Mapped[str] = mapped_column(String(16), nullable=False)
    pricing_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # No updated_at: a ledger row is never changed.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
