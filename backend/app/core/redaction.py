"""Secret redaction for every outbound path (SECURITY_MODEL §6.2, ADR-030, T12, M.2).

Two layers:
- text patterns (token prefixes, PEM, URL credentials, headers, ``key=value``, command-line flags), plus
  configured literals;
- structure: a value stored under a sensitive key name is dropped whole, whatever it looks like; so is the
  string after a secret flag in a list or tuple (an argv list).

Order matters: the labelled Anthropic admin-key pattern runs before the generic ``sk-ant-`` pattern so it
stays covered even if the generic pattern is narrowed later. A token pattern can run on into the key after it
(``ghp_…password="V"``): the rule for a value after a redacted token (``_AFTER_REDACTED``) runs next.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Iterable
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

# Well-known secret environment variables whose name ends in no tail. Exact names in any case, not tails:
# "pwd" alone is the working directory in code and shells, and PGPASSFILE names a file (Lead ruling, 3.1f2c).
KNOWN_SECRET_NAMES = frozenset({"pgpassword", "mysql_pwd", "rediscli_auth"})

# The headers whose value is a credential, in the order CREDENTIAL_HEADER tries them. One list: it builds
# CREDENTIAL_HEADER, CREDENTIAL_HEADER_START and KEY_HINT, and tests/unit/test_redaction.py reads it.
CREDENTIAL_HEADER_NAMES = ("proxy-authorization", "authorization", "set-cookie", "cookie", "x-api-key")
_NETRC_KEY = "password"  # the one key _NETRC reads (KEY_HINT reads it too)


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


def _secret_key_pattern(tails: Iterable[str], names: Iterable[str]) -> str:
    """One sensitive key name, generated from the tails so a non-secret name never matches (Lead decision
    3.1f2c #2): a pattern for any identifier would swallow ``note="password=v"`` whole, secret and all.

    ``is_sensitive_key`` drops leading underscores and dashes (a private name, a command-line flag), then
    takes a well-known secret name whole, in any case (``names``: ``PGPASSWORD``). Otherwise it splits the
    rest into words at "_", "-" and humps (``_CAMEL_BOUNDARY``) and asks whether the last words spell a
    tail. The same name in free text, after any leading "_" or "-" (``self._password``, ``--api-key``):
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
    word after a run of ``[A-Za-z0-9]``, so a match tried from one start is linear in the name, and the
    leading run of "_" and "-" is possessive, so it is read once. Two lookaheads only fail early what could
    not match (tests/unit/test_redaction.py compares the pattern with and without them).
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
        # Leading "_" (self._password) and "-" (--password), possessive: every alternative after it starts
        # with a letter or digit, so a "_" or "-" given back could never match, and a long run of dashes is
        # read once (backtracking it through the alternatives cost 18 ms per 64 KiB per pattern).
        r"[_-]*+"
        # Every alternative starts with a letter or digit (any case (?i) reads, whatever the enclosing flags):
        # at any other position the pattern fails at once instead of trying every alternative.
        r"(?=(?i:[A-Za-z0-9]))"
        rf"(?:(?i:{'|'.join(sorted(names))})|"  # a well-known secret name, whole
        r"(?:[A-Za-z0-9]+[_-])*"  # the words before the tail's own segment
        rf"(?:(?i:{'|'.join(snake)})"
        # A camel or Pascal form holds a capital before any character other than a lower-case letter or
        # digit (one with none, secret_key, is a snake form on the same span, tried first), so a lower-case
        # word does not try every hump of every tail.
        rf"|(?-i:(?=[a-z0-9]*[A-Z])(?:(?:{'|'.join(lower)})"
        rf"|(?:[A-Za-z0-9]*[a-z0-9])?(?:{'|'.join(upper)})"
        rf"|[A-Za-z0-9]*(?:{'|'.join(capital)}))))"
        r")"
    )


# One sensitive key name, the free-text form of ``is_sensitive_key``: ``_KEY_VALUE`` here, the delta stream's
# key check in app.agents.runtime. A regex source with no boundary lookaround; the caller adds the boundaries.
SECRET_KEY_PATTERN = _secret_key_pattern(SENSITIVE_KEY_TAIL.split("|"), KNOWN_SECRET_NAMES)

