"""``agent.delta`` streams of a whole run (reviews of 073e68d: AG-13 D-M1, D-L1; /code-review Spec item 11).

- The open-candidate checks are linear: pathological 64 KiB attempts stream in well under a second.
- A long quoted value under a key the redactor treats as secret never streams raw, whatever its spelling:
  candidates are found in the raw text (``_NETRC`` rewrites ``password = "…`` before a candidate could be
  seen in the redacted text) and from the redactor's own key list.
- Resets are scoped to one model call (WP-3.1f2c D2-L1, Lead decision #9; replaces #44 of WP-3.1f2a): each
  delta names its call, a reset replaces that call's text only, a restart empties it, and the client shows
  the calls' texts in call order. After an attempt's first reset the rest of it waits for ``flush``, so the
  payloads stay linear in the text streamed, failed attempts included.
- WP-3.1f2c (D2-L2, D2-I1; Lead decisions #10, #11, Goal 6): a key far from its quote, a key in any spelling
  the redactor knows (camel, a flag, an argv item, one a token ran on into, a string prefix before the value),
  a long URL userinfo and a long configured literal never stream raw: no 32-character window of the secret
  reaches any payload.

``tests/security/test_delta_redaction.py`` holds the single-attempt cases.
"""

from __future__ import annotations

import json
import random
import re
import string
import time
from collections.abc import Iterable

import pytest

from app.agents.runtime import (
    DELTA_HOLD_CHARS,
    Delta,
    DeltaRedactor,
    delta_hold,
    delta_redactor,
    open_candidate,
)
from app.agents.runtime import DELTA_RELEASE_CHARS as DELTA_RELEASE
from app.core.redaction import Redactor, stopped_at_open_quote

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


def client(events: Iterable[Delta]) -> str:
    """The reference client (Lead decision 3.1f2c #9): one text per model call; a reset replaces its call's
    text, anything else is appended to it; the run's text is the calls' texts in call order."""
    calls: dict[int, str] = {}
    for event in events:
        calls[event.call] = event.text if event.reset else calls.get(event.call, "") + event.text
    return "".join(calls[call] for call in sorted(calls))


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("size", [1, 16, 64])
def test_a_long_quoted_secret_never_streams_raw_whatever_its_spelling(spelling: str, size: int) -> None:
    """D-L1: zero raw characters of the 1 200-character value in any payload."""
    text = "intro " * 40 + spelling.format(VALUE) + " tail" * 80
    events = stream(delta_redactor(REDACTOR), pieces(text, size))
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
    # AG-13 F-3 (final review of 74bf8d4): upper-case runs after a hump that never end a tail, before a quote.
    "upper runs before a quote": filled("aPASSWOR" * 4_096 + ' = "', "w"),
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
    # A header name, then 64 KiB of line breaks (LF, CRLF) before its value's quote (review M-1 of 50610b5).
    "header name, line breaks, quote": filled("Cookie:" + "\n" * 65_000, '"'),
    "header name, CRLF, quote": filled("Cookie:" + "\r\n" * 32_500, '"'),
    "header name, line breaks, last quote": "Cookie:" + "\n" * (KIB_64 - 8) + '"',
    # The header stop read after the header rule (reviews of 45c2ec7 and 08fa05e): a header value stopped at a
    # quote, then a line of markers and quotes; header lines each stopped at a quote, a marker and a quote
    # after it; key values that pair a header's stopping quote.
    "header markers and quotes": filled("Cookie: ", '[REDACTED]"'),
    "header lines stopped at quotes": filled("", 'Cookie: a b"x [REDACTED]"\n'),
    "keys pairing header stops": filled("", 'password: "Cookie: a b"x '),
}


@pytest.mark.parametrize("name", list(PATHOLOGICAL))
def test_a_pathological_64_kib_attempt_streams_in_under_a_second(name: str) -> None:
    """D-M1: the open-candidate checks are linear scans from the end, not ``\\Z``-anchored searches. The
    stream is a run's (``delta_redactor``), so its header stops are read too (review of 08fa05e)."""
    text = PATHOLOGICAL[name]
    assert len(text) == KIB_64
    started = time.perf_counter()
    events = stream(delta_redactor(REDACTOR), pieces(text, 16))
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, (name, elapsed)
    assert client(events) == REDACTOR.redact(text)


STOP_INPUTS = ["header markers and quotes", "header lines stopped at quotes", "keys pairing header stops"]


