"""Database engine and session factory (PostgreSQL is the source of truth, CLAUDE.md #4)."""

from __future__ import annotations

from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# A write on the agent-run path gives up on a lock and on a statement, as claim_lease does (reviews of
# 42bcb2e and 073e68d): a lock held by another transaction never stalls a run.
LOCK_TIMEOUT: Final = "5s"
STATEMENT_TIMEOUT: Final = "10s"
# The server ends a session that sits idle this long inside a transaction, and the row locks it holds go with
# it. Order kept by tests/unit/test_db_bounds.py: ENGINE_WRITE_TIMEOUT_S (15 s) < 30 s < LEASE_SECONDS (60 s).
IDLE_IN_TRANSACTION_TIMEOUT: Final = "30s"


def make_engine(database_url: str) -> AsyncEngine:
    # hide_parameters: a DB error must not echo bind values (password hashes, token hashes) into
    # exception text, tracebacks or logs (SECURITY_MODEL §6.2).
    return create_async_engine(database_url, pool_pre_ping=True, hide_parameters=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def bound_transaction(session: AsyncSession) -> None:
    """``SET LOCAL`` the lock, statement and idle-in-transaction timeouts of the transaction that starts here.

    The third bound is the server's, for a client that goes silent (a partition, a frozen process): after
    ``IDLE_IN_TRANSACTION_TIMEOUT`` without a statement the server terminates the **session** and the row
    locks of its transaction are released, so a lease-guarded write never pins a row for the lifetime of a
    dead connection (spec §16.6.2 #86). No legitimate caller idles that long inside the transaction (BC-13).

    A transaction already in the "idle in transaction (aborted)" state is not covered: a local setting
    reverts to its default when the transaction aborts. That is harmless, because the locks were released at
    the error (BC-12, probed on PostgreSQL 16.15). Publishing always follows the commit, never this window.
    """
    await session.execute(
        select(
            func.set_config("lock_timeout", LOCK_TIMEOUT, True),
            func.set_config("statement_timeout", STATEMENT_TIMEOUT, True),
            func.set_config("idle_in_transaction_session_timeout", IDLE_IN_TRANSACTION_TIMEOUT, True),
        )
    )
