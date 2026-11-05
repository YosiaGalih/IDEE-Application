"""The handler behind each action type that can be requested today (ADR-024; WP-INT-03 "bootstrap handler di
API & worker"). 3.1d adds ``tool_r3``; later WPs add theirs."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.services.approval_handlers import Handlers
from app.services.project_delete import ACTION, ProjectDeleteHandler


def default_handlers(*, sessions: async_sessionmaker[AsyncSession], audit: AuditWriter) -> Handlers:
    return {ACTION: ProjectDeleteHandler(sessions=sessions, audit=audit)}
