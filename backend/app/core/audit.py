"""Append-only audit writer (FR-146, SECURITY_MODEL §10).

Every string that reaches ``audit_events`` passes through the redactor; metadata is normalised to JSON-safe
values. ``write`` joins the caller's transaction (success events commit with the change they describe);
``write_detached`` commits on its own so ``denied``/``error`` events survive the caller's rollback.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.core.context import request_id_var
from app.core.redaction import Redactor
from app.models.audit import ACTOR_TYPES, RESULTS, AuditEvent

_MAX_LENGTHS = {
    "actor_id": 80,
    "action": 80,
    "resource_type": 40,
    "resource_id": 80,
    "correlation_id": 80,
    "ip": 64,
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted((json_safe(item) for item in value), key=str)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


class AuditWriter:
    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor

    @property
    def redactor(self) -> Redactor:
        """The redactor that also knows the configured secret literals; reuse it for related writes."""
        return self._redactor

    def _event(self, **fields: Any) -> AuditEvent:
        if fields["actor_type"] not in ACTOR_TYPES:
            raise ValueError(f"actor_type must be one of {ACTOR_TYPES}")
        if fields["result"] not in RESULTS:
            raise ValueError(f"result must be one of {RESULTS}")
        fields["correlation_id"] = fields.get("correlation_id") or request_id_var.get()
        for name, limit in _MAX_LENGTHS.items():
            value = fields.get(name)
            if value is not None:
                value = self._redactor.redact(str(value)) if name != "ip" else value
                if len(value) > limit:
                    raise ValueError(f"{name} exceeds {limit} characters")
                fields[name] = value
        metadata = self._redactor.redact_obj(fields.pop("metadata") or {}, stringify=True)
        return AuditEvent(metadata_=json_safe(metadata), **fields)

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
        event = self._event(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            result=result,
            resource_type=resource_type,
            resource_id=resource_id,
            project_id=project_id,
            metadata=metadata,
            correlation_id=correlation_id,
            ip=ip,
        )
        session.add(event)
        await session.flush()
        return event.id

    async def write_detached(self, sessions: async_sessionmaker[AsyncSession], **fields: Any) -> int:
        """Insert and commit in an independent session (for denied/error events)."""
        async with sessions() as session:
            event_id = await self.write(session, **fields)
            await session.commit()
            return event_id


async def audit_guard_problems(conn: AsyncConnection) -> list[str]:
    """Why the current DB role could tamper with ``audit_events`` (empty list = append-only holds)."""
    row = (
        await conn.execute(
            text(
                """
                SELECT r.rolsuper,
                       pg_has_role(current_user, c.relowner, 'MEMBER') AS is_owner,
                       has_table_privilege(current_user, 'audit_events', 'UPDATE') AS can_update,
                       has_table_privilege(current_user, 'audit_events', 'DELETE') AS can_delete,
                       has_table_privilege(current_user, 'audit_events', 'TRUNCATE') AS can_truncate
                FROM pg_class c, pg_roles r
                WHERE c.oid = 'audit_events'::regclass AND r.rolname = current_user
                """
            )
        )
    ).one()
    labels = {
        "rolsuper": "runtime role is a superuser",
        "is_owner": (
            "runtime role owns audit_events or is a member of its owner (can disable or drop the trigger)"
        ),
        "can_update": "runtime role has UPDATE on audit_events",
        "can_delete": "runtime role has DELETE on audit_events",
        "can_truncate": "runtime role has TRUNCATE on audit_events",
    }
    problems = [message for key, message in labels.items() if getattr(row, key)]
    # A SECURITY DEFINER function runs with its owner's rights, around the table privileges above.
    definers = await conn.execute(
        text(
            """
            SELECT n.nspname || '.' || p.proname
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.prosecdef
              AND NOT pg_has_role(current_user, p.proowner, 'MEMBER')
              AND has_function_privilege(current_user, p.oid, 'EXECUTE')
            ORDER BY 1
            """
        )
    )
    problems += [
        f"runtime role can execute SECURITY DEFINER function {name} (it runs with its owner's rights)"
        for name in definers.scalars()
    ]
    return problems
