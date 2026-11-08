"""Operator CLI (ARCHITECTURE §3).

- ``create-owner --email ... --display-name ...``: the password comes from an interactive prompt, or from
  stdin with ``--password-stdin``; never from argv.
- ``recover``: one recovery sweep.
- ``grant-runtime-role --role NAME``: privileges of the runtime DB role, run by the deployment after every
  migration (``app.core.db_roles``).
- ``seed-agents``: validate the agent registry (``AGENT_REGISTRY_PATH``) and sync it to ``agent_profiles``
  (FR-041; the API also syncs at start).
- ``claude-smoke [--json]``: the Claude readiness gate (RUNBOOK §4, ``make smoke``), run in the ``worker``
  container, the only one with ``ANTHROPIC_API_KEY``. Records the report in ``provider_configs`` and prints
  it redacted; exit 0 only when ready, 1 when not, 2 on a configuration error, 3 while another smoke runs.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import uuid
from pathlib import Path

from app.agents.registry import RegistryError, load_registry
from app.core.audit import AuditWriter
from app.core.config import ConfigError, load_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.db_roles import InvalidRoleName, grant_runtime_role
from app.core.redaction import Redactor
from app.services.agents import sync_registry
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


SMOKE_BUSY = 3


async def _claude_smoke(as_json: bool) -> int:
    from redis.asyncio import Redis
    from redis.exceptions import RedisError

    from app.services import provider_smoke, smoke_lock
    from app.services.provider_readiness import PROVIDER
    from app.worker.celery_app import configure_worker_logging

    settings = load_settings()
    configure_worker_logging(settings)  # the worker's redaction, with the key as a literal
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, socket_timeout=5.0, socket_connect_timeout=5.0)
    job_id = uuid.uuid4()
    try:
        try:
            if not await smoke_lock.acquire(redis, PROVIDER, job_id):
                print("a smoke test is already running for this provider; try again later", file=sys.stderr)
                return SMOKE_BUSY
        except RedisError as exc:
            print(f"cannot take the smoke lock: Redis {type(exc).__name__}", file=sys.stderr)
            return 2
        env = provider_smoke.SmokeEnv(
            settings=settings,
            sessions=make_sessionmaker(engine),
            audit=AuditWriter(Redactor(settings.secret_values())),
        )
        try:
            report = await provider_smoke.run_smoke(env, actor_id="cli", job_id=job_id)
        finally:
            await smoke_lock.release(redis, PROVIDER, job_id)
    finally:
        await redis.aclose()
        await engine.dispose()
    print(json.dumps(report.to_dict(), ensure_ascii=False) if as_json else provider_smoke.render(report))
    return 0 if report.ready else 1


async def _seed_agents() -> int:
    settings = load_settings()
    registry = load_registry(Path(settings.agent_registry_path))
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            result = await sync_registry(session, registry, AuditWriter(Redactor(settings.secret_values())))
            await session.commit()
    finally:
        await engine.dispose()
    print(
        f"agent registry synced: inserted={result.inserted} updated={result.updated} "
        f"retired={result.retired} unchanged={result.unchanged}"
    )
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
    commands.add_parser("seed-agents", help="validate the agent registry and sync it to the database")
    smoke = commands.add_parser("claude-smoke", help="Claude readiness gate, in the worker (RUNBOOK §4)")
    smoke.add_argument("--json", action="store_true", help="print the report as JSON (API §11 #8 shape)")
    args = parser.parse_args(argv)
    if args.command == "claude-smoke":
        try:
            return asyncio.run(_claude_smoke(args.json))
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.command == "seed-agents":
        try:
            return asyncio.run(_seed_agents())
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except RegistryError as exc:
            print("agent registry invalid:", file=sys.stderr)
            for problem in exc.problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
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