# The characters a case-insensitive pattern takes for an ASCII letter besides its two cases (Python's re;
# tests/unit/test_redaction.py checks all of Unicode for every letter).
_CASE_FOLDS = {"i": "\u0130\u0131", "k": "\u212a", "s": "\u017f"}
# ASCII letters from the rarest in English text to the most common.
_RARE_FIRST = "zqxjkvbpygfwmucldrhsnioate"


def _hint_words() -> list[str]:
    """A word every match of the six skippable rules holds: the last word of each tail (any spelling of a
    tail holds it: secret_key, secretKey and SECRETKEY all hold "key"), the known secret names, the header
    names, the netrc key and the redaction marker. A word that holds another is left out: the other is
    found wherever it is."""
    words = {tail.split("_")[-1].removesuffix("s?") for tail in SENSITIVE_KEY_TAIL.split("|")}
    words |= {*KNOWN_SECRET_NAMES, *CREDENTIAL_HEADER_NAMES, _NETRC_KEY, "[redacted]"}
    return sorted(word for word in words if not any(other != word and other in word for other in words))


def _key_hint(words: Iterable[str]) -> re.Pattern[str]:
    """A search for any of ``words`` in the case-insensitive sense, led by a case-sensitive class so that the
    search skips every other character at C speed: a (?i) alternation is tried at every position (sre drops
    its first-character set for cased literals under IGNORECASE). Each word is found at its rarest letter
    (the lead), the rest checked behind and after it; the class holds every lead in every character (?i)
    takes for it."""
    leads: set[str] = set()
    by_lead: dict[str, list[str]] = {}
    for word in words:
        at = min(range(len(word)), key=lambda i: _RARE_FIRST.find(word[i]) % (len(_RARE_FIRST) + 1))
        lead = word[at]
        leads |= {lead, lead.upper(), *_CASE_FOLDS.get(lead, "")}
        before = f"(?<={re.escape(word[: at + 1])})" if at else ""
        by_lead.setdefault(lead, []).append(before + re.escape(word[at + 1 :]))
    # One lookbehind per lead letter, then only that letter's words.
    branches = [f"(?<={re.escape(lead)})(?:{'|'.join(alts)})" for lead, alts in sorted(by_lead.items())]
    return re.compile(f"[{''.join(map(re.escape, sorted(leads)))}](?i:{'|'.join(branches)})")


# A word every key the key-value rules read holds (SECRET_KEY_PATTERN in _KEY_VALUE, _FLAG_VALUE and
# _ARGV_VALUE; _NETRC_KEY in _NETRC), as do every header name (CREDENTIAL_HEADER) and the marker
# _AFTER_REDACTED reads, in the case-insensitive sense those rules use. A text with none of them is left as
# it is by all six rules, so ``Redactor.redact`` skips them: they cost 15 ms per 64 KiB of plain text, and
# the delta stream redacts a call's whole text up to about 100 times. Public: the delta stream's header
# check reads it too.
KEY_HINT = _key_hint(_hint_words())

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
    # as a start: git.diff prints a removed line as "-" + text (/code-review WP-3.1d2a spec HIGH). The literal
    # "eyJ" leads and the start is checked behind it (the same spans), so the search skips to each "eyJ" at C
    # speed instead of trying the lookbehinds at every position (2 ms per 64 KiB; WP-3.1f2c fix round).
    (
        "jwt",
        re.compile(
            r"eyJ(?:(?<![A-Za-z0-9_-]eyJ)|(?<=^-eyJ)|(?<=[^A-Za-z0-9_-]-eyJ))"
            r"[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{10,}"
        ),
    ),
    ("password_hash", re.compile(r"\$argon2(?:id|i|d)\$[A-Za-z0-9+/=,$]+")),  # PHC string
    # PEM and PGP blocks ("-----BEGIN PGP PRIVATE KEY BLOCK-----"), including a truncated block with no END
    # marker (Lead decision 3.1f2c #5).
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
            r"(?:.*?-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|.*\Z)",
            re.DOTALL,
        ),
    ),
)
_PATTERNS: tuple[re.Pattern[str], ...] = tuple(pattern for _, pattern in _NAMED_PATTERNS)

