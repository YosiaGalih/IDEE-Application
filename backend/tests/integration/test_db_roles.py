"""Runtime DB role privileges (T10; WP-1.5): the API's audit guard passes for the granted role."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import audit_guard_problems
from app.core.db_roles import APPEND_ONLY_TABLES, InvalidRoleName, grant_runtime_role


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
    assert (
        "runtime role owns audit_events or is a member of its owner (can disable or drop the trigger)"
        in problems
    )


async def test_membership_in_the_owner_fails_the_guard(engine: AsyncEngine, runtime_role: str) -> None:
    """Owning through a granted role is owning: the member can SET ROLE to the owner and drop the trigger."""
    async with engine.connect() as conn:
        superuser = (
            await conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
        ).scalar()
    if not superuser:
        pytest.skip("granting the table owner's role needs a superuser test connection (CI has one)")
    async with engine.connect() as conn:
        async with conn.begin():
            await grant_runtime_role(conn, runtime_role)
            owner = (
                await conn.execute(
                    text(
                        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'audit_events'::regclass"
                    )
                )
            ).scalar_one()
            # The fixture made the owner a member of the runtime role; drop that first, a cycle is refused.
            await conn.execute(text(f'REVOKE "{runtime_role}" FROM "{owner}"'))
            await conn.execute(text(f'GRANT "{owner}" TO "{runtime_role}"'))
            await conn.execute(text(f'SET ROLE "{runtime_role}"'))
            problems = await audit_guard_problems(conn)
            await conn.execute(text("RESET ROLE"))
            await conn.rollback()
    assert (
        "runtime role owns audit_events or is a member of its owner (can disable or drop the trigger)"
        in problems
    )


async def test_runtime_role_cannot_call_schema_functions(engine: AsyncEngine, runtime_role: str) -> None:
    """Trigger functions still fire for it; it cannot call them, nor functions that later migrations add."""
    async with engine.begin() as conn:
        await grant_runtime_role(conn, runtime_role)
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(
                text("CREATE FUNCTION kk_later_function() RETURNS int LANGUAGE sql AS 'SELECT 1'")
            )
            executable = (
                (
                    await conn.execute(
                        text(
                            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                            " WHERE n.nspname = 'public' AND has_function_privilege(:role, p.oid, 'EXECUTE')"
                        ),
                        {"role": runtime_role},
                    )
                )
                .scalars()
                .all()
            )
            await conn.rollback()
    assert executable == []


async def test_triggers_still_fire_for_the_runtime_role(engine: AsyncEngine, runtime_role: str) -> None:
    """Without EXECUTE on a trigger function (tasks_status_guard, audit_events_no_mutation) the runtime role's
    writes still run it: PostgreSQL checks EXECUTE when the trigger is created, not when it fires."""
    async with engine.begin() as conn:
        await grant_runtime_role(conn, runtime_role)
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("CREATE TABLE kk_probe (v int)"))
            await conn.execute(
                text(
                    "CREATE FUNCTION kk_probe_guard() RETURNS trigger LANGUAGE plpgsql AS"
                    " $$ BEGIN IF NEW.v < 0 THEN RAISE EXCEPTION 'kk_probe_guard fired'; END IF;"
                    " RETURN NEW; END $$"
                )
            )
            await conn.execute(
                text(
                    "CREATE TRIGGER kk_probe_guard BEFORE INSERT ON kk_probe"
                    " FOR EACH ROW EXECUTE FUNCTION kk_probe_guard()"
                )
            )
            await conn.execute(text(f'GRANT INSERT ON kk_probe TO "{runtime_role}"'))
            await conn.execute(text(f'SET ROLE "{runtime_role}"'))
            assert not (
                await conn.execute(text("SELECT has_function_privilege('kk_probe_guard()', 'EXECUTE')"))
            ).scalar()
            await conn.execute(text("INSERT INTO kk_probe VALUES (1)"))
            with pytest.raises(DBAPIError, match="kk_probe_guard fired"):
                await conn.execute(text("INSERT INTO kk_probe VALUES (-1)"))
            await conn.rollback()


async def test_guard_refuses_an_executable_security_definer_function(
    engine: AsyncEngine, runtime_role: str
) -> None:
    """A SECURITY DEFINER function runs with its owner's rights: callable by the runtime role, it is a way
    around the append-only privileges, so the API must refuse to start."""
    async with engine.connect() as conn:
        async with conn.begin():
            await grant_runtime_role(conn, runtime_role)
            await conn.execute(
                text("CREATE FUNCTION kk_definer() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'")
            )
            await conn.execute(text(f'GRANT EXECUTE ON FUNCTION kk_definer() TO "{runtime_role}"'))
            await conn.execute(text(f'SET ROLE "{runtime_role}"'))
            problems = await audit_guard_problems(conn)
            await conn.execute(text("RESET ROLE"))
            await conn.rollback()
    assert problems == [
        "runtime role can execute SECURITY DEFINER function public.kk_definer"
        " (it runs with its owner's rights)"
    ]


async def test_append_only_tables_match_the_schema(engine: AsyncEngine) -> None:
    """Every table whose trigger <table>_no_mutation rejects UPDATE/DELETE is append-only for the runtime
    role.

    A migration that adds such a table without listing it in APPEND_ONLY_TABLES would hand the runtime role
    UPDATE and DELETE on it; this test turns that into a failure.
    """
    async with engine.connect() as conn:
        guarded = (
            (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
                        " WHERE NOT t.tgisinternal AND t.tgname = c.relname || '_no_mutation' ORDER BY 1"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sorted(APPEND_ONLY_TABLES) == guarded


@pytest.mark.parametrize("role", ['x"; DROP TABLE tasks; --', "UPPER", "", "a" * 64])
async def test_role_names_are_validated(engine: AsyncEngine, role: str) -> None:
    async with engine.begin() as conn:
        with pytest.raises(InvalidRoleName):
            await grant_runtime_role(conn, role)


async def test_unknown_role(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        with pytest.raises(InvalidRoleName, match="does not exist"):
            await grant_runtime_role(conn, "kk_no_such_role")
