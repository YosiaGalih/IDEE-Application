"""Agent profiles (the registry, synced) and agent runs (DATA_MODEL M07; spec WP-3.1b goal 5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey, in_values
from app.models.enums import (
    AGENT_GROUPS,
    AGENT_MODES,
    AGENT_PRIORITIES,
    AGENT_RUN_STATUSES,
    AGENT_TARGET_MODES,
    COST_STATUSES,
    MODEL_TIERS,
)

_EMPTY_JSON = text("'{}'::jsonb")


class AgentProfile(Timestamps, Base):
    """One registry entry as last synced. Rows are never deleted (agent ids are never reused): an id that left
    the registry keeps its row with ``in_registry = false`` and ``enabled = false``."""

    __tablename__ = "agent_profiles"
    __table_args__ = (
        CheckConstraint(in_values('"group"', AGENT_GROUPS), name="group"),
        CheckConstraint(in_values("mvp_mode", AGENT_MODES), name="mvp_mode"),
        CheckConstraint(in_values("priority", AGENT_PRIORITIES), name="priority"),
        CheckConstraint(in_values("model_tier", MODEL_TIERS), name="model_tier"),
        CheckConstraint(
            "target_mode IS NULL OR " + in_values("target_mode", AGENT_TARGET_MODES), name="target_mode"
        ),
    )

    agent_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    group: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mvp_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(4), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    triggers: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    tools: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    permission_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    runtime_agent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input_contract: Mapped[str | None] = mapped_column(String(60), nullable=True)
    output_contract: Mapped[str | None] = mapped_column(String(60), nullable=True)
    inputs_tools_note: Mapped[str] = mapped_column(Text, nullable=False)
    output_note: Mapped[str] = mapped_column(Text, nullable=False)
    access_note: Mapped[str] = mapped_column(Text, nullable=False)
    target_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    phase: Mapped[str | None] = mapped_column(String(8), nullable=True)
    contract_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    registry_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    in_registry: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class AgentRun(UUIDPrimaryKey, Timestamps, Base):
    """One invocation of an agent for a task (FR-046). Written by the AgentRuntime (WP-3.1f)."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(in_values("status", AGENT_RUN_STATUSES), name="status"),
        CheckConstraint(
            "cost_status IS NULL OR " + in_values("cost_status", COST_STATUSES), name="cost_status"
        ),
        Index("ix_agent_runs_task_id", "task_id"),
        Index("ix_agent_runs_agent_id_started_at", "agent_id", "started_at"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("agent_profiles.agent_id", ondelete="RESTRICT"), nullable=False
    )
    agent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    step: Mapped[str] = mapped_column(String(40), nullable=False)
    model_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(CHAR(12), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="running")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=_EMPTY_JSON)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    cost_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
