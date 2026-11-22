"""The server-side bounds of a bound transaction keep their order (spec §16.6.2 #86, BC-12)."""

from __future__ import annotations

from app.core import db
from app.workflow.engine import ENGINE_WRITE_TIMEOUT_S
from app.workflow.lease import LEASE_SECONDS


def test_the_idle_in_transaction_timeout_is_30_seconds() -> None:
    assert db.IDLE_IN_TRANSACTION_TIMEOUT == "30s"


def test_the_client_bound_fires_before_the_server_bound_and_that_before_the_lease_ends() -> None:
    idle_s = int(db.IDLE_IN_TRANSACTION_TIMEOUT.removesuffix("s"))
    assert ENGINE_WRITE_TIMEOUT_S < idle_s < LEASE_SECONDS
