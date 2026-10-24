"""Database engine and session factory (PostgreSQL is the source of truth, CLAUDE.md #4)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(database_url: str) -> AsyncEngine:
    # hide_parameters: a DB error must not echo bind values (password hashes, token hashes) into
    # exception text, tracebacks or logs (SECURITY_MODEL §6.2).
    return create_async_engine(database_url, pool_pre_ping=True, hide_parameters=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
