"""Secret redaction for every outbound path (SECURITY_MODEL §6.2, ADR-030, T12, M.2).

Two layers:
- text patterns (token prefixes, PEM, URL credentials, headers, ``key=value``), plus configured literals;
- structure: a value stored under a sensitive key name is dropped whole, whatever it looks like.

Order matters: the labelled Anthropic admin-key pattern runs before the generic ``sk-ant-`` pattern so it
stays covered even if the generic pattern is narrowed later.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

REDACTED = "[REDACTED]"

# A key is sensitive when its *last* segment(s) name a secret: GITHUB_TOKEN, client_secret, api-key. Public:
# the agent runtime's delta stream holds a quoted value back after the same keys (app.agents.runtime).
# "token_count", "max_tokens", "secret_santa" and "password_policy" stay readable.
SENSITIVE_KEY_TAIL = (
    r"passwords?|passwd|passphrase|secrets?|secret_key|token|api_key|apikey|access_key|private_key"
    r"|ticket|credentials?|authorization|cookies?|set_cookie|session|jwt|bearer"
)
_SENSITIVE_KEY_NAME = re.compile(rf"(?i)^(?:[a-z0-9]+[_-])*(?:{SENSITIVE_KEY_TAIL})$")


def _capitalised(word: str) -> str:
    return word[0].upper() + word[1:]  # "secrets?" -> "Secrets?": a tail word is letters and an optional "s?"


def _camel_rest(words: list[str], *, after_lower: bool) -> str:
    """The tail words after the first, joined as ``is_sensitive_key`` splits them: by "_" or "-" (the rest is
    then snake, any case), or by a hump. A Capitalised word starts a word after any letter; an UPPER word only
    after a lower-case letter (secretKEY splits, SECRETKEY does not)."""
    if not words:
        return ""
    word, *more = words
    joints = [rf"[_-](?i:{'[_-]'.join(words)})", _capitalised(word) + _camel_rest(more, after_lower=True)]
    if after_lower:
        joints.append(word.upper() + _camel_rest(more, after_lower=False))
    return "(?:" + "|".join(joints) + ")"


def _secret_key_pattern(tails: Iterable[str]) -> str:
    """One sensitive key name, generated from the tails so a non-secret name never matches (Lead decision
    3.1f2c #2): a pattern for any identifier would swallow ``note="password=v"`` whole, secret and all.

    ``is_sensitive_key`` drops a private name's leading underscores, splits the rest into words at "_",
    "-" and humps (``_CAMEL_BOUNDARY``) and asks whether the last words spell a tail. The same name in free
    text, after any leading underscores (``self._password``, ``__secret``):
    - snake, kebab or UPPER: the tail starts the name or follows "_"/"-", its words joined by "_" or "-",
      in any case (``db_password``, ``x-api-key``, ``SECRET_KEY``);
    - camel and Pascal: the tail starts where a hump starts a word. A Capitalised tail follows any letter
      or digit (``clientSecret``, ``DBPassword``); an UPPER tail only a lower-case letter or digit
      (``dbPASSWORD``; ``DBPASSWORD`` has no hump); a lower-case tail only the start of a word
      (``secretKey``).

    The camel part is case-sensitive whatever the flags of the pattern it is embedded in: a hump is a case
    change. The snake part is case-insensitive, so it also takes a mixed-case word (``PassWord``) that
    ``is_sensitive_key`` splits: free text redacts more there, never less. No boundary lookaround: the
    caller anchors the name (``_KEY_VALUE``, the delta stream's key check). Every alternative is a fixed
    word after a run of ``[A-Za-z0-9]``, so a match tried from one start is linear in the name.
    """
    snake: list[str] = []
    lower: list[str] = []
    upper: list[str] = []
    capital: list[str] = []
    for tail in tails:
        first, *more = tail.split("_")
        snake.append("[_-]".join([first, *more]))
        if more:  # a lower-case word alone is snake; it needs a hump inside the tail (secretKey)
            lower.append(first + _camel_rest(more, after_lower=True))
        upper.append(first.upper() + _camel_rest(more, after_lower=False))
        capital.append(_capitalised(first) + _camel_rest(more, after_lower=True))
    return (
        r"_*"  # a private name's leading underscores; the words start with a letter or digit, so no overlap
        r"(?:[A-Za-z0-9]+[_-])*"  # the words before the tail's own segment
        rf"(?:(?i:{'|'.join(snake)})"
        rf"|(?-i:(?:{'|'.join(lower)})"
        rf"|(?:[A-Za-z0-9]*[a-z0-9])?(?:{'|'.join(upper)})"
        rf"|[A-Za-z0-9]*(?:{'|'.join(capital)})))"
    )


# One sensitive key name, the free-text form of ``is_sensitive_key``: ``_KEY_VALUE`` here, the delta stream's
# key check in app.agents.runtime. A regex source with no boundary lookaround; the caller adds the boundaries.
SECRET_KEY_PATTERN = _secret_key_pattern(SENSITIVE_KEY_TAIL.split("|"))

# Named so ``Redactor.find`` (security.scan_secrets) reports which rule matched, from the very same patterns.
_NAMED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_admin_key", re.compile(r"sk-ant-admin\d{0,2}-[A-Za-z0-9_\-]{8,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    # Starts only where a token starts: a match tried from every "eyJ" of a long run was quadratic (256 KiB of
    # "eyJ" took 17 s; AG-13 WP-3.1d2a MEDIUM-2). One "-" that itself follows no token character also counts
    # as a start: git.diff prints a removed line as "-" + text (/code-review WP-3.1d2a spec HIGH).
    (
        "jwt",
        re.compile(
            r"(?:(?<![A-Za-z0-9_-])|(?<=^-)|(?<=[^A-Za-z0-9_-]-))"
            r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{10,}"
        ),
    ),
    ("password_hash", re.compile(r"\$argon2(?:id|i|d)\$[A-Za-z0-9+/=,$]+")),  # PHC string
    # PEM blocks, including a truncated block with no END marker.
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*\Z)",
            re.DOTALL,
        ),
    ),
)
_PATTERNS: tuple[re.Pattern[str], ...] = tuple(pattern for _, pattern in _NAMED_PATTERNS)

# scheme://user:pass@host and scheme://<token-like userinfo>@host → scheme://[REDACTED]@host
# The scheme is at most 32 characters: an unbounded one was retried from every position of a long run of
# letters (64 KB of "a" took 2.8 s; WP-3.1d1, tool outputs). No lookbehind: a URL right after "+", "-", ".",
# a digit or other letters (a diff line, a list) must still be redacted (AG-13, WP-3.1d1).
_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]{0,31}://)(?:[^/\s:@]*:[^/\s@]+|[^/\s:@]{8,})@"
)

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
    (?P<key>(?<![A-Za-z0-9_-])(?:{SECRET_KEY_PATTERN})(?![A-Za-z0-9_-]))
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


# Where a hump starts a word: after a lower-case letter or digit (clientSecret), and at the last capital of an
# acronym before a word (SMTPPassword -> SMTP_Password; Lead decision 3.1f2c #14).
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def is_sensitive_key(name: str) -> bool:
    """``accessToken``, ``x-api-key``, ``SMTPPassword``, ``_password`` and ``CLIENT_SECRET`` all normalise
    to a sensitive snake_case tail. A private name's leading underscores are not part of the name
    (``self._password``). Free text matches the same names with ``SECRET_KEY_PATTERN``."""
    normalised = _CAMEL_BOUNDARY.sub("_", name.lstrip("_")).replace("-", "_")
    return bool(_SENSITIVE_KEY_NAME.match(normalised))


@dataclass(frozen=True, slots=True)
class Finding:
    """Where a secret-looking value is, never the value: ``line`` is 1-based."""

    line: int
    rule: str


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

    def find(self, text: str) -> list[Finding]:
        """What ``redact`` would remove, as (line, rule), in text order; one finding per rule and line. The
        value itself is never returned (security.scan_secrets, SECURITY_MODEL T5)."""
        starts = [0, *(m.end() for m in re.finditer("\n", text))]
        rules: list[tuple[str, re.Pattern[str]]] = [
            *((f"literal_{index}", re.compile(re.escape(lit))) for index, lit in enumerate(self._literals)),
            *_NAMED_PATTERNS,
            ("url_credentials", _URL_CREDENTIALS),
            ("credential_header", _HEADERS),
            ("credential_assignment", _KEY_VALUE),
            ("credential_assignment", _NETRC),
        ]
        seen: set[tuple[int, str]] = set()
        for rule, pattern in rules:
            for match in pattern.finditer(text):
                name = "configured_secret" if rule.startswith("literal_") else rule
                seen.add((bisect.bisect_right(starts, match.start()), name))
        return [Finding(line, rule) for line, rule in sorted(seen)]

    def redact_obj(self, obj: Any, *, stringify: bool = False) -> Any:
        """Redact strings inside containers and drop values under sensitive keys.

        With ``stringify=True`` (logging), any other object is rendered with ``repr`` first so exceptions,
        bytes and models cannot carry a secret past the redactor.
        """
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {
                (self.redact(key) if isinstance(key, str) else key): REDACTED
                if isinstance(key, str) and is_sensitive_key(key) and value is not None
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
