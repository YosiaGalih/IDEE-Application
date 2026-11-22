"""Lease owner revocation (spec WP-3.2 §16.6.2 #84): a revoked owner is refused before any SQL."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.workflow.lease import LeaseLost, renew_lease, revoke_lease_owner


class NoSqlDb:
    """A session whose every statement fails the test: a revoked owner must never reach the database."""

    async def execute(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("no SQL for a revoked owner")


async def test_a_revoked_owner_is_refused_with_no_sql_and_no_owner_in_the_message() -> None:
    revoke_lease_owner("w-x")
    with pytest.raises(LeaseLost) as caught:
        await renew_lease(NoSqlDb(), uuid.uuid4(), "w-x")  # type: ignore[arg-type]
    assert "w-x" not in str(caught.value)


async def test_another_owner_still_reaches_the_database() -> None:
    revoke_lease_owner("w-x")
    with pytest.raises(AssertionError, match="no SQL for a revoked owner"):
        await renew_lease(NoSqlDb(), uuid.uuid4(), "w-y")  # type: ignore[arg-type]


@pytest.mark.parametrize("owner", ["", "w" * 121])
def test_an_unusable_owner_cannot_be_revoked(owner: str) -> None:
    with pytest.raises(ValueError, match="worker id"):
        revoke_lease_owner(owner)
