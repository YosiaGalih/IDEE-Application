"""``audit_events`` — append-only audit trail (DATA_MODEL § Audit, FR-146)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ACTOR_TYPES = ("user", "agent", "system")
RESULTS = ("success", "denied", "error")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(_in("actor_type", ACTOR_TYPES), name="actor_type"),
        CheckConstraint(_in("result", RESULTS), name="result"),
        Index("ix_audit_events_ts", "ts"),
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
        Index("ix_audit_events_project_id", "project_id"),
        Index("ix_audit_events_correlation_id", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    correlation_id: Mapped[str | None] = mapped_column(String(80))
    ip: Mapped[str | None] = mapped_column(String(64))
