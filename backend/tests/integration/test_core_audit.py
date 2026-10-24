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


async def test_writer_normalises_metadata_to_json_and_redacts_free_text(session: AsyncSession) -> None:
    writer = AuditWriter(Redactor())
    event_id = await writer.write(
        session,
        actor_type="system",
        actor_id="worker",
        action="tool.denied",
        result="denied",
        resource_id="token=abcdef123456",
        metadata={"tags": {"b", "a"}, "ratio": float("nan"), "accessToken": "zzz12345"},
    )
    await session.commit()
    row = (
        await session.execute(
            text("SELECT metadata, resource_id FROM audit_events WHERE id = :id"), {"id": event_id}
        )
    ).one()
    assert row.metadata == {"tags": ["a", "b"], "ratio": None, "accessToken": "[REDACTED]"}
    assert "abcdef123456" not in row.resource_id


async def test_writer_validates_column_lengths(session: AsyncSession) -> None:
    writer = AuditWriter(Redactor())
    with pytest.raises(ValueError, match="action"):
        await writer.write(session, actor_type="system", actor_id="x", action="a" * 81, result="success")


async def test_detached_write_survives_caller_rollback(engine: AsyncEngine, session: AsyncSession) -> None:
    from app.core.db import make_sessionmaker

    writer = AuditWriter(Redactor())
    event_id = await writer.write_detached(
        make_sessionmaker(engine), actor_type="user", actor_id="u1", action="auth.login", result="denied"
    )
    await session.rollback()
    found = (
        await session.execute(text("SELECT count(*) FROM audit_events WHERE id = :id"), {"id": event_id})
    ).scalar_one()
    assert found == 1


async def test_runtime_role_cannot_mutate_or_disable_audit(
    engine: AsyncEngine, migrated_database: str
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.audit import audit_guard_problems

    async with engine.begin() as conn:
        await conn.execute(text("DROP ROLE IF EXISTS kk_test_runtime"))
        await conn.execute(text("CREATE ROLE kk_test_runtime LOGIN PASSWORD 'runtime-pass-123'"))
        await conn.execute(text("GRANT INSERT, SELECT ON audit_events TO kk_test_runtime"))
        await conn.execute(text("GRANT USAGE ON SEQUENCE audit_events_id_seq TO kk_test_runtime"))
    runtime_url = migrated_database.replace("kodekantor:kodekantor@", "kk_test_runtime:runtime-pass-123@")
    runtime = create_async_engine(runtime_url)
    try:
        for statement in (
            "UPDATE audit_events SET action = 'x'",
            "DELETE FROM audit_events",
            "TRUNCATE audit_events",
            "ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_mutation",
        ):
            async with runtime.connect() as conn:
                with pytest.raises(DBAPIError):
                    await conn.execute(text(statement))
        async with runtime.connect() as conn:
            assert await audit_guard_problems(conn) == []
        async with engine.connect() as conn:
            assert await audit_guard_problems(conn)  # the owner role is flagged
    finally:
        await runtime.dispose()
        async with engine.begin() as conn:
            await conn.execute(text("REVOKE ALL ON audit_events FROM kk_test_runtime"))
            await conn.execute(text("REVOKE ALL ON SEQUENCE audit_events_id_seq FROM kk_test_runtime"))
            await conn.execute(text("DROP ROLE kk_test_runtime"))


async def test_triggers_fire_even_in_replica_mode(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        modes = (
            await conn.execute(
                text(
                    "SELECT tgname, tgenabled::text FROM pg_trigger "
                    "WHERE tgrelid = 'audit_events'::regclass AND NOT tgisinternal"
                )
            )
        ).all()
    assert {name: mode for name, mode in modes} == {
        "audit_events_no_mutation": "A",
        "audit_events_no_truncate": "A",
    }
