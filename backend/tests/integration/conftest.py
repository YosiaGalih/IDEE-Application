"""Integration tests run against a real PostgreSQL migrated to head (TEST_STRATEGY §2.2).

The database fixtures live in ``tests/conftest.py`` so ``tests/security`` can share them.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _database(migrated_database: str) -> str:
    return migrated_database
