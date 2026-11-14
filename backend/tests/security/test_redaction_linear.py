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
    # The same with a key word later in the text: the KEY_HINT prefilter skips the flag rule on a text with
    # none, so the case above no longer reaches _FLAG_VALUE (final fix wave: mutant Z3j survived it).
    ("dashes_before_plain_name_then_a_key_word", "-" * KIB_64 + "x y token"),
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
    # The fix round's KEY_HINT: every character a lead of its words, and words cut one letter short.
    ("key_hint_leads", "pcksujb[PCKSUJB\u017f" * 4_096),
    ("key_hint_near_words", "toke tick auh pas secre sessio cooki credentia beare jw [redacte " * 1_024),
    # Review of 50610b5: a header parameter's quote never closed, many of them, many on their own lines; a
    # header name before 64 KiB of line breaks (LF, CRLF); the #7 swallow after a glued string prefix.
    ("header_parameter_never_closed", 'Cookie: a="' + "b" * KIB_64),
    ("header_parameter_quotes", "Cookie: " + 'a="b ' * 13_107),
    ("header_parameter_quote_lines", "Cookie: " + 'a="\n' * 16_384),
    ("header_name_line_breaks", "Cookie:" + "\n" * KIB_64 + '"'),
    ("header_name_crlf", "Cookie:" + "\r\n" * (KIB_64 // 2) + '"'),
    ("bare_swallow_prefix_letters", "password=a:" + "b" * KIB_64),
    ("bare_swallow_prefix_quotes", "password=a:b" + '"' * KIB_64),
    # Review of 0733969: base64 padding before a quote that closes a shell word, many times on one line.
    ("header_padding_quotes", "Cookie: " + 'a==" ' * 13_107),
    # AG-13 F-1 (final review of 74bf8d4): a quoted value closed on its own line by a plain close, its
    # escape-aware close on no line: escaped quotes to the end of one long line, the same with single quotes,
    # and many lines that each end such a value.
    ("escaped_quotes_on_one_line", 'password="' + '\\"' * (KIB_64 // 2)),
    ("escaped_apostrophes_on_one_line", "password='" + "\\'" * (KIB_64 // 2)),
    ("backslash_closes_on_many_lines", "password='a\\'\n" * (KIB_64 // 14)),
    # AG-13 F-2: a bare value is never a key with its own value. Keys as values one after another, netrc's
    # key as a value, a key before 64 KiB of spaces, and one 64 KiB word read as a key.
    ("bare_value_key_run", "password=token=" * (KIB_64 // 15)),
    ("bare_value_netrc_key_run", "token: password " * (KIB_64 // 16)),
    ("netrc_value_key_run", "password password " * (KIB_64 // 18)),
    ("bare_value_key_before_spaces", "password=token" + " " * KIB_64 + "x"),
    ("bare_value_long_word", "password=" + "aB" * (KIB_64 // 2) + "="),
    # AG-13 F-3: after a hump a tail word is any upper-case run then lower case. Runs that never end a tail,
    # mixed tails one after another (one 64 KiB name), and two-word tails mixed after a hump.
    ("upper_runs_that_never_end_a_tail", "aPASSWOR" * (KIB_64 // 8)),
    ("mixed_tail_run", "dbPASSword" * (KIB_64 // 10) + "=V"),
    ("mixed_two_word_tail_run", "aSecretKEy" * (KIB_64 // 10) + "=V"),
    # AG-13 F-8: a key may start after an escaped line break or tab. Escaped breaks before keys, flags and
    # netrc's key one after another, and before words that are no key.
    ("escaped_breaks_before_keys", "\\npassword=x" * (KIB_64 // 12)),
    ("escaped_breaks_before_flags", "\\t--token x" * (KIB_64 // 12)),
    ("escaped_breaks_before_netrc", "\\rpassword x" * (KIB_64 // 12)),
    ("escaped_breaks_before_words", "\\nxyz" * (KIB_64 // 5)),
]


@pytest.mark.parametrize(("name", "text"), CASES, ids=[name for name, _ in CASES])
def test_pattern_is_linear(name: str, text: str) -> None:
    """``redact``, and ``redact_with_stop``: the agent runtime's delta stream reads a header stop with it
    (review of 08fa05e)."""
    redactor = Redactor()
    for redact in (redactor.redact, redactor.redact_with_stop):
        started = time.perf_counter()
        redact(text)
        elapsed = time.perf_counter() - started
        assert elapsed < 1.0, f"{name}, {redact.__name__}: {elapsed:.2f} s"


def test_the_key_predicate_is_linear() -> None:
    # _CAMEL_BOUNDARY splits a name at every hump: a 64 KiB name with a hump at every second character.
    started = time.perf_counter()
    is_sensitive_key("aB" * (KIB_64 // 2))
    is_sensitive_key("-" * KIB_64 + "password")
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"{elapsed:.2f} s"


@pytest.mark.parametrize(
    "items",
    [
        ["--token"] * 8_192,
        ["--token", "x"] * 4_096,
        ["--no-password", "--token"] * 4_096,
        ["--token", ["x"], "--password=", 1] * 2_048,  # any value after a flag, and a flag ending in "="
    ],
    ids=["flags", "flag_value_pairs", "secret_flags", "nested_values"],
)
def test_an_argv_list_is_linear(items: list[str]) -> None:
    redactor = Redactor()
    started = time.perf_counter()
    redactor.redact_obj(items)
    redactor.redact_obj(tuple(items))
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"{elapsed:.2f} s"
