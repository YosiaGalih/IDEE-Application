"""Every redaction pattern stays linear on pathological input (spec WP-3.1f2c Goal 4).

``Redactor.redact`` runs on every outbound path, often on 64 KiB tool outputs. A pattern retried from every
position of a long run was quadratic before: "eyJ" repeated over 256 KiB took 17 s (AG-13 WP-3.1d2a
MEDIUM-2), an unbounded URL scheme 2.8 s on 64 KiB of letters (WP-3.1d1). One case per new or changed
pattern; the bound is loose for CI.
"""

from __future__ import annotations

import time

import pytest

from app.core.redaction import Redactor, is_sensitive_key

KIB_64 = 65_536

CASES = [
    # The camel and acronym key forms (R-1): a long identifier with no separator, a hump at every second
    # character, a tail word repeated with nothing after it, and one tail after a long acronym.
    ("camel_run", "aB" * (KIB_64 // 2)),
    ("camel_tail_run", "secretKey" * 7_282),
    ("acronym_run_before_tail", "A" * KIB_64 + "Password="),
    # Leading underscores of a private name (self._password): a long run of them before a tail, and a name
    # that alternates them with letters.
    ("underscores_before_tail", "_" * KIB_64 + "password="),
    ("underscore_letter_run", "_a" * (KIB_64 // 2)),
    # Command-line flags (--password=V, --token V): a long run of dashes before a tail and before a plain
    # name, dashes alternating with letters, and many flags in a row.
    ("dashes_before_tail", "-" * KIB_64 + "password=V"),
    ("dashes_before_plain_name", "-" * KIB_64 + "x y"),
    ("dash_letter_run", "--a" * 21_845),
    ("flag_run", "--token " * 8_192),
    # Value forms (R-4..R-6): quotes never closed, backslashes inside a string (plain and escaped JSON), the
    # rule after a redacted token, an unclosed triple quote, bare quotes, and a bare value ending in ":"
    # before a long gap (#7). A netrc "password" before a long gap, and quoted flags in an argv list.
    ("unclosed_quote_run", 'password="' * 6_554),
    ("backslashes_in_a_quote", 'password="' + "\\" * KIB_64),
    ("backslashes_in_escaped_json", 'password=\\"' + "\\" * KIB_64),
    ("escaped_quotes_in_escaped_json", 'password=\\"' + '\\\\\\"' * 16_384),
    ("redacted_assignment_run", "[REDACTED]=" * 5_958),
    ("unclosed_triple_quote", 'password = """' + "a" * KIB_64),
    ("apostrophes", "'" * KIB_64),
    ("double_quotes", '"' * KIB_64),
    ("bare_value_before_spaces", "password=a:" + " " * KIB_64 + "x"),
    ("netrc_before_spaces", "password" + " " * KIB_64 + "="),
    ("quoted_flag_run", '"--password", ' * 4_096),
    ("quoted_flag_without_separator_run", '"--password"' * 5_461),
    ("secret_flag_run", "--password --token " * 3_449),
    # The secret-flag check on a flag's value reads a key from the value's start: a long run of dashes there,
    # quoted or not, and quoted dashes everywhere.
    ("flag_value_of_dashes", "--password " + "-" * KIB_64),
    ("flag_value_of_quoted_dashes", '--password "' + "-" * KIB_64),
    ("quoted_dash_run", "'-" * (KIB_64 // 2)),
    # A redacted token before a long gap (the rule after a token, #7), and string prefixes before no quote.
    ("token_before_spaces", "ghp_" + "a" * 36 + ":" + " " * KIB_64),
    ("string_prefix_run", "password=" + "rb" * (KIB_64 // 2)),
    ("string_prefix_before_quotes", "password=b" + "'" * KIB_64),
    # A secret flag taken as a value takes the word after it (Task 2 review, Important 1): long runs of secret
    # flags, bare (also a word the key pattern matches two ways), quoted, before trailing spaces, as argv list
    # items and after a netrc "password". A quoted flag in shell words, and a #7 swallow kept on one line.
    ("secret_flag_chain", "--password " + "--token " * 8_190),
    ("secret_flag_chain_pascal", "--password " + "--Password " * 5_956),
    ("secret_flag_chain_before_spaces", "--password " + "--token " * 4_096 + " " * (KIB_64 // 2)),
    ("quoted_secret_flag_chain", "--password " + '"--token" ' * 6_552),
    ("argv_secret_item_chain", '"--password", ' + '"--token", ' * 5_956),
    ("quoted_flag_shell_run", '"--password" ' * 5_041),
    ("netrc_secret_flag_chain", "password " + "-abcSecret " * 5_956),
    ("bare_swallow_before_tabs_and_a_line", "password=a:" + "\t" * KIB_64 + '\n"x"'),
    # A header value reads quoted parts (R-2): quotes never closed in a cookie; PGP blocks with no END (R-3).
    ("header_quote_run", "Cookie: " + '"a' * 32_768),
    ("header_closed_quote_run", "Cookie: " + '"a"' * 21_845),
    ("pgp_begin_run", "-----BEGIN PGP PRIVATE KEY BLOCK-----" * 1_771),
]


@pytest.mark.parametrize(("name", "text"), CASES, ids=[name for name, _ in CASES])
def test_pattern_is_linear(name: str, text: str) -> None:
    redactor = Redactor()
    started = time.perf_counter()
    redactor.redact(text)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"{name}: {elapsed:.2f} s"


def test_the_key_predicate_is_linear() -> None:
    # _CAMEL_BOUNDARY splits a name at every hump: a 64 KiB name with a hump at every second character.
    started = time.perf_counter()
    is_sensitive_key("aB" * (KIB_64 // 2))
    is_sensitive_key("-" * KIB_64 + "password")
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"{elapsed:.2f} s"


@pytest.mark.parametrize(
    "items",
    [["--token"] * 8_192, ["--token", "x"] * 4_096, ["--no-password", "--token"] * 4_096],
    ids=["flags", "flag_value_pairs", "secret_flags"],
)
def test_an_argv_list_is_linear(items: list[str]) -> None:
    redactor = Redactor()
    started = time.perf_counter()
    redactor.redact_obj(items)
    redactor.redact_obj(tuple(items))
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"{elapsed:.2f} s"
