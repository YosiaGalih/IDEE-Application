"""Provider configuration and the Claude readiness gate (DATA_MODEL provider_configs, ADR-004, ADR-032; spec
WP-3.1e Goal 1). One row per provider.

The API key itself is never stored: ``secret_ref`` names where it lives (env/secret file, worker only), and
the worker reports what it saw (``secret_status``, a masked ``secret_hint``, a one-way
``secret_fingerprint`` that detects a rotated key). ``readiness_report`` is the last gate run, already
redacted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, Boolean, CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey, in_values
from app.models.enums import LLM_PROVIDER_MODES, PROVIDERS, READINESS_STATUSES, SECRET_STATUSES


class ProviderConfig(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "provider_configs"
    __table_args__ = (
        CheckConstraint(in_values("provider", PROVIDERS), name="provider"),
        CheckConstraint(in_values("readiness_status", READINESS_STATUSES), name="readiness_status"),
        CheckConstraint(in_values("secret_status", SECRET_STATUSES), name="secret_status"),
        CheckConstraint(
            f"llm_provider_mode IS NULL OR {in_values('llm_provider_mode', LLM_PROVIDER_MODES)}",
            name="llm_provider_mode",
        ),
        CheckConstraint(
            "secret_fingerprint IS NULL OR secret_fingerprint ~ '^[0-9a-f]{16}$'", name="secret_fingerprint"
        ),
    )

    provider: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)  # as reported by the worker
    model_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    secret_ref: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("'ANTHROPIC_API_KEY'")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    pricing_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    llm_provider_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    readiness_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'unknown'")
    )
    readiness_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    readiness_report: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    secret_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'unknown'"))
    secret_hint: Mapped[str | None] = mapped_column(String(40), nullable=True)
    secret_fingerprint: Mapped[str | None] = mapped_column(CHAR(16), nullable=True)
    secret_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
