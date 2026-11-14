"""White-box tests of ``app.core.redaction``: they read its private patterns and tables.

``tests/unit/test_redaction.py`` checks behaviour through the public API only. These tests check what that
API cannot show without a reference to compare with: a speed-up (the KEY_HINT prefilter, the literal-first
jwt pattern, the lookaheads in SECRET_KEY_PATTERN, the literal-first URL rule) leaves every output as the
rules run in full, or the patterns' former forms, would give; the prefilter's tables cover every word the
rules read; the black-box tests read no private name. Each is coupled to the module's internals on
purpose: a refactor that renames a pattern updates these tests, never the black-box ones (backend review M1,
final review of 74bf8d4).
"""

from __future__ import annotations

import ast
import functools
import random
import re
from pathlib import Path

import pytest

from app.core import redaction
from app.core.redaction import (
    _AFTER_REDACTED,
    _ARGV_VALUE,
    _CASE_FOLDS,
    _CREDENTIAL_HEADER,
    _FLAG_VALUE,
    _KEY_VALUE,
    _KNOWN_SECRET_NAMES,
    _NAMED_PATTERNS,
    _NETRC,
    _NETRC_KEY,
    _PATTERNS,
    _URL_CREDENTIALS,
    CREDENTIAL_HEADER_NAMES,
    KEY_HINT,
    REDACTED,
    SECRET_KEY_PATTERN,
    SENSITIVE_KEY_TAIL,
    Redactor,
    _drop_value,
)
from tests.unit.test_redaction import DOTLESS_I, DOTTED_I, FUZZ_PIECES, KELVIN, KNOWN_NAMES, LONG_S

# --- The fix round's speed-ups change no output (WP-3.1f2c, Lead ruling on the delta's time budget) ---
# Redactor.redact skips a rule on a text that cannot hold a match of it (KEY_HINT and one character per
# rule), the jwt pattern leads with its literal, and SECRET_KEY_PATTERN fails early where it could not match.
# Each must leave every output as it was: these tests compare with the rules run in full and with the
# patterns' former forms.

_ALL_CHARACTERS = "".join(map(chr, [*range(0xD800), *range(0xE000, 0x110000)]))


@functools.cache
def _case_variants(letter: str) -> str:
    """Every character a case-insensitive pattern takes for ``letter``, from all of Unicode."""
    return "".join(match[0] for match in re.finditer(f"(?i){re.escape(letter)}", _ALL_CHARACTERS))


def _tail_words() -> list[str]:
    words: list[str] = []
    for tail in SENSITIVE_KEY_TAIL.split("|"):
        words += [tail.replace("s?", ""), tail.replace("s?", "s")] if tail.endswith("s?") else [tail]
    return words


# The same sources KEY_HINT is built from (review M-2 of 50610b5): one header list, the netrc key, the marker.
HINTED_WORDS = [*_tail_words(), *sorted(_KNOWN_SECRET_NAMES), *CREDENTIAL_HEADER_NAMES, _NETRC_KEY, REDACTED]


def test_the_case_folds_are_every_character_a_case_insensitive_letter_takes() -> None:
    """KEY_HINT's lead class adds these to each letter's two cases: Python's re takes exactly these for an
    ASCII letter under IGNORECASE, over all of Unicode."""
    for letter in "abcdefghijklmnopqrstuvwxyz":
        expected = {letter, letter.upper(), *_CASE_FOLDS.get(letter, "")}
        assert set(_case_variants(letter)) == expected, letter


@pytest.mark.parametrize("word", HINTED_WORDS)
def test_key_hint_finds_every_word_the_rules_read_in_every_case(word: str) -> None:
    """Every key, header name and marker the six skippable rules read holds a word of KEY_HINT, in every
    character a case-insensitive pattern takes for each of its letters (the long s for s, the Kelvin sign for
    k)."""
    assert KEY_HINT.search(word) and KEY_HINT.search(word.upper()) and KEY_HINT.search(word.title())
    for at, letter in enumerate(word):
        for variant in _case_variants(letter) if letter.isalpha() else letter:
            assert KEY_HINT.search(word[:at] + variant + word[at + 1 :]), (word, at, hex(ord(variant)))


def _every_rule(text: str) -> str:
    """``Redactor().redact`` with no rule skipped: the reference for the skips."""
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _URL_CREDENTIALS.sub(f"://{REDACTED}@", text)
    for rule in (_AFTER_REDACTED, _CREDENTIAL_HEADER, _KEY_VALUE, _FLAG_VALUE, _ARGV_VALUE, _NETRC):
        text = rule.sub(_drop_value, text)
    return text


def test_the_rules_redact_skips_could_not_have_matched() -> None:
    rnd = random.Random(31)  # noqa: S311 - a reproducible fuzz, not a secret
    redactor = Redactor()
    for _ in range(4_000):
        text = "".join(rnd.choice(FUZZ_PIECES) for _ in range(rnd.randint(1, 16)))
        assert redactor.redact(text) == _every_rule(text), text


# The jwt pattern as it was before the fix round: the start checked before the literal.
_JWT_BEFORE = re.compile(
    r"(?:(?<![A-Za-z0-9_-])|(?<=^-)|(?<=[^A-Za-z0-9_-]-))"
    r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{10,}"
)


def test_the_jwt_pattern_matches_what_its_former_form_did() -> None:
    jwt = dict(_NAMED_PATTERNS)["jwt"]
    pieces = ["eyJ", "-", "a", ".", " ", "\n", "_", "x", "eyJhbGci", ".eyJzdWIi", "AbCdEfGhIjK", "Z", "é"]
    rnd = random.Random(7)  # noqa: S311 - a reproducible fuzz, not a secret
    for _ in range(20_000):
        text = "".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 25)))
        assert [m.span() for m in jwt.finditer(text)] == [m.span() for m in _JWT_BEFORE.finditer(text)], text


