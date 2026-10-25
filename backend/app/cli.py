"""Operator CLI (ARCHITECTURE §3).

- ``create-owner --email ... --display-name ...``: the password comes from an interactive prompt, or from
  stdin with ``--password-stdin``; never from argv.
- ``recover``: one recovery sweep.
- ``grant-runtime-role --role NAME``: privileges of the runtime DB role, run by the deployment after every
  migration (``app.core.db_roles``).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.audit import AuditWriter
from app.core.config import ConfigError, load_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.db_roles import InvalidRoleName, grant_runtime_role
from app.core.redaction import Redactor
from app.services.users import EmailTakenError, OwnerExistsError, create_owner


async def _create_owner(args: argparse.Namespace, password: str) -> int:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    try:
        user_id = await create_owner(
            make_sessionmaker(engine),
            AuditWriter(Redactor(settings.secret_values())),
            email=args.email,
            display_name=args.display_name,
            password=password,
        )
    finally:
        await engine.dispose()
    print(f"owner created: {user_id}")
    return 0


def _recover() -> int:
    from app.worker.celery_app import configure_worker_logging
    from app.worker.jobs import run_recovery_sweep
    from app.workflow.recovery import RecoveryIncomplete

    configure_worker_logging(load_settings())
    try:
        counts = asyncio.run(run_recovery_sweep())
    except RecoveryIncomplete as exc:
        counts = dict(exc.result.__dict__)
    print(", ".join(f"{name}: {counts[name]}" for name in ("candidates", "recovered", "failed", "skipped")))
    return 0 if counts["failed"] == 0 else 1


async def _grant_runtime_role(role: str) -> int:
    engine = make_engine(load_settings().database_url)
    try:
        async with engine.begin() as conn:
            await grant_runtime_role(conn, role)
    finally:
        await engine.dispose()
    print(f"runtime role {role}: privileges granted (audit_events append-only)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    owner = commands.add_parser("create-owner", help="create the single global owner (ADR-002)")
    owner.add_argument("--email", required=True)
    owner.add_argument("--display-name", required=True)
    owner.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    commands.add_parser("recover", help="run the task recovery sweep once (RUNBOOK)")
    grant = commands.add_parser(
        "grant-runtime-role", help="privileges of the runtime DB role (after migrations)"
    )
    grant.add_argument("--role", required=True)
    args = parser.parse_args(argv)
    if args.command == "grant-runtime-role":
        try:
            return asyncio.run(_grant_runtime_role(args.role))
        except (ConfigError, InvalidRoleName) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.command == "recover":
        try:
            return _recover()
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    password = sys.stdin.readline().rstrip("\n") if args.password_stdin else getpass.getpass("Password: ")
    try:
        return asyncio.run(_create_owner(args, password))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2
    except (OwnerExistsError, EmailTakenError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
