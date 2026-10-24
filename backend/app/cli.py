"""Operator CLI: ``python -m app.cli create-owner --email ... --display-name ...`` (ARCHITECTURE §3).

The password is read from an interactive prompt, or from stdin with ``--password-stdin``; never from argv.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.audit import AuditWriter
from app.core.config import ConfigError, load_settings
from app.core.db import make_engine, make_sessionmaker
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
    from app.worker.jobs import run_recovery_sweep

    counts = asyncio.run(run_recovery_sweep())
    print(f"recovered: {counts['recovered']}, failed: {counts['failed']}")
    return 0 if counts["failed"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    owner = commands.add_parser("create-owner", help="create the single global owner (ADR-002)")
    owner.add_argument("--email", required=True)
    owner.add_argument("--display-name", required=True)
    owner.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    commands.add_parser("recover", help="run the task recovery sweep once (RUNBOOK)")
    args = parser.parse_args(argv)
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
