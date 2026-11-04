"""Projects, repositories and conversations (DATA_MODEL M03, M04, M06). Messages land with their WP."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey, in_values
from app.models.enums import (
    INDEX_ENTRY_KINDS,
    PROJECT_STATUSES,
    REPOSITORY_PROVIDERS,
    REPOSITORY_SYNC_STATES,
)


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


class Repository(UUIDPrimaryKey, Timestamps, Base):
    """The project's git repository at ``WORKSPACE_ROOT/<project_id>/repo`` (one per project in v1)."""

    __tablename__ = "repositories"
    __table_args__ = (
        CheckConstraint(in_values("provider", REPOSITORY_PROVIDERS), name="provider"),
        CheckConstraint(in_values("sync_state", REPOSITORY_SYNC_STATES), name="sync_state"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # Stored without userinfo; a credential is only ever a reference name (credential_ref), never a value.
    remote_url: Mapped[str | None] = mapped_column(String(500))
    branch: Mapped[str] = mapped_column(String(100), nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(200))
    sync_state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ready'"))
    index_commit: Mapped[str | None] = mapped_column(String(64))
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The index of index_commit stopped at a limit (WP-2.1d): partial, rebuilt on the next request.
    index_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)


class RepoIndexEntry(Base):
    """One file or one symbol of a project's indexed commit (DATA_MODEL M04/M16, WP-2.1d), rebuilt whole by
    the index job. ``bigserial`` key and no timestamps (DATA_MODEL); ``commit`` names the tree read."""

    __tablename__ = "repo_index_entries"
    __table_args__ = (
        CheckConstraint(in_values("kind", INDEX_ENTRY_KINDS), name="kind"),
        Index("ix_repo_index_entries_project_id_kind", "project_id", "kind"),
        Index("ix_repo_index_entries_project_id_symbol", "project_id", "symbol"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(300))
    symbol_kind: Mapped[str | None] = mapped_column(String(40))
    line: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(40))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(CHAR(64))
    commit: Mapped[str] = mapped_column(String(64), nullable=False)


class Conversation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "conversations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