# scheme://user:pass@host and scheme://<token-like userinfo>@host → scheme://[REDACTED]@host
# The scheme is at most 32 characters: an unbounded one was retried from every position of a long run of
# letters (64 KB of "a" took 2.8 s; WP-3.1d1, tool outputs). No lookbehind: a URL right after "+", "-", ".",
# a digit or other letters (a diff line, a list) must still be redacted (AG-13, WP-3.1d1). The scheme is
# public: the agent runtime's delta stream holds a URL from its scheme while its userinfo may still grow.
URL_SCHEME_PATTERN = r"[A-Za-z][A-Za-z0-9+.\-]{0,31}"
_URL_CREDENTIALS = re.compile(rf"(?P<scheme>{URL_SCHEME_PATTERN}://)(?:[^/\s:@]*:[^/\s@]+|[^/\s:@]{{8,}})@")

# Header-like pairs anywhere in a line, also inside JSON/repr: Authorization: Bearer x / "x-api-key": "y".
# Without an opening quote after the colon the value is the rest of the line, quoted parts included
# (Authorization: Token token="V", Cookie: session="V"; theme=dark): a header value is a list of
# parameters, and a quote in it opens one. After an opening quote (JSON: "Authorization": "Bearer V", ...)
# the value ends at the next quote (Lead decision 3.1f2c #4).
# - A quote that opens a parameter's value (right after "=", and followed by neither a space nor a quote) is
#   read as #6 reads a key's quoted value: it may close on a later line, and one never closed takes the rest
#   of the text, so a value cut by the end of the text (max_tokens) never stays raw (D2-I2, #12; reviews of
#   50610b5 and 0733969).
# - Any other quote keeps to its line, and one its line never closes ends the value: in
#   curl -H "Authorization: Bearer V" https://api.example.com/x, and after base64 padding
#   (curl -H "Cookie: sid=abc==" https://...), it closes the shell word, so the URL stays.
# - The value otherwise stays on its line. The alternatives start on different characters, each quoted part
#   is read once, and the repeat is possessive: linear. No space is given back before the colon (it cannot
#   be one): a name before 64 KiB of line breaks is read once.
# After the colon the spaces are taken whole when a value or its opening quote can follow; otherwise they are
# given back only down to a space or tab (a value can start with one, never with a line break): the positions
# a plain \s* would try, in the same order, without trying every alternative at each of 64 KiB of line breaks.
_HEADER_NAME_SEP = (
    rf"(?P<name>(?:{'|'.join(map(re.escape, CREDENTIAL_HEADER_NAMES))}))"
    r"""(?P<sep>\\?["']?\s*+:(?:\s*+(?=[^\s\\]|\\["'])|\s*(?=[^\S\r\n]))?(?P<open>\\?["'])?)"""
)
CREDENTIAL_HEADER = re.compile(
    rf"""(?ix)
    (?<![A-Za-z0-9_-]){_HEADER_NAME_SEP}
    (?P<value>(?(open)[^"'\\\r\n]+|(?:[^"'\\\r\n]|(?<==)"(?=[^\s"])[^"]*+"|(?<==)'(?=[^\s'])[^']*+'
                                  |(?<==)(?:"(?=[^\s"])|'(?=[^\s']))(?s:.*)|"[^"\r\n]*+"|'[^'\r\n]*+')++))
    """
)
# Where a header's value starts, read from the raw text by the agent runtime's delta stream: the name and
# separator alone, with no left boundary (a superset of CREDENTIAL_HEADER's starts, whatever the redactor
# replaced before them), and no quote paired.
CREDENTIAL_HEADER_START = re.compile(rf"(?i){_HEADER_NAME_SEP}")

# The value forms below are fragments of the verbose patterns after them. Each alternative starts at a fixed
# point that a key, a flag or a redaction marker has anchored, and reads forward once: the strings are
# unambiguous (one way to split them) and possessive, so a string never closed costs one pass, never a retry.

