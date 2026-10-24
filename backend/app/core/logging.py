"""Structured JSON logging with mandatory redaction (ARCHITECTURE §11, SECURITY_MODEL §6.2)."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.core.context import request_id_var
from app.core.redaction import Redactor

_redactor = Redactor()


def _add_request_id(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    request_id = request_id_var.get()
    if request_id is not None and "request_id" not in event_dict:
        event_dict["request_id"] = request_id
    return event_dict


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    return _redactor.redact_obj(dict(event_dict))


def configure_logging(level: str, redactor: Redactor) -> None:
    """Configure structlog once per process; the redactor carries the configured secret literals."""
    global _redactor
    _redactor = redactor
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        # No fixed file: each call writes to the *current* sys.stdout (safe under stream swaps/capture).
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
