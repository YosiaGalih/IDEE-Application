"""The handler behind each action type that can be requested today (ADR-024; WP-INT-03 "bootstrap handler di
API & worker"). ``approval_store.REQUESTABLE`` names exactly these (a test keeps them equal)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.services import project_delete, tool_r3
from app.services.approval_handlers import Handlers


def default_handlers(*, sessions: async_sessionmaker[AsyncSession], audit: AuditWriter) -> Handlers:
    return {
        project_delete.ACTION: project_delete.ProjectDeleteHandler(sessions=sessions, audit=audit),
        tool_r3.ACTION: tool_r3.ToolR3Handler(sessions=sessions, audit=audit),
    }