# A quoted value, after a string prefix if any (r"…", b'…', rb"…": a bytes field in a repr), the alternatives
# in this order (Lead decisions 3.1f2c #6, #8):
# - triple quoted, one value across lines; never closed: to the end of the text;
# - \"escaped JSON\": in the JSON-encoded text, \\ followed by \" or \\ is one escape of the inner string;
# - "double" or 'single' quoted with backslash escapes ("ab\"c" is one string), closed on its own line;
# - else closed on its own line by the first quote of its kind, a backslash before it or not: single-quoted
#   shell, YAML, INI and PowerShell strings read no escapes ('C:\keys\'), and a value that read one there
#   would take the next value's opening quote, on a later line, as its close (AG-13 F-1);
# - else across lines, with backslash escapes;
# - a quote never closed: to the end of the text, like a PEM block without its END line. The rest of the
#   text holds no closing quote of that kind, so what is over-redacted is bounded.
# Each of the three line-bound tries reads at most the rest of the value's line; one that fails leaves no
# unescaped quote of its kind on that line for a later value to open with, so a line is read so at most twice.
_QUOTED = r"""
        (?:[bBfFrRuU]{1,2}(?=["']))?
        (?:
        "{3}(?s:.*?)(?:"{3}|\Z)
      | '{3}(?s:.*?)(?:'{3}|\Z)
      | \\"(?:[^\\]|\\[^"\\]|\\\\(?:[^\\]|\\[\s\S]))*+\\"
      | "(?:[^"\\\r\n]|\\[^\r\n])*+"
      | '(?:[^'\\\r\n]|\\[^\r\n])*+'
      | "[^"\r\n]*+"
      | '[^'\r\n]*+'
      | "(?:[^"\\]|\\[\s\S])*+"
      | '(?:[^'\\]|\\[\s\S])*+'
      | (?:\\"|["'])(?s:.*)
        )
"""

# An unquoted value: one word. A word that ends in ":" or "=" immediately followed, on the same line, by a
# quoted string takes the string too: a key was taken as the value of the key before it (password : password:
# "V"; Lead decision #7). Not across a line: "password=None):" then a docstring on the next line.
_BARE = rf"""
        [^\s"'&;,\\]++(?:(?<=[:=])[\ \t]*+(?:{_QUOTED})
                       |(?:(?<=[:=][bBfFrRuU])|(?<=[:=][bBfFrRuU][bBfFrRuU]))(?=["'])(?:{_QUOTED}))?
"""

# The value after a sensitive key and its separator (_KEY_VALUE, _AFTER_REDACTED): quoted or one word, after
# an auth scheme word (token: Bearer V). The scheme word and its credential share a line: "token: bearer" at
# the end of a line does not take the next line.
_VALUE = rf"""
        (?:(?:bearer|basic|token)[\ \t]++)?(?:{_QUOTED}|{_BARE})
"""

# A secret flag as one argv word: bare (it ends at a space or the end of the text) or quoted, also as escaped
# JSON. Atomic, so a word the key pattern matches in two ways (--Password: snake and Pascal) is tried once.
_SECRET_FLAG_WORD = rf"(?>\\?[\"']?(?=-)(?:{SECRET_KEY_PATTERN})(?:\\?[\"']|(?=\s|\Z)))"
_SECRET_FLAG_ITEM = rf"(?>\\?[\"'](?=-)(?:{SECRET_KEY_PATTERN})\\?[\"'])"  # a quoted list item only

# The value of a command-line flag after spaces (_FLAG_VALUE, _ARGV_VALUE): one argv word, so no scheme word
# ("--token TOKEN    The API token" keeps its help text). Never a separator: --api-key = V is _KEY_VALUE's.
# A value that is itself a secret flag is redacted and takes the word after it too: in
# "--no-password --token V" the token's own value goes with it, and "--password -MyPassword" (a secret that
# reads as a flag) is redacted, never skipped.
_ARG = rf"""
        (?![:=])
        (?:{_SECRET_FLAG_WORD}(?:[\ \t]++{_SECRET_FLAG_WORD})*+(?:[\ \t]++(?:{_QUOTED}|{_BARE}))?
          | {_QUOTED}
          | {_BARE})
"""

