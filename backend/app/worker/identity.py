"""Lease owner ids (tasks.lease_owner): the worker process id and the per-delivery owner built on it."""

from __future__ import annotations

import os
import socket
from secrets import token_hex

from app.workflow.lease import LEASE_OWNER_LENGTH

_DELIVERY_SUFFIX_LENGTH = 9  # ":" + 8 hex characters
_cached: tuple[int, str] | None = None


def worker_id() -> str:
    """Stable within one process, new in every process.

    Keyed by pid, not cached at import: Celery's prefork pool forks after import, and children that
    inherited one value would share a lease owner.
    """
    global _cached
    pid = os.getpid()
    if _cached is None or _cached[0] != pid:
        token = token_hex(4)
        suffix = f":{pid}:{token}"
        host = socket.gethostname()[: LEASE_OWNER_LENGTH - _DELIVERY_SUFFIX_LENGTH - len(suffix)] or "host"
        _cached = (pid, f"{host}{suffix}")
    return _cached[1]


def delivery_owner() -> str:
    """A new lease owner per delivery: two deliveries in one process must not share one, or the first
    could not be fenced off once the second claimed the task. Fits ``LEASE_OWNER_LENGTH`` by construction."""
    return f"{worker_id()}:{token_hex(4)}"