@pytest.mark.parametrize("name", STOP_INPUTS)
def test_the_inputs_aimed_at_the_header_stop_reach_it(name: str) -> None:
    """Review of 08fa05e: the timed stream reads the stop in ``Redactor.redact_with_stop``, and these inputs
    give it one to read: a cut of the text where the header rule ends a value at a quote."""
    text = PATHOLOGICAL[name]
    assert any(REDACTOR.redact_with_stop(text[:cut])[1] is not None for cut in range(16, 4_096, 16))


def changes_once_z_arrives(text: str) -> str:
    return text.replace("a", "*") if "Z" in text else text


def test_a_reset_replaces_only_its_call() -> None:
    """Lead decision #9 (D2-L1; replaces #44 of WP-3.1f2a, item 11): a reset in the second call carries the
    second call's stable text only, never the first call's, and the client keeps the first call's text."""
    redactor = DeltaRedactor(changes_once_z_arrives, hold=4, release=1)
    call_1 = "The first call has an answer. "
    first = stream(redactor, [call_1])
    second = stream(redactor, ["abc" * 10, "Zmore", "text"])
    assert {event.call for event in first} == {1} and {event.call for event in second} == {2}
    resets = [event for event in second if event.reset]
    assert resets == [Delta(changes_once_z_arrives("abc" * 10 + "Zmore")[:-4], 2, reset=True)]
    assert not any(call_1 in event.text for event in second)
    assert client(first + second) == changes_once_z_arrives(call_1) + changes_once_z_arrives(
        "abc" * 10 + "Zmoretext"
    )


def test_a_restart_voids_only_its_call() -> None:
    """Lead decision #9 (replaces #44's case of item 11, M4): a restart in the second call is an empty reset
    of that call, never of the first; the client keeps the first call's text, and a secret in the failed
    attempt reaches no payload."""
    redactor = delta_redactor(REDACTOR)
    call_1 = "Here is what I found in the repository so far. " * 8
    first = stream(redactor, pieces(call_1, 16))
    failed = stream(redactor, pieces("Second call. " * 30 + 'password="' + VALUE[:600], 16), flush=False)
    assert failed  # the failed attempt showed some of its text
    restart = redactor.restart()
    assert restart == Delta("", 2, reset=True)
    retry = stream(redactor, pieces("The retried second call. " * 20, 16))
    events = first + failed + [restart] + retry
    assert [event.call for event in events] == [1] * len(first) + [2] * (len(failed) + 1 + len(retry))
    assert "Zq7" not in "".join(event.text for event in events)
    assert client(events) == REDACTOR.redact(call_1) + REDACTOR.redact("The retried second call. " * 20)


def changes_with_each_z(text: str) -> str:
    """A fake redaction that reads every "a" differently each time a "Z" arrives: each "Z" changes what was
    already sent."""
    return text.replace("a", str(text.count("Z") % 10))


def test_one_mid_call_reset_then_hold() -> None:
    """Lead decision #9: after an attempt's first reset the rest of it waits for ``flush``. Two changes to
    what was sent in one call give exactly one reset before ``flush`` and no ``feed`` payload after it;
    ``flush`` sends the call's ``redact``; the next call streams again, numbered 2."""
    redactor = DeltaRedactor(changes_with_each_z, hold=4, release=1)
    parts = ["abc" * 10, "Z", "abc" * 10, "Z", "abc" * 10, "tail"]
    fed = [redactor.feed(part) for part in parts]
    assert fed[0] == Delta(changes_with_each_z(parts[0])[:-4], 1)
    assert fed[1] is not None and fed[1].reset and fed[1].call == 1  # the first "Z"
    assert fed[2:] == [None] * (len(parts) - 2)  # the second "Z" changes what was sent again: held
    final = redactor.flush()
    assert final is not None and final.call == 1
    events = [event for event in [*fed, final] if event is not None]
    assert client(events) == changes_with_each_z("".join(parts))
    assert redactor.feed("abc" * 10) == Delta(changes_with_each_z("abc" * 10)[:-4], 2)


def test_a_restart_starts_its_attempt_with_a_clean_hold() -> None:
    """Lead decision #9 (builder check 5): a retried attempt starts with no hold of its own, so it streams
    before ``flush`` even when the failed attempt had a reset."""
    redactor = DeltaRedactor(changes_with_each_z, hold=4, release=1)
    failed = [redactor.feed(part) for part in ["abc" * 10, "Z", "more text"]]
    assert any(event is not None and event.reset for event in failed)
    assert redactor.restart() == Delta("", 1, reset=True)
    retry = [redactor.feed(part) for part in ["The retried attempt ", "streams again."]]
    assert [event for event in retry if event is not None], "the retry is held as if it had a reset"
    assert all(event is None or (not event.reset and event.call == 1) for event in retry)