# An argv list item after a comma: a quoted string only. A bare word there is code that defines the flag, not
# its value: add_argument("--token", help=...), ["--api-key", api_key]. A secret flag item takes the next one.
_ITEM = rf"""
        (?:{_SECRET_FLAG_ITEM}(?:\s*+,\s*+{_SECRET_FLAG_ITEM})*+(?:\s*+,\s*+(?:{_QUOTED}))?
          | {_QUOTED})
"""

# key=value / key: value / "key": "value" / \"key\": \"value\" / ?key=value / --key=value, compound names.
_KEY_VALUE = re.compile(
    rf"""(?ix)
    (?P<key>(?<![A-Za-z0-9_-])(?:{SECRET_KEY_PATTERN})(?![A-Za-z0-9_-]))
    (?P<sep>\\?["']?\s*+[:=]\s*+)
    (?P<value>{_VALUE})
    """
)

# A value right after a redacted token: a key the token pattern ran on into (ghp_…password="V",
# sk-ant-…password=V; Lead decision #7). It reads the text with the literals, token patterns and URL
# credentials already replaced (Redactor._without_tokens).
_AFTER_REDACTED = re.compile(
    rf"""(?ix)
    {re.escape(REDACTED)}
    (?P<sep>\\?["']?\s*+[:=]\s*+)
    (?P<value>{_VALUE})
    """
)

# A command-line flag and its value after spaces or tabs: --password V, -api-key "V" (_KEY_VALUE covers
# --key=V). Only a flag: a bare "token V" is prose. The dash is a lookahead, not "-+": "-+" before the
# pattern's own "[_-]*" would split a long run of dashes in every way (quadratic). Spaces and tabs only: a
# flag at the end of a line does not take the next line.
_FLAG_VALUE = re.compile(
    rf"""(?ix)
    (?P<key>(?<![A-Za-z0-9_-])(?=-)(?:{SECRET_KEY_PATTERN})(?![A-Za-z0-9_-]))
    (?P<sep>[\ \t]++)
    (?P<value>{_ARG})
    """
)

# A quoted flag and its value: after a comma, an argv list item (["mysql", "--password", "V"], also as escaped
# JSON), a quoted string only; after spaces, a quoted shell word ("--password" "V", '--password' V), quoted or
# bare. The opening quote anchors it.
_ARGV_VALUE = re.compile(
    rf"""(?ix)
    (?P<key>(?P<quote>["'])(?=-)(?:{SECRET_KEY_PATTERN})\\?(?P=quote))
    (?P<sep>(?P<comma>\s*+,\s*+)|[\ \t]++)
    (?P<value>(?(comma){_ITEM}|{_ARG}))
    """
)

# .netrc: "... password <value>", on one line. Never a separator: password = "V" is _KEY_VALUE's (#6). A value
# that reads as a secret flag takes the word after it, as in _ARG.
_NETRC = re.compile(
    rf"""(?ix)
    (?<![A-Za-z0-9_])(?P<key>{_NETRC_KEY})
    (?P<sep>[\ \t]++)
    (?P<value>(?![:=])(?:{_SECRET_FLAG_WORD}(?:[\ \t]++{_SECRET_FLAG_WORD})*+(?:[\ \t]++\S+)?|\S+))
    """
)

_MIN_LITERAL_LENGTH = 8
_PRIMITIVES = (str, int, float, bool, type(None))


# Where a hump starts a word: after a lower-case letter or digit (clientSecret), and at the last capital of an
# acronym before a word (SMTPPassword -> SMTP_Password; Lead decision 3.1f2c #14).
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def is_sensitive_key(name: str) -> bool:
    """``accessToken``, ``x-api-key``, ``SMTPPassword``, ``_password``, ``--password`` and ``CLIENT_SECRET``
    all normalise to a sensitive snake_case tail. Leading underscores and dashes are not part of the name
    (``self._password``, ``mysql --password``). A well-known secret name with no tail (``PGPASSWORD``) is
    sensitive as a whole name. Free text matches the same names with ``SECRET_KEY_PATTERN``."""
    normalised = _CAMEL_BOUNDARY.sub("_", name.lstrip("_-")).replace("-", "_")
    return bool(_SENSITIVE_KEY_NAME.match(normalised)) or name.lstrip("_-").lower() in KNOWN_SECRET_NAMES


