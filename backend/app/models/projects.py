"""Projects and conversations (DATA_MODEL M03, M06). Repositories and messages land with their WPs."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey, in_values
from app.models.enums import PROJECT_STATUSES


class Project(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(in_values("status", PROJECT_STATUSES), name="status"),
        UniqueConstraint("owner_id", "slug", name="uq_projects_owner_id_slug"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    stack: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    workspace_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("'main'"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class Conversation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "conversations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
