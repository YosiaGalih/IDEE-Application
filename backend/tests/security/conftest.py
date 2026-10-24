"""Security regressions that need the real database (TEST_STRATEGY §1 rule 5, §2)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture(scope="session", autouse=True)
def _database(migrated_database: str) -> str:
    return migrated_database


@pytest.fixture(autouse=True)
async def clean_identity(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM users"))
