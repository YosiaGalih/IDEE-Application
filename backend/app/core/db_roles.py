"""Privileges of the runtime database role (T10, DATA_MODEL; WP-1.5 deployment).

Migrations run as the owner of the tables; api, worker and scheduler connect as a separate runtime role.
After every migration, ``grant_runtime_role`` gives that role what the application needs and nothing that
would let it rewrite history:
- every table: SELECT, INSERT, UPDATE, DELETE;
- the tables in ``APPEND_ONLY_TABLES``: SELECT, INSERT only (the API refuses to start otherwise,
  ``app.main``);
- ``alembic_version``: SELECT only (readiness compares it with the migration heads);
- every sequence: USAGE, SELECT;
- functions in ``public``: no EXECUTE for PUBLIC, now or for functions later migrations add. Triggers still
  fire (PostgreSQL checks EXECUTE when a trigger is created, not when it runs); a SECURITY DEFINER function
  would run with its owner's rights, so one must be granted on purpose and the API's guard refuses it.

It is idempotent and covers tables added by later migrations, so the deployment runs it after each
``alembic upgrade head``.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_ROLE = re.compile(r"[a-z_][a-z0-9_]{0,62}")

# Tables whose trigger ``<table>_no_mutation`` rejects UPDATE/DELETE (DATA_MODEL). tests/integration/
# test_db_roles.py compares this list with the triggers in the schema, so a new one cannot be forgotten.
APPEND_ONLY_TABLES = ("audit_events",)


class InvalidRoleName(ValueError):
    pass


async def grant_runtime_role(conn: AsyncConnection, role: str) -> None:
    if not _ROLE.fullmatch(role):
        raise InvalidRoleName("role names are lower-case letters, digits and underscores")
    exists = (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).scalar()
    if exists is None:
        raise InvalidRoleName(f"role {role} does not exist")
    quoted = f'"{role}"'  # validated above: no quotes possible
    for statement in (
        f"GRANT USAGE ON SCHEMA public TO {quoted}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {quoted}",
        *(
            f"REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON {t} FROM {quoted}"
            for t in APPEND_ONLY_TABLES
        ),
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON alembic_version FROM {quoted}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted}",
        "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC",
        # global: EXECUTE for PUBLIC is a global default, which a per-schema REVOKE cannot remove
        "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
    ):
        # DDL cannot bind identifiers; the role passed _ROLE and exists, the rest is constant (EX-05).
        # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text (EX-05)
        await conn.execute(text(statement))