# The URL rule as it was before the final fix wave: the scheme first, retried from every letter.
_URL_CREDENTIALS_BEFORE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]{0,31}://)(?:[^/\s:@]*:[^/\s@]+|[^/\s:@]{8,})@"
)


def test_the_url_rule_redacts_what_its_former_form_did() -> None:
    """AG-13 F-4 (final review of 74bf8d4): the literal-first URL rule replaces the same userinfo, and its
    matches end where the former form's did (they start at "://", after the scheme the former form took)."""
    pieces = [
        "://", ":", "/", "@", "a", "Z", "1", "+", ".", "-", " ", "\n", "user", "pw", "x" * 8, "https", "h",
        "ab", "\u00e9", "a" * 31, "1" * 31, REDACTED, "git+ssh", "Pq9" * 4,
    ]  # fmt: skip
    rnd = random.Random(17)  # noqa: S311 - a reproducible fuzz, not a secret
    for _ in range(30_000):
        text = "".join(rnd.choice(pieces) for _ in range(rnd.randint(1, 14)))
        before = _URL_CREDENTIALS_BEFORE.sub(rf"\g<scheme>{REDACTED}@", text)
        assert _URL_CREDENTIALS.sub(f"://{REDACTED}@", text) == before, text
        ends = [m.end() for m in _URL_CREDENTIALS_BEFORE.finditer(text)]
        assert [m.end() for m in _URL_CREDENTIALS.finditer(text)] == ends, text


def test_the_key_pattern_matches_what_it_did_without_its_lookaheads() -> None:
    """The two lookaheads only fail early where no alternative could match: with and without them the
    pattern matches the same spans, inside a case-insensitive pattern (as _KEY_VALUE embeds it) and outside
    one."""
    lookaheads = ["(?=(?i:[A-Za-z0-9]))", "(?=[a-z0-9]*[A-Z])"]
    assert all(lookahead in SECRET_KEY_PATTERN for lookahead in lookaheads)
    without = SECRET_KEY_PATTERN
    for lookahead in lookaheads:
        without = without.replace(lookahead, "")
    pieces = [
        "pass", "Pass", "PASS", "word", "Word", "WORD", "wd", "secret", "Secret", "SECRET", "Key", "key",
        "KEY", "api", "Api", "API", "access", "Access", "private", "Private", "token", "Token", "TOKEN",
        "session", "Session", "cookie", "Cookie", "set", "Set", "jwt", "JWT", "bearer", "ticket",
        "credential", "s", "Id",
        "db", "DB", "x", "X", "2", "3", "_", "-", LONG_S, KELVIN, DOTLESS_I, DOTTED_I, "\u00e9", "pgpassword",
        "mysql_pwd", "aB", "ecret", "ession",
    ]  # fmt: skip
    boundaries = r"(?ix)(?<![A-Za-z0-9_-])(?:{})(?![A-Za-z0-9_-])"
    compiled = [
        (re.compile(boundaries.format(SECRET_KEY_PATTERN)), re.compile(boundaries.format(without))),
        (re.compile(SECRET_KEY_PATTERN), re.compile(without)),
    ]
    rnd = random.Random(11)  # noqa: S311 - a reproducible fuzz, not a secret
    for _ in range(20_000):
        text = "".join(rnd.choice(pieces) for _ in range(rnd.randint(1, 8)))
        for fast, slow in compiled:
            assert [m.span() for m in fast.finditer(text)] == [m.span() for m in slow.finditer(text)], text


def test_the_known_secret_names_are_the_three_the_lead_ruled() -> None:
    assert frozenset(KNOWN_NAMES) == _KNOWN_SECRET_NAMES


# --- The black-box tests read no private name (backend review M1, final review of 74bf8d4) ---

BLACK_BOX = Path(__file__).with_name("test_redaction.py")
BLACK_BOX_NAMES = {"REDACTED", "SECRET_KEY_PATTERN", "SENSITIVE_KEY_TAIL", "Redactor", "is_sensitive_key"}


def test_the_black_box_tests_import_only_the_public_api() -> None:
    """test_redaction.py tests through Redactor, is_sensitive_key and SECRET_KEY_PATTERN, with REDACTED and
    SENSITIVE_KEY_TAIL as data; anything that reads a private pattern belongs here."""
    imported = {
        alias.name
        for node in ast.walk(ast.parse(BLACK_BOX.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.module.startswith("app.")
        for alias in node.names
    }
    assert imported == BLACK_BOX_NAMES


# --- The public names are the ones other modules read (backend review M4, final review of 74bf8d4) ---

# app.agents.runtime reads the header names, the header start, KEY_HINT, the key and URL scheme patterns and
# stopped_at_open_quote; every outbound path reads Redactor (and Finding, from find); the tests read REDACTED,
# SENSITIVE_KEY_TAIL and is_sensitive_key.
PUBLIC_NAMES = {
    "REDACTED", "SENSITIVE_KEY_TAIL", "SECRET_KEY_PATTERN", "CREDENTIAL_HEADER_NAMES",
    "CREDENTIAL_HEADER_START", "KEY_HINT", "URL_SCHEME_PATTERN", "stopped_at_open_quote", "is_sensitive_key",
    "Finding", "Redactor",
}  # fmt: skip


def test_the_public_names_are_the_ones_other_modules_read() -> None:
    defined: set[str] = set()
    for node in ast.parse(Path(redaction.__file__).read_text()).body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined |= {target.id for target in node.targets if isinstance(target, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    assert {name for name in defined if not name.startswith("_")} == PUBLIC_NAMES
