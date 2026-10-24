"""Audit trail is append-only and redacted (FR-146, AC-19, SECURITY_MODEL §10)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.audit import AuditWriter
from app.core.context import request_id_var
from app.core.redaction import Redactor

pytestmark = pytest.mark.asyncio


async def insert_row(session: AsyncSession) -> int:
    writer = AuditWriter(Redactor())
    event_id = await writer.write(
        session, actor_type="system", actor_id="test", action="test.created", result="success"
    )
    await session.commit()
    return event_id


async def test_audit_events_cannot_be_updated_or_deleted(session: AsyncSession, engine: AsyncEngine) -> None:
    event_id = await insert_row(session)
    for statement in (
        "UPDATE audit_events SET action = 'tampered' WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
    ):
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError, match="append-only"):
                await conn.execute(text(statement), {"id": event_id})
    async with engine.connect() as conn:
        action = (
            await conn.execute(text("SELECT action FROM audit_events WHERE id = :id"), {"id": event_id})
        ).scalar_one()
    assert action == "test.created"


async def test_audit_events_cannot_be_truncated(session: AsyncSession, engine: AsyncEngine) -> None:
    await insert_row(session)
    async with engine.connect() as conn:
        with pytest.raises(DBAPIError, match="append-only"):
            await conn.execute(text("TRUNCATE audit_events"))


async def test_check_constraints_reject_unknown_values(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError, match="ck_audit_events_actor_type"):
            await conn.execute(
                text(
                    "INSERT INTO audit_events (actor_type, actor_id, action, result) "
                    "VALUES ('robot', 'x', 'y', 'success')"
                )
            )


async def test_writer_redacts_metadata_and_uses_request_id(session: AsyncSession) -> None:
    project_id = uuid.uuid4()
    token = request_id_var.set("req_audit_test_1")
    try:
        writer = AuditWriter(Redactor(literals=["configured-secret-literal"]))
        event_id = await writer.write(
            session,
            actor_type="user",
            actor_id=str(uuid.uuid4()),
            action="auth.login",
            result="denied",
            resource_type="session",
            project_id=project_id,
            metadata={"password": "hunter2hunter2", "note": "key configured-secret-literal", "attempt": 3},
            ip="203.0.113.7",
        )
        await session.commit()
    finally:
        request_id_var.reset(token)
    row = (
        await session.execute(
            text("SELECT metadata, correlation_id, project_id FROM audit_events WHERE id = :id"),
            {"id": event_id},
        )
    ).one()
    assert row.metadata == {"password": "[REDACTED]", "note": "key [REDACTED]", "attempt": 3}
    assert row.correlation_id == "req_audit_test_1"
    assert row.project_id == project_id


async def test_writer_rejects_values_outside_the_contract(session: AsyncSession) -> None:
    writer = AuditWriter(Redactor())
    with pytest.raises(ValueError, match="actor_type"):
        await writer.write(session, actor_type="robot", actor_id="x", action="y", result="success")
    with pytest.raises(ValueError, match="result"):
        await writer.write(session, actor_type="system", actor_id="x", action="y", result="maybe")
