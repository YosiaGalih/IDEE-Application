"""Lease owner revocation (spec WP-3.2 §16.6.2 #84): a revoked owner is refused before any SQL."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.workflow.lease import ClaimResult, LeaseLost, claim_lease, renew_lease, revoke_lease_owner


class NoSqlDb:
    """A session whose every statement fails the test: a revoked owner must never reach the database."""

    async def execute(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("no SQL for a revoked owner")


async def test_a_revoked_owner_is_refused_with_no_sql_and_no_owner_in_the_message() -> None:
    revoke_lease_owner("w-x")
    with pytest.raises(LeaseLost) as caught:
        await renew_lease(NoSqlDb(), uuid.uuid4(), "w-x")  # type: ignore[arg-type]
    assert "w-x" not in str(caught.value)


class SpyDb:
    """A session that records every attribute the code under test touches; a revoked owner touches none."""

    def __init__(self) -> None:
        self.touched: list[str] = []

    def __getattr__(self, name: str) -> Any:
        self.touched.append(name)
        raise AssertionError(f"no database access for a revoked owner: {name}")


async def test_a_revoked_owner_cannot_claim_and_no_statement_runs() -> None:
    """AG-13 INFO-1b: a claim with a revoked owner is HELD before any SQL (an owner is unique per delivery,
    and the engine claims before it revokes, so a legitimate claim never meets this)."""
    revoke_lease_owner("w-claim")
    db = SpyDb()
    result = await claim_lease(db, uuid.uuid4(), "w-claim", max_active=5)  # type: ignore[arg-type]
    assert result is ClaimResult.HELD
    assert db.touched == []


async def test_another_owner_still_reaches_the_database() -> None:
    revoke_lease_owner("w-x")
    with pytest.raises(AssertionError, match="no SQL for a revoked owner"):
        await renew_lease(NoSqlDb(), uuid.uuid4(), "w-y")  # type: ignore[arg-type]


@pytest.mark.parametrize("owner", ["", "w" * 121])
def test_an_unusable_owner_cannot_be_revoked(owner: str) -> None:
    with pytest.raises(ValueError, match="worker id"):
        revoke_lease_owner(owner)
