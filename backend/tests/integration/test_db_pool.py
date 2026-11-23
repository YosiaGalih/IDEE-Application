"""A pool record whose terminate was cancelled keeps its closed connection; a checkout pre-ping replaces it.

SQLAlchemy 2.1 ``_ConnectionRecord.invalidate`` runs ``__close(terminate=True)`` and only then clears
``dbapi_connection``. ``Pool._close_connection`` re-raises a ``BaseException``, so a ``CancelledError`` that
lands while a heartbeat's invalidated connection is being terminated (the engine cancels its heartbeat at
every step end) leaves the closed asyncpg connection in the record. Without a pre-ping the next checkout
hands it out and the statement fails with "connection is closed". Production ``make_engine`` pings; the
shared test ``engine`` fixture must too (it did not, and an engine test failed once in a full suite).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import make_engine


async def leave_a_record_whose_terminate_was_cancelled(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialect = engine.sync_engine.dialect

    def cancelled_terminate(dbapi_connection: Any) -> None:
        dbapi_connection._connection.terminate()  # the socket is gone, as after the shielded close
        raise asyncio.CancelledError  # what the cancelled shield raises inside await_()

    with pytest.raises(asyncio.CancelledError):
        async with engine.connect() as conn:
            monkeypatch.setattr(dialect, "do_terminate", cancelled_terminate)
            try:
                await conn.invalidate()
            finally:
                monkeypatch.undo()


@pytest.mark.parametrize("which", ["make_engine", "fixture"])
async def test_a_record_left_by_a_cancelled_terminate_is_replaced_on_checkout(
    which: str, engine: AsyncEngine, migrated_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = make_engine(migrated_database) if which == "make_engine" else engine
    try:
        await leave_a_record_whose_terminate_was_cancelled(target, monkeypatch)
        async with target.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar() == 1
    finally:
        if which == "make_engine":
            await target.dispose()
