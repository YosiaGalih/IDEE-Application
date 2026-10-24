"""Per-request context shared with logging."""

from __future__ import annotations

import re
import secrets
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"
_CLIENT_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return f"req_{secrets.token_hex(12)}"


def accept_request_id(candidate: str | None) -> str:
    """Use the client's ``X-Request-ID`` only when it matches the contract pattern (API_CONTRACTS §1)."""
    if candidate and _CLIENT_REQUEST_ID.fullmatch(candidate):
        return candidate
    return new_request_id()
