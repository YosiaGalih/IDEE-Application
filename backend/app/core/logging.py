"""Structured JSON logging with mandatory redaction (ARCHITECTURE §11, SECURITY_MODEL §6.2).

structlog output and stdlib logging (uvicorn error/access logs, library loggers) both pass through the same
redactor, so a traceback or a query string can never print a secret.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any

import structlog

from app.core.context import request_id_var
from app.core.redaction import Redactor

_redactor = Redactor()
_STDLIB_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access")


def _add_request_id(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    request_id = request_id_var.get()
    if request_id is not None and "request_id" not in event_dict:
        event_dict["request_id"] = request_id
    return event_dict


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    return _redactor.redact_obj(dict(event_dict), stringify=True)


class RedactingFilter(logging.Filter):
    """Formats the record's message and traceback, then redacts them before any handler writes."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redactor.redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = _redactor.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = _redactor.redact(record.stack_info)
        return True


class _JsonFormatter(logging.Formatter):
    """Same JSON shape as structlog output, for stdlib records (already redacted by ``RedactingFilter``)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "event": record.getMessage(),
            "logger": record.name,
            "level": record.levelname.lower(),
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        if record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


class _CurrentStdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes JSON to whatever ``sys.stdout`` is at emit time."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(_JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def _configure_stdlib(level: int) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(handler, _CurrentStdoutHandler) for handler in root.handlers):
        root.addHandler(_CurrentStdoutHandler())
    for name in _STDLIB_LOGGERS:
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, RedactingFilter) for f in handler.filters):
                handler.addFilter(RedactingFilter())


def configure_logging(level: str, redactor: Redactor) -> None:
    """Configure structlog and stdlib logging; the redactor carries the configured secret literals."""
    global _redactor
    _redactor = redactor
    numeric_level = logging.getLevelNamesMapping()[level]
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
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        # No fixed file: each call writes to the *current* sys.stdout (safe under stream swaps/capture).
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _configure_stdlib(numeric_level)


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
