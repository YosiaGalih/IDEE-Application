"""Secret redaction for every outbound path (SECURITY_MODEL §6.2, ADR-030, T12, M.2).

Two layers:
- text patterns (token prefixes, PEM, URL credentials, headers, ``key=value``), plus configured literals;
- structure: a value stored under a sensitive key name is dropped whole, whatever it looks like.

Order matters: the labelled Anthropic admin-key pattern runs before the generic ``sk-ant-`` pattern so it
stays covered even if the generic pattern is narrowed later.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"

# A key is sensitive when its *last* segment(s) name a secret: GITHUB_TOKEN, client_secret, api-key.
# "token_count", "max_tokens", "secret_santa" and "password_policy" stay readable.
_SENSITIVE_TAIL = (
    r"password|passwd|passphrase|secret|secret_key|token|api_key|apikey|access_key|private_key"
    r"|ticket|credential|credentials|authorization|cookie|set_cookie|session"
)
_SENSITIVE_KEY_NAME = re.compile(rf"(?i)^(?:[a-z0-9]+[_-])*(?:{_SENSITIVE_TAIL})$")

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-admin\d{0,2}-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    # PEM blocks, including a truncated block with no END marker.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*\Z)",
        re.DOTALL,
    ),
)

# scheme://user:pass@host and scheme://<token-like userinfo>@host → scheme://[REDACTED]@host
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?:[^/\s:@]*:[^/\s@]+|[^/\s:@]{8,})@")

# Header-like pairs anywhere in a line, also inside JSON/repr: Authorization: Bearer x / "x-api-key": "y".
_HEADERS = re.compile(
    r"""(?ix)
    (?P<name>(?<![A-Za-z0-9_-])(?:proxy-authorization|authorization|set-cookie|cookie|x-api-key))
    (?P<sep>\\?["']?\s*:\s*\\?["']?)
    (?P<value>[^"'\\\r\n]+)
    """
)

# key=value / key: value / "key": "value" / \"key\": \"value\" / ?key=value, compound names included.
_KEY_VALUE = re.compile(
    rf"""(?ix)
    (?P<key>(?<![A-Za-z0-9_-])(?:[a-z0-9]+[_-])*(?:{_SENSITIVE_TAIL})(?![A-Za-z0-9_-]))
    (?P<sep>\\?["']?\s*[:=]\s*)
    (?P<value>
        \\"[^"\\]*\\"            # \"escaped json\"
      | "[^"]*"                  # "double quoted"
      | '[^']*'                  # 'single quoted'
      | (?:(?:bearer|basic|token)\s+)?[^\s"'&;,\\]+
    )
    """
)

# .netrc: "... password <value>"
_NETRC = re.compile(r"(?i)(?<![A-Za-z0-9_])(?P<key>password)(?P<sep>\s+)(?P<value>\S+)")

_MIN_LITERAL_LENGTH = 8
_PRIMITIVES = (str, int, float, bool, type(None))


def is_sensitive_key(name: str) -> bool:
    return bool(_SENSITIVE_KEY_NAME.match(name))


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
        text = _HEADERS.sub(rf"\g<name>\g<sep>{REDACTED}", text)
        text = _KEY_VALUE.sub(rf"\g<key>\g<sep>{REDACTED}", text)
        return _NETRC.sub(rf"\g<key>\g<sep>{REDACTED}", text)

    def redact_obj(self, obj: Any, *, stringify: bool = False) -> Any:
        """Redact strings inside containers and drop values under sensitive keys.

        With ``stringify=True`` (logging), any other object is rendered with ``repr`` first so exceptions,
        bytes and models cannot carry a secret past the redactor.
        """
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {
                key: REDACTED
                if isinstance(key, str) and is_sensitive_key(key) and obj[key] is not None
                else self.redact_obj(value, stringify=stringify)
                for key, value in obj.items()
            }
        if isinstance(obj, list):
            return [self.redact_obj(value, stringify=stringify) for value in obj]
        if isinstance(obj, tuple):
            return tuple(self.redact_obj(value, stringify=stringify) for value in obj)
        if isinstance(obj, set | frozenset):
            return type(obj)(self.redact_obj(value, stringify=stringify) for value in obj)
        if stringify and not isinstance(obj, _PRIMITIVES):
            if isinstance(obj, bytes | bytearray):
                return self.redact(bytes(obj).decode("utf-8", errors="replace"))
            return self.redact(repr(obj))
        return obj
