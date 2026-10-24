"""Tasks, dependencies and events (DATA_MODEL M08). Only workflow.states.transition writes ``status``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey, in_values
from app.models.enums import DEPENDENCY_TYPES, TASK_PRIORITIES, TASK_STATUSES, TASK_STEPS, WORKFLOW_TYPES

_EMPTY_JSON = text("'{}'::jsonb")
# The tasks_status_guard trigger (migration 0003) rejects a status change unless this transaction-local
# setting names the state machine: a database backstop for CLAUDE.md #4.
STATUS_WRITER_SETTING = "kk.status_writer"
STATUS_WRITER = "transition"


class Task(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(in_values("workflow_type", WORKFLOW_TYPES), name="workflow_type"),
        CheckConstraint(in_values("status", TASK_STATUSES), name="status"),
        CheckConstraint(
            f"current_step IS NULL OR {in_values('current_step', TASK_STEPS)}", name="current_step"
        ),
        CheckConstraint(in_values("priority", TASK_PRIORITIES), name="priority"),
        Index("ix_tasks_project_id_status", "project_id", "status"),
        Index("ix_tasks_status_lease_expires_at", "status", "lease_expires_at"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'draft'"))
    status_reason: Mapped[str | None] = mapped_column(String(200))
    current_step: Mapped[str | None] = mapped_column(String(40))
    priority: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'normal'"))
    budget_limit: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=_EMPTY_JSON)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=_EMPTY_JSON)
    branch_name: Mapped[str | None] = mapped_column(String(120))
    base_commit: Mapped[str | None] = mapped_column(String(64))
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    debug_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskDependency(UUIDPrimaryKey, Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        CheckConstraint(in_values("dependency_type", DEPENDENCY_TYPES), name="dependency_type"),
        CheckConstraint("task_id <> depends_on_task_id", name="not_self"),
        UniqueConstraint(
            "task_id", "depends_on_task_id", name="uq_task_dependencies_task_id_depends_on_task_id"
        ),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dependency_type: Mapped[str] = mapped_column(String(16), nullable=False)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "seq", name="uq_task_events_task_id_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=_EMPTY_JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
