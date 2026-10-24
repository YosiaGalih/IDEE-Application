"""Append-only audit writer (FR-146, SECURITY_MODEL §10). Metadata is redacted before it is stored."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import request_id_var
from app.core.redaction import Redactor
from app.models.audit import ACTOR_TYPES, RESULTS, AuditEvent


class AuditWriter:
    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor

    async def write(
        self,
        session: AsyncSession,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
        result: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        project_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        ip: str | None = None,
    ) -> int:
        """Insert one event in the caller's transaction and return its id. The caller commits."""
        if actor_type not in ACTOR_TYPES:
            raise ValueError(f"actor_type must be one of {ACTOR_TYPES}")
        if result not in RESULTS:
            raise ValueError(f"result must be one of {RESULTS}")
        event = AuditEvent(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            result=result,
            resource_type=resource_type,
            resource_id=resource_id,
            project_id=project_id,
            metadata_=self._redactor.redact_obj(metadata or {}, stringify=True),
            correlation_id=correlation_id or request_id_var.get(),
            ip=ip,
        )
        session.add(event)
        await session.flush()
        return event.id
