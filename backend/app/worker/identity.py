"""Lease owner id for this worker process (tasks.lease_owner, at most 120 characters)."""

from __future__ import annotations

import os
import socket
from secrets import token_hex

_MAX_LENGTH = 120
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
        host = socket.gethostname()[: _MAX_LENGTH - len(suffix)] or "host"
        _cached = (pid, f"{host}{suffix}")
    return _cached[1]
