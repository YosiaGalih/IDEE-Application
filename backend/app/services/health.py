"""Liveness and readiness (FR-149, API_CONTRACTS §9).

A check reports only ``"ok"`` or ``"unavailable"``. Exception text never leaves this module, because driver
errors can contain DSNs with passwords.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.db import make_engine, make_sessionmaker
from app.core.logging import get_logger

ReadinessCheck = Callable[[], Awaitable[bool]]

CHECK_TIMEOUT_SECONDS = 2.0
REDIS_TIMEOUT_SECONDS = 2.0
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

logger = get_logger(__name__)


def alembic_heads() -> set[str]:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


@dataclass
class Dependencies:
    """Clients the API process owns. Created lazily-connecting; closed on shutdown."""

    engine: AsyncEngine
    redis: Redis
    http: httpx.AsyncClient
    sandbox_manager_url: str
    migration_heads: frozenset[str]
    sessions: async_sessionmaker[AsyncSession]

    @classmethod
    def from_settings(cls, settings: Settings) -> Dependencies:
        engine = make_engine(settings.database_url)
        return cls(
            engine=engine,
            # Explicit timeouts: a Redis that accepts but never answers must fail fast (rate limiting fails
            # closed on it), whatever the redis-py default is.
            redis=Redis.from_url(
                settings.redis_url,
                socket_timeout=REDIS_TIMEOUT_SECONDS,
                socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            ),
            http=httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS),
            sandbox_manager_url=settings.sandbox_manager_url.rstrip("/"),
            # Read once at startup: building ScriptDirectory imports every revision module (blocking I/O).
            migration_heads=frozenset(alembic_heads()),
            sessions=make_sessionmaker(engine),
        )

    async def aclose(self) -> None:
        try:
            await self.http.aclose()
        finally:
            try:
                await self.redis.aclose()
            finally:
                await self.engine.dispose()

    async def check_db(self) -> bool:
        async with self.engine.connect() as conn:
            return (await conn.execute(text("SELECT 1"))).scalar_one() == 1

    async def check_redis(self) -> bool:
        return bool(await self.redis.ping())

    async def check_sandbox_manager(self) -> bool:
        response = await self.http.get(f"{self.sandbox_manager_url}/healthz")
        return response.status_code == 200

    async def check_migrations(self) -> bool:
        async with self.engine.connect() as conn:
            table = (await conn.execute(text("SELECT to_regclass('alembic_version')"))).scalar_one()
            if table is None:
                return not self.migration_heads  # fine only when there is nothing to apply yet
            rows = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current = {row[0] for row in rows}
        return current == self.migration_heads

    def readiness_checks(self) -> dict[str, ReadinessCheck]:
        return {
            "db": self.check_db,
            "redis": self.check_redis,
            "sandbox_manager": self.check_sandbox_manager,
            "migrations": self.check_migrations,
        }


async def _run_check(name: str, check: ReadinessCheck) -> str:
    try:
        ok = await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # any failure means "not ready"; only the type is logged
        # The message passes through the redactor (DSNs carry passwords); it never reaches the response.
        logger.warning("readiness_check_failed", check=name, error_type=type(exc).__name__, error=str(exc))
        return "unavailable"
    return "ok" if ok else "unavailable"


async def readiness(checks: Mapping[str, ReadinessCheck]) -> dict[str, str]:
    results = await asyncio.gather(*(_run_check(name, check) for name, check in checks.items()))
    return dict(zip(checks, results, strict=True))