def test_a_lost_delta_marks_its_call_until_flush_or_a_restart() -> None:
    """Silent-failure review MEDIUM-2 (final review of 74bf8d4): a call marked lost ends with its whole
    ``redact`` as a reset; a mark for a call that has already ended changes nothing; a restart clears the
    mark (its own empty reset, if delivered, voids what the client had)."""
    redactor = DeltaRedactor(REDACTOR.redact, hold=4, release=1)
    first = stream(redactor, ["The first call. " * 4])
    assert not first[-1].reset
    redactor.mark_lost(1)  # call 1 has ended: its flush delta cannot be resent
    second = "The second call streams. " * 4
    assert redactor.feed(second) is not None
    redactor.mark_lost(2)
    assert redactor.flush() == Delta(REDACTOR.redact(second), 2, reset=True)
    assert redactor.feed("The third call. " * 4) is not None
    redactor.mark_lost(3)
    assert redactor.restart() == Delta("", 3, reset=True)
    retry = stream(redactor, ["The retried third call. " * 4])
    assert retry and not any(event.reset for event in retry)  # the restart cleared the mark


def test_a_retry_in_call_2_then_a_reset_in_call_3_keeps_every_call() -> None:
    """Plan Review Focus 4: a run retried in its second call, then a change to what was sent in the middle of
    its third: the reference client shows each call's ``redact`` in call order; the first call is never
    lost."""
    redactor = DeltaRedactor(changes_once_z_arrives, hold=4, release=1)
    calls = [
        "The first call. " * 4,
        "The second call, retried. " * 4,
        "abc" * 20 + "Z and the third goes on.",
    ]
    events = stream(redactor, pieces(calls[0], 5))
    events += stream(redactor, pieces("The second call's failed attempt. " * 4, 5), flush=False)
    events.append(redactor.restart())
    events += stream(redactor, pieces(calls[1], 5))
    events += stream(redactor, pieces(calls[2], 5))
    assert any(event.reset and event.call == 3 and event.text for event in events)  # mid-call, the third
    assert client(events) == "".join(changes_once_z_arrives(text) for text in calls)


CALL_KIB = 1024


