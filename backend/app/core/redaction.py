"""Secret redaction for every outbound path (SECURITY_MODEL §6.2, ADR-030, T12, M.2).

Order matters: the labelled Anthropic admin-key pattern runs before the generic ``sk-ant-`` pattern so it
stays covered even if the generic pattern is narrowed later.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = r"password|passwd|token|secret|api_key|access_key|ticket"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-admin\d{0,2}-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)

# scheme://user:pass@host → scheme://[REDACTED]@host
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@")

# Header lines: keep the header name, drop the value.
_HEADERS = re.compile(
    r"(?im)^(?P<name>\s*(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key))\s*:\s*.+$"
)

# key=value / key: value / "key": "value" / ?key=value — case-insensitive, whole key only.
_KEY_VALUE = re.compile(
    rf"""(?ix)
    (?P<key>(?<![A-Za-z0-9_])(?:{_SENSITIVE_KEYS})(?![A-Za-z0-9_]))
    (?P<sep>\s*["']?\s*[:=]\s*["']?)
    (?P<value>[^\s"'&;,]+)
    """
)

# .netrc: "... password <value>"
_NETRC = re.compile(r"(?i)(?<![A-Za-z0-9_])(?P<key>password)(?P<sep>\s+)(?P<value>\S+)")

_MIN_LITERAL_LENGTH = 8


class Redactor:
    """Removes secrets from text before it is logged, stored, streamed or sent to a provider."""

    def __init__(self, literals: Iterable[str] = ()) -> None:
        unique = {value for value in literals if value and len(value) >= _MIN_LITERAL_LENGTH}
        # Longest first so a secret that contains another secret is removed whole.
        self._literals = sorted(unique, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for literal in self._literals:
            text = text.replace(literal, REDACTED)
        for pattern in _PATTERNS:
            text = pattern.sub(REDACTED, text)
        text = _URL_CREDENTIALS.sub(rf"\g<scheme>{REDACTED}@", text)
        text = _HEADERS.sub(rf"\g<name>: {REDACTED}", text)
        text = _KEY_VALUE.sub(rf"\g<key>\g<sep>{REDACTED}", text)
        return _NETRC.sub(rf"\g<key>\g<sep>{REDACTED}", text)

    def redact_obj(self, obj: Any) -> Any:
        """Redact every string inside dicts, lists, tuples and sets; other values pass through."""
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {key: self.redact_obj(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self.redact_obj(value) for value in obj]
        if isinstance(obj, tuple):
            return tuple(self.redact_obj(value) for value in obj)
        if isinstance(obj, set | frozenset):
            return type(obj)(self.redact_obj(value) for value in obj)
        return obj
