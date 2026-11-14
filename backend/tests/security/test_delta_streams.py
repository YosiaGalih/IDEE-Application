"""``agent.delta`` streams of a whole run (reviews of 073e68d: AG-13 D-M1, D-L1; /code-review Spec item 11).

- The open-candidate checks are linear: pathological 64 KiB attempts stream in well under a second.
- A long quoted value under a key the redactor treats as secret never streams raw, whatever its spelling:
  candidates are found in the raw text (``_NETRC`` rewrites ``password = "…`` before a candidate could be
  seen in the redacted text) and from the redactor's own key list.
- A reset carries the run's whole text shown so far: the calls already ended, then the current call.
- WP-3.1f2c (D2-L2, D2-I1; Lead decisions #10, #11, Goal 6): a key far from its quote, a key in any spelling
  the redactor knows (camel, a flag, an argv item, one a token ran on into, a string prefix before the value),
  a long URL userinfo and a long configured literal never stream raw: no 32-character window of the secret
  reaches any payload.

``tests/security/test_delta_redaction.py`` holds the single-attempt cases.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

import pytest

from app.agents.runtime import DELTA_HOLD_CHARS, Delta, DeltaRedactor, delta_hold, open_candidate
from app.agents.runtime import DELTA_RELEASE_CHARS as DELTA_RELEASE
from app.core.redaction import Redactor

REDACTOR = Redactor()
VALUE = "Zq7" * 400  # 1 200 characters
SPELLINGS = [
    'password="{}"', 'password: "{}"', '"password": "{}"', 'password = "{}"', "password = '{}'",
    'password : "{}"', 'PASSWORD = "{}"', 'db_password = "{}"', 'passphrase = "{}"', 'secret_key: "{}"',
    '"secrets": "{}"', '"cookies": "{}"', 'ticket="{}"', '"passwords": "{}"',
]  # fmt: skip


def pieces(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def stream(redactor: DeltaRedactor, parts: Iterable[str], *, flush: bool = True) -> list[Delta]:
    out = [redactor.feed(part) for part in parts]
    if flush:
        out.append(redactor.flush())
    return [d for d in out if d is not None]


def client(events: Iterable[Delta], shown: str = "") -> str:
    for event in events:
        shown = event.text if event.reset else shown + event.text
    return shown


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("size", [1, 16, 64])
def test_a_long_quoted_secret_never_streams_raw_whatever_its_spelling(spelling: str, size: int) -> None:
    """D-L1: zero raw characters of the 1 200-character value in any payload."""
    text = "intro " * 40 + spelling.format(VALUE) + " tail" * 80
    events = stream(DeltaRedactor(REDACTOR.redact), pieces(text, size))
    assert all("Zq7" not in event.text for event in events)
    assert "Zq7" not in "".join(event.text for event in events)
    assert client(events) == REDACTOR.redact(text)


KIB_64 = 64 * 1024


def filled(head: str, filler: str) -> str:
    """``head``, then ``filler`` up to 64 KiB."""
    return (head + filler * KIB_64)[:KIB_64]


# Built, not written: test values a token pattern matches, followed by a key it runs on into.
GITHUB_TOKEN = "ghp_" + "a1B2" * 9
ANTHROPIC_KEY = "sk-ant-" + "api03-" + "A1b2C3d4" * 2

PATHOLOGICAL = {
    # An open quote pins the origin while ended eyJ runs follow: each release used to rescan every run.
    "eyJ runs": filled('password="' + "eyJ" * 10_000 + " ", "w"),
    "BEGIN markers": filled('password="' + "-----BEGIN" * 3_000 + "-----END x", "w"),
    "keys and quotes": filled('password="a" ' * 4_000 + 'password="', "w"),
    "open quote": filled('password="', "w"),
    "open token": filled("eyJ", "A"),
    "open BEGIN": filled("-----BEGIN PRIVATE KEY-----\n", "MIIE"),
    "plain": filled("", "The quick brown fox jumps over the lazy dog. "),
    # WP-3.1f2c (Lead decisions #10, #11): each new candidate path. A URL never closed; a key 32 KiB of
    # spaces before its quote, and spaces with no key; the window before a quote full of camel humps or dashes
    # that never make a key; flags, keys a token ran on into and string prefixes one after another; an argv
    # item and a quoted shell word held open; a long run of prefix letters.
    "open URL": filled("https://", "a"),
    "far key": filled("password =" + " " * 32_768 + '"', "w"),
    "spaces before a quote": filled(" " * 32_768 + '"', "w"),
    "camel humps": filled("aB" * 16_384 + ' = "', "w"),
    "dashes": filled("-" * 32_768 + ' "', "w"),
    "dash flags": filled("--password " * 3_000 + '"', "w"),
    "argv item": filled('"--password", ' * 10 + '"', "w"),
    "quoted shell word": filled('"--password" ' * 10 + '"', "w"),
    "keys after tokens": filled((GITHUB_TOKEN + 'password="a" ') * 1_000 + GITHUB_TOKEN + 'password="', "w"),
    "string prefixes": filled('password = rb"a" ' * 3_000 + 'password = b"', "w"),
    "prefix letters": filled("password = " + "b" * 32_768 + '"', "w"),
    # Dense text where a new candidate opens at every release (Lead ruling on the 3.1f2c fix round: every
    # input stays under the bound): URLs, argv items and quoted shell words one after another, quoted dashes.
    "dense URLs": filled("", "ab+.-://"),
    "dense argv items": filled("", '"--password", '),
    "dense quoted shell words": filled("", '"--password" '),
    "quoted dashes": filled('"-' * 16_384 + ', "', "w"),
    # A header line with an open quote: a 64 KiB line, its quoted parts never closed, then closed ones.
    "open header quotes": filled("Cookie: ", 'a="b '),
    "header quoted parts": filled("Cookie: ", 'a="b"; '),
    "header names": filled("", 'Cookie: "'),
}


@pytest.mark.parametrize("name", list(PATHOLOGICAL))
def test_a_pathological_64_kib_attempt_streams_in_under_a_second(name: str) -> None:
    """D-M1: the open-candidate checks are linear scans from the end, not ``\\Z``-anchored searches."""
    text = PATHOLOGICAL[name]
    assert len(text) == KIB_64
    started = time.perf_counter()
    events = stream(DeltaRedactor(REDACTOR.redact), pieces(text, 16))
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, (name, elapsed)
    assert client(events) == REDACTOR.redact(text)


def changes_once_z_arrives(text: str) -> str:
    return text.replace("a", "*") if "Z" in text else text


def test_a_reset_in_a_later_call_carries_the_text_of_the_calls_before() -> None:
    """Item 11: the first call's final text is committed; a reset in the second call carries it, then the
    second call's stable text, so the client shows the run's whole text."""
    redactor = DeltaRedactor(changes_once_z_arrives, hold=4, release=1)
    first = stream(redactor, ["The first call has an answer. "])
    second = stream(redactor, ["abc" * 10, "Zmore", "text"])
    assert any(event.reset for event in second)
    reset = next(event for event in second if event.reset)
    assert reset.text.startswith("The first call has an answer. ")
    expected = changes_once_z_arrives("The first call has an answer. ") + changes_once_z_arrives(
        "abc" * 10 + "Zmoretext"
    )
    assert client(first + second) == expected


def test_a_restart_in_a_later_call_voids_only_that_attempt() -> None:
    """Item 11, M4: a restart goes back to the calls already ended, never to an empty text; a secret in the
    failed attempt reaches no payload."""
    redactor = DeltaRedactor(REDACTOR.redact)
    call_1 = "Here is what I found in the repository so far. " * 8
    first = stream(redactor, pieces(call_1, 16))
    failed = stream(redactor, pieces("Second call. " * 30 + 'password="' + VALUE[:600], 16), flush=False)
    restart = redactor.restart()
    assert restart == Delta(REDACTOR.redact(call_1), reset=True)
    retry = stream(redactor, pieces("The retried second call. " * 20, 16))
    events = first + failed + [restart] + retry
    assert "Zq7" not in "".join(event.text for event in events)
    assert client(events) == REDACTOR.redact(call_1) + REDACTOR.redact("The retried second call. " * 20)


def test_a_redaction_across_the_candidate_start_never_cuts_into_the_candidate() -> None:
    """D-L1: the hold is mapped from the raw candidate start into the redacted text by the common prefix of
    the whole redaction and the redaction of the text before the candidate. When the text before redacts
    differently once the candidate follows (here 40 glue characters vanish), the length of the latter alone
    would cut 40 characters into the value."""
    glue = "GLUE" * 10

    def redact(text: str) -> str:
        return REDACTOR.redact(text.replace(glue + "password", "password"))

    text = "intro " * 60 + glue + 'password="' + VALUE + '"' + " tail" * 80
    for size in (1, 16, 64):
        events = stream(DeltaRedactor(redact), pieces(text, size))
        assert "Zq7" not in "".join(event.text for event in events), size
        assert client(events) == redact(text)


TOKENS = {
    "anthropic": "sk-ant-" + "api03-" + "A1b2C3d4" * 6,
    "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
    "jwt": "eyJ"
    + "hbGciOiJIUzI1NiJ9"
    + ".eyJ"
    + "zdWIiOiIxMjM0NTY3ODkwIn0"
    + "."
    + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",
}


@pytest.mark.parametrize("name", list(TOKENS))
@pytest.mark.parametrize("cut", [6, 12, 16])
def test_the_last_window_holds_a_token_cut_at_a_release(name: str, cut: int) -> None:
    """The ``hold`` window, not only the candidates: a release that falls ``cut`` characters into a token the
    redactor does not match yet sends none of it."""
    token = TOKENS[name]
    text = "x" * (DELTA_RELEASE - cut - 1) + " " + token + " and the answer goes on." * 20
    events = stream(DeltaRedactor(REDACTOR.redact), pieces(text, 1))
    assert token[4:cut] not in "".join(event.text for event in events)
    assert client(events) == REDACTOR.redact(text)


# --- WP-3.1f2c: candidates for far keys, every key spelling, long userinfo, long literals (D2-L2, D2-I1) ---

WINDOW = 32
SIZES = [1, 16, 64]
INTRO = "intro " * 40
TAIL = " tail" * 80


def windows(secret: str) -> set[str]:
    return {secret[i : i + WINDOW] for i in range(len(secret) - WINDOW + 1)}


def leaked(events: list[Delta], secret: str) -> list[str]:
    """The 32-character windows of ``secret`` found in any payload, or across payloads (Goal 6)."""
    texts = [event.text for event in events]
    return sorted({w for w in windows(secret) for text in [*texts, "".join(texts)] if w in text})


def held_from(text: str, value: str) -> int | None:
    """Where the open candidate starts while ``value`` is half streamed."""
    return open_candidate(text[: text.index(value) + len(value) // 2])


GAPS = {"140 spaces": " " * 140, "140 line breaks": "\n" * 140, "127 spaces": " " * 127}


@pytest.mark.parametrize("gap", list(GAPS))
@pytest.mark.parametrize("size", SIZES)
def test_a_key_far_from_its_quote_still_holds(gap: str, size: int) -> None:
    """D2-L2 (Lead decision #10): the key and its separator are looked for within 128 characters of the last
    non-space character before the quote, so spaces or line breaks of any length between them keep the value
    held from its key."""
    text = INTRO + "password =" + GAPS[gap] + '"' + VALUE + '"' + TAIL
    assert held_from(text, VALUE) == text.index("password")
    events = stream(DeltaRedactor(REDACTOR.redact), pieces(text, size))
    assert leaked(events, VALUE) == []
    assert client(events) == REDACTOR.redact(text)


USERINFO = "Pq9" * 200
URLS = {
    "user and password": "https://user:" + USERINFO + "@git.example.com/r.git",
    "token as the user": "https://" + USERINFO + "@git.example.com/r.git",
}


@pytest.mark.parametrize("url", list(URLS))
@pytest.mark.parametrize("size", SIZES)
def test_a_long_userinfo_never_streams_raw(url: str, size: int) -> None:
    """D2-I1 (Lead decision #11): a ``scheme://`` not yet followed by "@", "/" or a space is held from its
    scheme, so userinfo longer than the hold is redacted whole once its "@" arrives."""
    text = INTRO + URLS[url] + TAIL
    assert held_from(text, USERINFO) == text.index("https://")
    events = stream(DeltaRedactor(REDACTOR.redact), pieces(text, size))
    assert leaked(events, USERINFO) == []
    assert client(events) == REDACTOR.redact(text)


LONG_LITERAL = "Lit" * 400  # 1 200 characters
SHORT_LITERAL = "Sh0rt-8c"  # 8 characters: the shortest literal the redactor takes


def test_the_hold_covers_the_longest_literal_and_never_drops_below_the_default() -> None:
    assert delta_hold(Redactor()) == DELTA_HOLD_CHARS
    assert delta_hold(Redactor([SHORT_LITERAL, "x" * DELTA_HOLD_CHARS])) == DELTA_HOLD_CHARS
    assert delta_hold(Redactor(["x" * (DELTA_HOLD_CHARS + 2)])) == DELTA_HOLD_CHARS + 1
    assert delta_hold(Redactor([SHORT_LITERAL, LONG_LITERAL])) == len(LONG_LITERAL) - 1


@pytest.mark.parametrize("size", SIZES)
def test_a_long_configured_literal_never_streams_raw(size: int) -> None:
    """D2-I1 (Lead decision #11; plan Review Focus 5): with a short and a long literal in one run the hold
    follows the longest, so neither is ever sent in part."""
    redactor = Redactor([SHORT_LITERAL, LONG_LITERAL])
    text = INTRO + "first " + SHORT_LITERAL + " then " + LONG_LITERAL + TAIL
    events = stream(DeltaRedactor(redactor.redact, hold=delta_hold(redactor)), pieces(text, size))
    assert leaked(events, LONG_LITERAL) == []
    assert not any(SHORT_LITERAL in event.text for event in events)
    assert client(events) == redactor.redact(text)


# Camel, Pascal and acronym keys: the first is AG-13's, the others a check on SENSITIVE_KEY_TAIL alone missed.
CAMEL_KEYS = ["clientSecret", "secretKey", "privateKey", "accessKey", "awsSecretAccessKey", "SMTPPassword"]


@pytest.mark.parametrize("key", CAMEL_KEYS)
@pytest.mark.parametrize("size", SIZES)
def test_a_camel_key_quote_holds(key: str, size: int) -> None:
    """Goal 6: the delta's key check is SECRET_KEY_PATTERN, the redactor's own key names."""
    text = INTRO + key + ' = "' + VALUE + '"' + TAIL
    assert held_from(text, VALUE) == text.index(key)
    events = stream(DeltaRedactor(REDACTOR.redact), pieces(text, size))
    assert leaked(events, VALUE) == []
    assert client(events) == REDACTOR.redact(text)


def test_a_plain_link_at_the_end_is_kept() -> None:
    """Lead decision #12: a URL with no "@" carries no credential. It is held while it may still grow, and the
    final payload at ``flush`` shows it as ``redact`` does."""
    text = INTRO + "See https://example.com"
    assert open_candidate(text) == text.index("https://")
    for size in SIZES:
        events = stream(DeltaRedactor(REDACTOR.redact), pieces(text, size))
        assert client(events) == REDACTOR.redact(text) == text
        assert "https://example.com" in client(events)


# The value forms the redactor takes after a key it knows (WP-3.1f2c Task 2), each with where the candidate
# starts: a flag and its value after spaces, an argv list item after its flag, a quoted shell word, a key a
# token pattern ran on into (the redactor sees "[REDACTED]=" there), and a string prefix before the quote.
# A token followed by a separator alone has no key in the raw text: the redactor hides that value to the end
# of the text while its quote is open, so it is checked end to end only.
QUOTE_FORMS: dict[str, tuple[str, str | None]] = {
    "dash flag": ('mysql --password "{}"', "--password"),
    "dash flag, single quotes": ("curl -api-key '{}'", "-api-key"),
    "argv item": ('["mysql", "--password", "{}"]', '"--password"'),
    "argv item, single quotes": ("['--token', '{}']", "'--token'"),
    "argv item, escaped JSON": ('[\\"--password\\", \\"{}\\"]', '"--password'),
    "quoted shell word": ('mysql "--password" "{}"', '"--password"'),
    "quoted shell word, single quotes": ("mysql '--api-key' '{}'", "'--api-key'"),
    "key after a GitHub token": (GITHUB_TOKEN + 'password="{}"', "password"),
    "key after an Anthropic key": (ANTHROPIC_KEY + 'password="{}"', "password"),
    "camel key after a GitHub token": (GITHUB_TOKEN + "secretKey='{}'", "secretKey"),
    "separator after a token": (GITHUB_TOKEN + '="{}"', None),
    "bytes prefix": ('password = b"{}"', "password"),
    "raw prefix": ('SECRET_KEY = r"{}"', "SECRET_KEY"),
    "two-letter prefix": ("token: rb'{}'", "token"),
    "prefix after a flag": ("--token b'{}'", "--token"),
    # A header value without an opening quote is the rest of its line, quoted parts included (Task 3, R-2):
    # the delta holds it from the value's start while a quote on that line is open (Lead ruling, fix round).
    "cookie parameter": ('Cookie: theme="{}"', "theme"),
    "set-cookie after other parameters": ('Set-Cookie: a=1; sid="{}"', "a=1"),
    "authorization token parameter": ('Authorization: Token token="{}"', "Token"),
    "x-api-key parameter": ('x-api-key: password="{}"', "password"),
    "proxy-authorization, single quotes": ("Proxy-Authorization: Digest response='{}'", "Digest"),
}


@pytest.mark.parametrize("form", list(QUOTE_FORMS))
@pytest.mark.parametrize("size", SIZES)
def test_a_quoted_value_after_any_key_form_never_streams_raw(form: str, size: int) -> None:
    """Goal 6 and the Lead's Task 2 carry: the delta holds a quote-opened value in every key form the
    redactor redacts, from the key, and the client ends with exactly what ``redact`` gives."""
    template, key = QUOTE_FORMS[form]
    text = INTRO + template.format(VALUE) + TAIL
    assert not any(window in REDACTOR.redact(text) for window in windows(VALUE))  # the redactor takes it
    if key is not None:
        assert held_from(text, VALUE) == text.index(key)
    events = stream(DeltaRedactor(REDACTOR.redact), pieces(text, size))
    assert leaked(events, VALUE) == []
    assert client(events) == REDACTOR.redact(text)


@pytest.mark.parametrize(
    "text",
    [
        'Run it with "--password" and type the password when asked.',
        "Pass '--token' first.",
        'The --password" flag',
        "The secret's length",
        'mysql "--password""',
    ],
)
def test_a_flag_word_with_no_space_before_the_quote_is_no_candidate(text: str) -> None:
    """A flag (or a quoted flag) opens a value only after spaces, as in the redactor (``_FLAG_VALUE``,
    ``_ARGV_VALUE``): the closing quote of "--password" in prose opens nothing, so the rest of the answer is
    not held until the stream ends."""
    assert open_candidate(text) is None


def test_a_header_line_is_held_only_while_its_quote_is_open() -> None:
    """A header value never crosses a line: once the line ends, or its quote closes, nothing is held."""
    line = "Cookie: it's here"
    assert open_candidate("Some text.\n" + line) == len("Some text.\nCookie: ")
    assert open_candidate("Some text.\n" + line + "\nThe answer goes on.") is None
    assert open_candidate('Cookie: theme="dark"; lang=en') is None
    # A value stopped by its own quote on an earlier line is final; a quote on the last line opens nothing.
    assert open_candidate("Cookie: it's here\nThe 'answer") is None
    # The name and the colon may sit on earlier lines (YAML): the separator takes line breaks.
    yaml = 'cookie:\n  theme="Zq7'
    assert open_candidate(yaml) == yaml.index("theme")
    spread = 'Cookie\n\n:\n theme="Zq7'
    assert open_candidate(spread) == spread.index("theme")
    assert open_candidate('"Authorization": "Bearer abc') == 1  # the JSON form: the key candidate, as before


def test_a_quoted_flag_in_prose_does_not_hold_the_rest_of_the_answer() -> None:
    text = 'Run it with "--password" and type the password when asked.' + " Then it carries on." * 40
    redactor = DeltaRedactor(REDACTOR.redact)
    shown = client(e for e in (redactor.feed(piece) for piece in pieces(text, 16)) if e is not None)
    assert len(shown) >= len(REDACTOR.redact(text)) - DELTA_HOLD_CHARS - DELTA_RELEASE
