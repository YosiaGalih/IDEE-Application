"""Assembles the task engine from its ports (spec WP-3.2 §14.4a). The worker calls this per job."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.config import Settings
from app.core.events import EventBus
from app.workflow.engine import Step, TaskEngine, WorkflowPorts


def build_orchestrator(
    *,
    sessions: async_sessionmaker[AsyncSession],
    bus: EventBus,
    audit: AuditWriter,
    settings: Settings,
    worker_id: str,
    steps: Mapping[str, Step] | None = None,
) -> TaskEngine:
    """``steps=None`` is production: the registry is empty until the step work packages register theirs."""
    return TaskEngine(
        WorkflowPorts(
            sessions=sessions,
            bus=bus,
            audit=audit,
            worker_id=worker_id,
            max_active=settings.max_active_tasks,
            steps=steps if steps is not None else {},
            delivery_limit_s=settings.task_delivery_max_minutes * 60,
        )
    )
