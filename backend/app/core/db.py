"""Database engine and session factory (PostgreSQL is the source of truth, CLAUDE.md #4)."""

from __future__ import annotations

from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# A write on the agent-run path gives up on a lock and on a statement, as claim_lease does (reviews of
# 42bcb2e and 073e68d): a lock held by another transaction never stalls a run.
LOCK_TIMEOUT: Final = "5s"
STATEMENT_TIMEOUT: Final = "10s"


def make_engine(database_url: str) -> AsyncEngine:
    # hide_parameters: a DB error must not echo bind values (password hashes, token hashes) into
    # exception text, tracebacks or logs (SECURITY_MODEL §6.2).
    return create_async_engine(database_url, pool_pre_ping=True, hide_parameters=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def bound_transaction(session: AsyncSession) -> None:
    """``SET LOCAL lock_timeout`` and ``statement_timeout`` for the transaction that starts here."""
    await session.execute(
        select(
            func.set_config("lock_timeout", LOCK_TIMEOUT, True),
            func.set_config("statement_timeout", STATEMENT_TIMEOUT, True),
        )
    )
