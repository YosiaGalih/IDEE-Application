"""Runtime DB role privileges (T10; WP-1.5): the API's audit guard passes for the granted role."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import audit_guard_problems
from app.core.db_roles import InvalidRoleName, grant_runtime_role


@pytest.fixture
async def runtime_role(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    role = f"kk_runtime_{uuid.uuid4().hex[:8]}"
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        # PostgreSQL 16: a CREATEROLE (non-superuser) creator must grant itself SET to switch to the role.
        await conn.execute(text(f'GRANT "{role}" TO CURRENT_USER WITH SET TRUE'))
    yield role
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP OWNED BY "{role}"'))
        await conn.execute(text(f'DROP ROLE "{role}"'))


async def test_granted_role_passes_the_audit_guard(engine: AsyncEngine, runtime_role: str) -> None:
    async with engine.begin() as conn:
        await grant_runtime_role(conn, runtime_role)
        await grant_runtime_role(conn, runtime_role)  # idempotent
    async with engine.connect() as conn:
        await conn.execute(text(f'SET ROLE "{runtime_role}"'))
        assert await audit_guard_problems(conn) == []
        privileges = (
            await conn.execute(
                text(
                    "SELECT has_table_privilege(current_user, 'audit_events', 'INSERT'),"
                    " has_table_privilege(current_user, 'audit_events', 'SELECT'),"
                    " has_table_privilege(current_user, 'tasks', 'UPDATE'),"
                    " has_table_privilege(current_user, 'tasks', 'DELETE'),"
                    " has_table_privilege(current_user, 'alembic_version', 'SELECT'),"
                    " has_table_privilege(current_user, 'alembic_version', 'UPDATE')"
                )
            )
        ).one()
        assert tuple(privileges) == (True, True, True, True, True, False)


async def test_runtime_role_cannot_rewrite_the_audit_trail(engine: AsyncEngine, runtime_role: str) -> None:
    async with engine.begin() as conn:
        await grant_runtime_role(conn, runtime_role)
    for statement in (
        "UPDATE audit_events SET action = 'x'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ):
        async with engine.connect() as conn:
            await conn.execute(text(f'SET ROLE "{runtime_role}"'))
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text(statement))


async def test_the_owner_fails_the_guard(engine: AsyncEngine) -> None:
    """The test connection owns the tables (and is a superuser in CI): the API would refuse to start."""
    async with engine.connect() as conn:
        problems = await audit_guard_problems(conn)
    assert "runtime role owns audit_events (can disable or drop the trigger)" in problems


@pytest.mark.parametrize("role", ['x"; DROP TABLE tasks; --', "UPPER", "", "a" * 64])
async def test_role_names_are_validated(engine: AsyncEngine, role: str) -> None:
    async with engine.begin() as conn:
        with pytest.raises(InvalidRoleName):
            await grant_runtime_role(conn, role)


async def test_unknown_role(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        with pytest.raises(InvalidRoleName, match="does not exist"):
            await grant_runtime_role(conn, "kk_no_such_role")