def _drop_value(match: re.Match[str]) -> str:
    """A match with its ``value`` group replaced: the key (or marker) and the separator before it stay. A
    function, not a ``\\g<key>`` template: ``sub`` looks a template up in a cache keyed by the pattern, and
    for these long patterns that lookup cost more than scanning a short text (a log field)."""
    return match.string[match.start() : match.start("value")] + REDACTED


# A value a rule ended at a quote: its marker, then the quote.
_STOPPED_AT_QUOTE = (REDACTED + '"', REDACTED + "'")


def stopped_at_open_quote(redacted: str) -> int | None:
    """Where, on the last line of ``redacted``, a value ends at a quote, or None: the marker of the value,
    directly followed by the quote. The header rule ends a value at a quote its line has not closed, and the
    line may still close it (``Redactor.redact_with_stop``). Two searches from the last line break: linear."""
    line = max(redacted.rfind("\n"), redacted.rfind("\r")) + 1
    return min((at for end in _STOPPED_AT_QUOTE if (at := redacted.find(end, line)) != -1), default=None)


def _is_secret_flag(item: object) -> bool:
    """A command-line flag whose value is a secret: ``--password``, ``-api-key``. ``--password=V`` is not (its
    value is in the same string, which the text patterns redact); neither is ``--password-file``."""
    return isinstance(item, str) and item.startswith("-") and is_sensitive_key(item)


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

    @property
    def longest_literal(self) -> int:
        """The length of the longest configured literal it redacts, or 0. A stream of text redacted as it
        grows holds back this many characters less one, so a literal cut at its end is never sent in part
        (the agent runtime's delta stream; Lead decision 3.1f2c #11)."""
        return len(self._literals[0]) if self._literals else 0

    def redact(self, text: str) -> str:
        """``text`` with every secret it finds replaced by REDACTED: the configured literals, token patterns
        and URL credentials first, then the value after a redacted token, a credential header, a secret key,
        a secret flag (also as an argv item) and netrc's ``password``, in that order (``_redact``). Linear in
        the length of ``text`` (``tests/security/test_redaction_linear.py``)."""
        return self._redact(text, stop=False)[0]

    def redact_with_stop(self, text: str) -> tuple[str, int | None]:
        """``redact(text)``, and where in it a stream of the text, cut at its end, holds back from: the agent
        runtime's delta stream (reviews of 0733969 and 45c2ec7). The header rule ends a value at a quote its
        line may still close (CREDENTIAL_HEADER), and the value takes the quoted part once it closes. The
        rules after it may pair that quote or take the marker before it, so the stop is read on the text as
        the header rule leaves it (``stopped_at_open_quote``), then placed in the redacted text: at the same
        place when no later rule changed anything up to it, else at the start of the redacted text's last line
        (a later rule only replaces a value with the marker, never adds a line break, so that line holds all
        that came after the stop). None when the header rule left no such stop."""
        return self._redact(text, stop=True)

    def _redact(self, text: str, *, stop: bool) -> tuple[str, int | None]:
        """The rules in their one order. Each runs only on a text that holds what every match of it holds
        (WP-3.1f2c fix round: the delta stream redacts a call's whole text up to about 100 times): a word of
        KEY_HINT for all of them, and a character each needs (its separator, the marker's "[", a flag's "-").
        Skipping a rule that cannot match leaves the text as running it would."""
        text = self._without_tokens(text)
        if KEY_HINT.search(text) is None:
            return text, None  # no key, header name or marker: none of the rules below can match
        if "[" in text:
            text = _AFTER_REDACTED.sub(_drop_value, text)
        at: int | None = None
        if ":" in text:
            text = CREDENTIAL_HEADER.sub(_drop_value, text)
            at = stopped_at_open_quote(text) if stop else None
        headed = text
        if ":" in text or "=" in text:
            text = _KEY_VALUE.sub(_drop_value, text)
        if "-" in text:
            text = _FLAG_VALUE.sub(_drop_value, text)
            text = _ARGV_VALUE.sub(_drop_value, text)
        text = _NETRC.sub(_drop_value, text)
        if at is not None and not text.startswith(headed[: at + len(REDACTED) + 1]):
            at = max(text.rfind("\n"), text.rfind("\r")) + 1
        return text, at

    def _without_tokens(self, text: str, *, keep_lines: bool = False) -> str:
        """The text with configured literals, token patterns and URL credentials replaced: what
        ``_AFTER_REDACTED`` reads. With ``keep_lines`` (``find``), each marker comes after the line breaks of
        what it replaced, so the marker and the text after it stay on their original line."""

        def marker(removed: str) -> str:
            return "\n" * removed.count("\n") + REDACTED if keep_lines else REDACTED

        replace: Callable[[re.Match[str]], str] | str = (
            (lambda match: marker(match[0])) if keep_lines else REDACTED
        )
        for literal in self._literals:
            text = text.replace(literal, marker(literal))
        for pattern in _PATTERNS:
            text = pattern.sub(replace, text)
        if "://" not in text:
            return text
        return _URL_CREDENTIALS.sub(rf"\g<scheme>{REDACTED}@", text)  # userinfo holds no line break

    def find(self, text: str) -> list[Finding]:
        """What ``redact`` would remove, as (line, rule), in text order; one finding per rule and line. The
        value itself is never returned (security.scan_secrets, SECURITY_MODEL T5)."""
        rules: list[tuple[str, re.Pattern[str]]] = [
            *((f"literal_{index}", re.compile(re.escape(lit))) for index, lit in enumerate(self._literals)),
            *_NAMED_PATTERNS,
            ("url_credentials", _URL_CREDENTIALS),
            ("credential_header", CREDENTIAL_HEADER),
            ("credential_assignment", _KEY_VALUE),
            ("credential_assignment", _FLAG_VALUE),
            ("credential_assignment", _ARGV_VALUE),
            ("credential_assignment", _NETRC),
        ]
        seen: set[tuple[int, str]] = set()
        starts = _line_starts(text)
        for rule, pattern in rules:
            name = "configured_secret" if rule.startswith("literal_") else rule
            seen.update((_line(starts, match), name) for match in pattern.finditer(text))
        # A value after a redacted token is only there once the tokens are replaced, as in redact.
        replaced = self._without_tokens(text, keep_lines=True)
        starts = _line_starts(replaced)
        seen.update(
            (_line(starts, match), "credential_assignment") for match in _AFTER_REDACTED.finditer(replaced)
        )
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
            return self._redact_items(obj, stringify=stringify)
        if isinstance(obj, tuple):
            return tuple(self._redact_items(obj, stringify=stringify))
        if isinstance(obj, set | frozenset):
            return type(obj)(self.redact_obj(value, stringify=stringify) for value in obj)
        if stringify and not isinstance(obj, _PRIMITIVES):
            if isinstance(obj, bytes | bytearray):
                return self.redact(bytes(obj).decode("utf-8", errors="replace"))
            return self.redact(repr(obj))
        return obj

    def _redact_items(self, items: Iterable[Any], *, stringify: bool) -> list[Any]:
        """A list or tuple, read as a command line too: the string after a secret flag is dropped whole, like
        a value under a sensitive key (``["mysql", "--password", "V"]``). A dropped string that is itself a
        secret flag drops the string after it too (``["--no-password", "--token", "V"]``)."""
        out: list[Any] = []
        after_flag = False
        for item in items:
            take = after_flag and isinstance(item, str)
            out.append(REDACTED if take else self.redact_obj(item, stringify=stringify))
            after_flag = _is_secret_flag(item)
        return out


def _line_starts(text: str) -> list[int]:
    return [0, *(m.end() for m in re.finditer("\n", text))]


def _line(starts: list[int], match: re.Match[str]) -> int:
    """The 1-based line a match starts on, given ``_line_starts`` of the text it is in."""
    return bisect.bisect_right(starts, match.start())