def a_32_kib_call() -> str:
    """32 KiB, a "Z" ending each of its last 16 KiB: changes to what was sent from the middle on."""
    kib = "abc " * (CALL_KIB // 4)
    return "".join(kib if i < 16 else kib[:-1] + "Z" for i in range(32))


def test_payload_bytes_stay_linear() -> None:
    """D2-L1 (Lead decision #9): 40 model calls of 32 KiB, each with changes to what was sent from its middle
    on, and calls 2, 10 and 30 restarted once after 24 KiB: the payloads total at most 4 x the raw text
    streamed, failed attempts included. A run-wide reset carried every call before it (quadratic); a reset at
    every change carried the call's text again each time."""
    redactor = DeltaRedactor(changes_with_each_z)
    text = a_32_kib_call()
    events: list[Delta] = []
    total_raw = sent = 0
    for call in range(1, 41):
        attempts = [text[: 24 * CALL_KIB], text] if call in (2, 10, 30) else [text]
        for attempt, raw in enumerate(attempts, start=1):
            out = stream(redactor, pieces(raw, 64), flush=False)
            out.append(redactor.restart() if attempt < len(attempts) else redactor.flush())
            events += [event for event in out if event is not None]
            total_raw += len(raw)
            sent += sum(len(event.text) for event in out if event is not None)
            assert sent <= 4 * total_raw, (call, attempt, sent, total_raw)
    assert {event.call for event in events} == set(range(1, 41))
    assert client(events) == changes_with_each_z(text) * 40


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
    events = stream(delta_redactor(REDACTOR), pieces(text, 1))
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
    events = stream(delta_redactor(REDACTOR), pieces(text, size))
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
    events = stream(delta_redactor(REDACTOR), pieces(text, size))
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
    events = stream(delta_redactor(redactor), pieces(text, size))
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
    events = stream(delta_redactor(REDACTOR), pieces(text, size))
    assert leaked(events, VALUE) == []
    assert client(events) == REDACTOR.redact(text)


def test_a_plain_link_at_the_end_is_kept() -> None:
    """Lead decision #12: a URL with no "@" carries no credential. It is held while it may still grow, and the
    final payload at ``flush`` shows it as ``redact`` does."""
    text = INTRO + "See https://example.com"
    assert open_candidate(text) == text.index("https://")
    for size in SIZES:
        events = stream(delta_redactor(REDACTOR), pieces(text, size))
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
    events = stream(delta_redactor(REDACTOR), pieces(text, size))
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


def test_a_header_value_is_held_while_its_line_is_open() -> None:
    """A header value is held from its start while its line is the last line, whatever its quotes (review
    I-1 of 50610b5: the raw text pairs quotes differently from the redactor's); once the line ends, nothing
    is held."""
    line = "Cookie: it's here"
    assert open_candidate("Some text.\n" + line) == len("Some text.\nCookie: ")
    assert open_candidate("Some text.\n" + line + "\nThe answer goes on.") is None
    closed = 'Cookie: theme="dark"; lang=en'
    assert open_candidate(closed) == closed.index("theme")
    assert open_candidate(closed + "\nThe answer goes on.") is None
    # A header on an earlier line is final there: a quote on the last line opens nothing for it.
    assert open_candidate("Cookie: it's here\nThe 'answer") is None
    # The name and the colon may sit on earlier lines (YAML): the separator takes line breaks.
    yaml = 'cookie:\n  theme="Zq7'
    assert open_candidate(yaml) == yaml.index("theme")
    spread = 'Cookie\n\n:\n theme="Zq7'
    assert open_candidate(spread) == spread.index("theme")
    assert open_candidate('"Authorization": "Bearer abc') == 1  # the JSON form: the key candidate, as before


def test_a_quoted_flag_in_prose_does_not_hold_the_rest_of_the_answer() -> None:
    text = 'Run it with "--password" and type the password when asked.' + " Then it carries on." * 40
    redactor = delta_redactor(REDACTOR)
    shown = client(e for e in (redactor.feed(piece) for piece in pieces(text, 16)) if e is not None)
    assert len(shown) >= len(REDACTOR.redact(text)) - DELTA_HOLD_CHARS - DELTA_RELEASE


# Review I-1 of 50610b5: a URL credential or a configured literal in a header line may hold a quote or a
# backslash. The redactor reads the line with them replaced, so it pairs the line's quotes differently from
# the raw text: the delta holds the header's value while its line is open, pairing no quote.
HEADER_LINES: dict[str, tuple[str, list[str]]] = {
    "URL credentials with a quote": ('Cookie: a=https://u:p"q@h/x; b="{}"', []),
    "URL credentials with a backslash": ('Cookie: a=https://u:p\\q@h/x; b="{}"', []),
    "literal with a quote": ('Cookie: sid=Pa55"word9; theme="{}"', ['Pa55"word9']),
    "literal with a backslash": ('Cookie: sid=Pa55\\word9; theme="{}"', ["Pa55\\word9"]),
}


@pytest.mark.parametrize("line", list(HEADER_LINES))
@pytest.mark.parametrize("size", [1, 7, 16, 64, 255, 257])
def test_a_header_value_never_streams_raw_whatever_the_redactor_replaces(line: str, size: int) -> None:
    template, literals = HEADER_LINES[line]
    redactor = Redactor(literals)
    text = INTRO + template.format(VALUE) + TAIL
    assert not any(window in redactor.redact(text) for window in windows(VALUE))  # the redactor takes it
    assert held_from(text, VALUE) == text.index("Cookie: ") + len("Cookie: ")
    events = stream(delta_redactor(redactor), pieces(text, size))
    assert leaked(events, VALUE) == []
    assert client(events) == redactor.redact(text)


# Review round 2 of 0733969: a parameter's quote carries a header value onto later lines, where a quote that
# is not a parameter's may stop it while its line is open. The delta holds from the value's marker in the
# redacted text, wherever the header's name is, and pairs no quote of the raw text.
HEADER_CONTINUATIONS: dict[str, tuple[str, list[str]]] = {
    "a quote after the parameter's line": ('Cookie: a="x\ny" z"{}" w', []),
    "a quoted part after the parameter": ('Cookie: a="x\ny"; "{}"', []),
    "digest parameters": ('Authorization: Digest realm="a\n b", "{}"', []),
    "a literal with a quote on the continuation": ('Cookie: a="x\ny" Pa55"word9 z"{}" w', ['Pa55"word9']),
    "single quotes": ("Cookie: a='x\ny' z'{}' w", []),
}


@pytest.mark.parametrize("line", list(HEADER_CONTINUATIONS))
@pytest.mark.parametrize("size", [1, 7, 16, 64, 255, 257])
def test_a_header_value_carried_onto_later_lines_never_streams_raw(line: str, size: int) -> None:
    template, literals = HEADER_CONTINUATIONS[line]
    redactor = Redactor(literals)
    text = INTRO + template.format(VALUE) + TAIL
    assert not any(window in redactor.redact(text) for window in windows(VALUE))  # the redactor takes it
    events = stream(delta_redactor(redactor), pieces(text, size))
    assert leaked(events, VALUE) == []
    assert client(events) == redactor.redact(text)


def test_a_value_stopped_at_a_quote_is_held_only_while_its_line_is_open() -> None:
    assert stopped_at_open_quote('Cookie: [REDACTED]"abc') == len("Cookie: ")
    assert stopped_at_open_quote("x-api-key: [REDACTED]'a") == len("x-api-key: ")
    assert stopped_at_open_quote('Cookie: [REDACTED]"abc\nThe answer goes on.') is None
    assert stopped_at_open_quote("Cookie: [REDACTED] done") is None
    text = 'Cookie: a b"q\n' + "The answer goes on after it. " * 60
    redactor = delta_redactor(REDACTOR)
    shown = client(e for e in (redactor.feed(piece) for piece in pieces(text, 16)) if e is not None)
    assert len(shown) >= len(REDACTOR.redact(text)) - DELTA_HOLD_CHARS - DELTA_RELEASE


# Review round 3 of 45c2ec7: the rules after the header rule may pair the header's stopping quote or take its
# marker (a key's quoted value, an argv item, netrc's word, the #7 swallow), so the redacted text no longer
# shows it. The stop is read on the text as the header rule leaves it (Redactor.redact_with_stop).
LATER_RULES = {
    "a key's quoted value": 'password: "Cookie: a="x\ny" z"{}" w"',
    "a key's single-quoted value": "token = 'Cookie: a=\"x\ny\" z'{}' w'",
    "an argv item": '"--password", "Cookie: a="x\ny" z"{}" w"',
    # netrc's word is never a bare key with its own value (AG-13 F-2): a quoted header name keeps it netrc's.
    "netrc's word": 'password "Cookie":a="x\ny" z" {}" w',
    "netrc's key before a header": 'password Cookie:a="x\ny" z" {}" w',
    "the #7 swallow": 'password=q=" Cookie: a="x\ny" z"{}" w"',
}


@pytest.mark.parametrize("form", list(LATER_RULES))
@pytest.mark.parametrize("size", [1, 7, 16, 64, 255, 257])
def test_a_header_stop_a_later_rule_rewrites_never_streams_raw(form: str, size: int) -> None:
    text = INTRO + LATER_RULES[form].format(VALUE) + TAIL
    assert not any(window in REDACTOR.redact(text) for window in windows(VALUE))  # the redactor takes it
    events = stream(delta_redactor(REDACTOR), pieces(text, size))
    assert leaked(events, VALUE) == []
    assert client(events) == REDACTOR.redact(text)


# AG-13 F-1 (final review of 74bf8d4): a value ending in a backslash before its quote, then the next key's
# quoted value on the next line. The redactor closes the first value on its own line, so the second is its own
# match, and the stream holds it from its key.
BACKSLASH_BEFORE_A_CLOSE = {
    "shell single quotes": "export DB_PASSWORD='aB3\\'\nexport GITHUB_TOKEN='{}'\n",
    "yaml single quotes": "db:\n  password: 'p4ss\\'\n  api_key: '{}'\n",
    "double quotes": 'PASSWORD="ab\\"\nTOKEN="{}"\n',
}


@pytest.mark.parametrize("form", list(BACKSLASH_BEFORE_A_CLOSE))
@pytest.mark.parametrize("size", SIZES)
def test_a_value_after_a_backslash_before_a_close_never_streams_raw(form: str, size: int) -> None:
    text = INTRO + BACKSLASH_BEFORE_A_CLOSE[form].format(VALUE) + TAIL
    assert not any(window in REDACTOR.redact(text) for window in windows(VALUE))  # the redactor takes it
    events = stream(delta_redactor(REDACTOR), pieces(text, size))
    assert leaked(events, VALUE) == []
    assert client(events) == REDACTOR.redact(text)


# AG-13 F-8 (final review of 74bf8d4): a key after an escaped line break or tab in a JSON-dumped tool output
# (ToolGateway._content_text). The redactor reads the key; the stream holds its quoted value from it: the
# delta's key check has no left boundary, so the "n" or "t" of the escape before the key changes nothing.
ESCAPED_BREAK_OUTPUTS = {
    "quoted value": ('x\npassword="{}"', "password"),
    "camel key": ('x\nclientSecret: "{}"', "clientSecret"),
    "tab, single quotes": ("[db]\n\tsecret = '{}'", "secret"),
    "bare value": ("x\npassword={}", None),
}


@pytest.mark.parametrize("output", list(ESCAPED_BREAK_OUTPUTS))
@pytest.mark.parametrize("size", SIZES)
def test_a_key_after_an_escaped_line_break_never_streams_raw(output: str, size: int) -> None:
    template, key = ESCAPED_BREAK_OUTPUTS[output]
    text = json.dumps({"stdout": INTRO + template.format(VALUE) + TAIL})
    if key is not None:  # held from the key (or from the escape's letter glued to it)
        assert held_from(text, VALUE) in (text.index(key), text.index(key) - 1)
    assert not any(window in REDACTOR.redact(text) for window in windows(VALUE))  # the redactor takes it
    events = stream(delta_redactor(REDACTOR), pieces(text, size))
    assert leaked(events, VALUE) == []
    assert client(events) == REDACTOR.redact(text)


# The reviewer's per-prefix fuzz (sfuzz2.py) in short: a header value carried onto a later line by a
# parameter's quote, among the later rules' atoms (keys, flags, argv items, netrc, the #7 swallow, markers,
# literals with a quote or a backslash, URL credentials). Every release is a redaction (release 1), so every
# cut is checked.
FUZZ_VALUE = "Zq7" * 120
FUZZ_LITERALS = ['Pa55"word9', "Pa55\\word9", "Pa55'word9"]
FUZZ_HEADS = [
    "Cookie: ",
    "Cookie:",
    "Authorization: Digest ",
    "x-api-key: ",
    "Set-Cookie: ",
    '"Cookie": "',
    "cookie:\n  ",
]
FUZZ_ATOMS = [
    '"', "'", "=", '=" ', '="', "='", " ", ";", ",", "\\", "\n", "\r\n", "a", "b=", "z", 'Pa55"word9',
    "Pa55\\word9", "Pa55'word9", 'https://u:p"q@h/x', "--token ", "Cookie: ", "Authorization: ", "[REDACTED]",
    "token=", "password=", "password: ", "--password ", '"--password" ', '"--password", ', "password ",
    "password Cookie:", "password=q=", "ghp_" + "a1B2" * 9, "it's", '""', "x\"y", "b='",
]  # fmt: skip


def _continued_header(rnd: random.Random) -> str:
    def atoms(most: int) -> str:
        return "".join(rnd.choice(FUZZ_ATOMS) for _ in range(rnd.randint(0, most)))

    head = (
        rnd.choice(FUZZ_HEADS)
        + rnd.choice(['a="x', "a='x", 'realm="a', 'b="p'])
        + rnd.choice(["\n", "\r\n", "\n "])
    )
    return (
        "intro "
        + atoms(3)
        + head
        + atoms(6)
        + rnd.choice(['"', "'", 'y"', ""])
        + atoms(4)
        + FUZZ_VALUE
        + atoms(4)
    )


def test_no_cut_of_a_continued_header_streams_its_value_raw() -> None:
    redactor = Redactor(FUZZ_LITERALS)
    rnd = random.Random(45)  # noqa: S311 - a reproducible fuzz, not a secret
    for _ in range(150):
        text = _continued_header(rnd)
        if any(window in redactor.redact(text) for window in windows(FUZZ_VALUE)):
            continue  # the redactor itself keeps the value: nothing for the stream to keep back
        # A run's stream, but every piece is a release: every cut of the text is checked.
        deltas = delta_redactor(redactor, release=1)
        events = stream(deltas, pieces(text, 4))
        assert leaked(events, FUZZ_VALUE) == [], text
        assert client(events) == redactor.redact(text), text


def a_64_kib_attempt_reset_early() -> str:
    """64 KiB: 600 characters, then a "Z" (a change to what was sent, so a reset at the next release), then a
    "Z" ending every KiB to the end: each would change what was sent again."""
    head = "abc " * 150 + "Z"
    rest = "".join(("abc " * (CALL_KIB // 4))[:-1] + "Z" for _ in range(KIB_64 // CALL_KIB))
    return (head + rest)[:KIB_64]


def test_a_held_64_kib_attempt_streams_in_under_a_second() -> None:
    """Global constraint (linear, < 1.0 s at 64 KiB) on the held branch of ``feed`` (Lead decision #9; review
    of 52e0679): an attempt reset early, then fed one character at a time to 64 KiB, costs one append per
    character until ``flush``, never a redaction."""
    text = a_64_kib_attempt_reset_early()
    parts = pieces(text, 1)
    redactor = DeltaRedactor(changes_with_each_z)
    started = time.perf_counter()
    events = stream(redactor, parts)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, elapsed
    resets = [index for index, event in enumerate(events) if event.reset]
    assert resets[0] < len(events) - 1  # reset early: the rest was held until flush
    assert len(resets) == 2 and resets[1] == len(events) - 1  # the flush is the only other reset
    assert client(events) == changes_with_each_z(text)


def test_a_retry_after_a_held_attempt_streams_64_kib_in_under_a_second() -> None:
    """The same bound when a restart follows the held state (Lead decision #9, builder check 5): the failed
    attempt is held after its early reset; the retry starts with a clean hold and streams its 64 KiB, one
    character at a time, at the usual release steps."""
    held = pieces(a_64_kib_attempt_reset_early(), 1)
    retry_text = ("abc " * (KIB_64 // 4))[:KIB_64]
    retry = pieces(retry_text, 1)
    redactor = DeltaRedactor(changes_with_each_z)
    started = time.perf_counter()
    failed = stream(redactor, held, flush=False)
    restart = redactor.restart()
    streamed = stream(redactor, retry, flush=False)
    final = redactor.flush()
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, elapsed
    assert any(event.reset for event in failed) and restart == Delta("", 1, reset=True)
    assert streamed and not any(event.reset for event in streamed)  # streamed before flush, never held
    assert client([*failed, restart, *streamed, *([final] if final else [])]) == changes_with_each_z(
        retry_text
    )


# --- the AG-13 delta 2 fuzz (poc_fuzz.py), with WP-3.1f2c's candidates and per-call resets (Goal 6) ---

FUZZ_SEED = 20261114
FUZZ_CASES = 2_000
ALNUM = string.ascii_letters + string.digits
FUZZ_KEYS = [
    "password", "db_password", "secret", "secrets", "client_secret", "token", "api_key", "apikey",
    "access_key", "private_key", "ticket", "credentials", "cookie", "session", "jwt", "bearer", "passphrase",
    "clientSecret", "accessToken", "SMTPPassword",
]  # fmt: skip
FUZZ_SEPS = ["=", ": ", " = ", " : ", "=\n", ":\t"]
FUZZ_FILLER = [
    "the", "value", "is", "=", ":", "'", '"', "eyJ", "-----", "BEGIN", "END", "x", "\n", "{", "}", ",",
    "password", "token", "http://", "@", "-", ".", "\\", "a_b", "Bearer",
]  # fmt: skip
# Configured literals of 300, 800 and 1 500 characters: the hold follows the longest (Lead decision #11).
_LITERAL_RNG = random.Random(FUZZ_SEED + 1)  # noqa: S311 - reproducible test values, not secrets
FUZZ_LONG_LITERALS = ["".join(_LITERAL_RNG.choices(ALNUM, k=n)) for n in (300, 800, 1_500)]
FUZZ_REDACTOR = Redactor(FUZZ_LONG_LITERALS)
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]{32,}")


def _value(rng: random.Random, low: int, high: int) -> str:
    return "".join(rng.choices(ALNUM, k=rng.randint(low, high)))


def _gap(rng: random.Random) -> str:
    """Up to 200 spaces, tabs or line breaks between a key's separator and its quote (Lead decision #10)."""
    return "".join(rng.choices(" \t\n", k=rng.randint(0, 200)))


def _secret_piece(rng: random.Random) -> tuple[str, str]:
    """A secret in one of the forms the redactor takes, and the secret (alphanumeric, 300 to 1 500)."""
    kind = rng.randrange(9)
    value = _value(rng, 300, 1_500)
    key = rng.choice(FUZZ_KEYS)
    if kind == 0:  # quoted under a key, JSON-like or not, the quote up to 200 characters after the separator
        quote = rng.choice(['"', "'"])
        name = f'"{key}"' if rng.random() < 0.4 else key
        return f"{name}{rng.choice(FUZZ_SEPS)}{_gap(rng)}{quote}{value}{quote}", value
    if kind == 1:  # escaped JSON
        return f'\\"{key}\\": \\"{value}\\"', value
    if kind == 2:  # a bare value
        return f"{key}={value}", value
    if kind == 3:  # a header
        return rng.choice(["Authorization: Bearer ", "Cookie: ", "x-api-key: "]) + value, value
    if kind == 4:  # a JWT
        return f"eyJ{value[:400]}.eyJ{value[400:800] or 'abcdefgh'}.{value[800:] or 'S' * 20}", value[:400]
    if kind == 5:  # a PEM block
        return f"-----BEGIN RSA PRIVATE KEY-----\n{value}\n-----END RSA PRIVATE KEY-----", value
    if kind == 6:  # an Anthropic key
        return f"sk-ant-api03-{value}", value
    if kind == 7:  # URL userinfo longer than the hold (no longer left out: Lead decision #11)
        userinfo = rng.choice([f"user:{value}", value])
        return f"https://{userinfo}@git.example.com/r.git", value
    literal = rng.choice(FUZZ_LONG_LITERALS)  # a configured literal longer than DELTA_HOLD_CHARS
    return literal, literal


def _filler(rng: random.Random, words: int) -> str:
    return " ".join(rng.choice(FUZZ_FILLER) for _ in range(words))


def _fuzz_text(rng: random.Random) -> tuple[str, list[str]]:
    parts, secrets = [_filler(rng, rng.randint(0, 80))], []
    for _ in range(rng.randint(1, 4)):
        piece, secret = _secret_piece(rng)
        parts += [" ", piece, " ", _filler(rng, rng.randint(0, 80))]
        secrets.append(secret)
    return "".join(parts), secrets


def _fuzz_feed(redactor: DeltaRedactor, text: str, rng: random.Random) -> list[Delta]:
    out, at = [], 0
    while at < len(text):
        size = rng.choice([1, 3, 16, 16, 64, 200])
        out.append(redactor.feed(text[at : at + size]))
        at += size
    return [event for event in out if event is not None]


def secret_windows(events: list[Delta], secrets: list[str]) -> list[str]:
    """The 32-character windows of ``secrets`` (alphanumeric) found in any payload, or across payloads: each
    lies in an alphanumeric run of 32 or more, so only those runs are read."""
    texts = [event.text for event in events]
    found = set()
    for text in [*texts, "".join(texts)]:
        for run in _ALNUM_RUN.findall(text):
            for at in range(len(run) - WINDOW + 1):
                window = run[at : at + WINDOW]
                if any(window in secret for secret in secrets):
                    found.add(window)
    return sorted(found)


def test_fuzz_no_secret_window_in_any_payload() -> None:
    """Goal 6, AG-13 delta 2's fuzz (poc_fuzz.py), with the keys of R-1, key-to-quote gaps up to 200, long URL
    userinfo and long configured literals no longer left out: runs of one to three model calls, each call's
    attempt restarted at a random cut half of the time, streamed through a run's ``delta_redactor``. No
    32-character window of a secret the redactor takes reaches any payload, resets and restarts included, and
    the reference client ends with each call's ``redact`` in call order."""
    rng = random.Random(FUZZ_SEED)  # noqa: S311 - a reproducible fuzz, not a secret
    checked = restarts = 0
    for case in range(FUZZ_CASES):
        redactor = delta_redactor(FUZZ_REDACTOR)
        events: list[Delta] = []
        secrets: list[str] = []
        expected = ""
        for _ in range(rng.randint(1, 3)):
            if rng.random() < 0.5:  # a failed attempt that streamed, then its restart
                failed, failed_secrets = _fuzz_text(rng)
                events += _fuzz_feed(redactor, failed[: rng.randint(1, len(failed))], rng)
                events.append(redactor.restart())
                restarts += 1
                secrets += [s for s in failed_secrets if s not in FUZZ_REDACTOR.redact(failed)]
            text, text_secrets = _fuzz_text(rng)
            events += _fuzz_feed(redactor, text, rng)
            if (final := redactor.flush()) is not None:
                events.append(final)
            expected += FUZZ_REDACTOR.redact(text)
            secrets += [s for s in text_secrets if s not in FUZZ_REDACTOR.redact(text)]
        assert client(events) == expected, case
        assert secret_windows(events, secrets) == [], case
        checked += len(secrets)
    assert checked > 5 * FUZZ_CASES and restarts > FUZZ_CASES / 2  # the fuzz is not vacuous
