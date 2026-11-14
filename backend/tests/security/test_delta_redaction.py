"""``agent.delta`` redacted across deltas (spec WP-3.1f Keputusan Lead #24, #34; plan WP-3.1f2a Task 6;
reviews of 42bcb2e: M3, AG-13 INFO-3).

The whole text of the attempt is redacted, and only the redacted text outside the last ``DELTA_HOLD_CHARS``
goes out; nothing goes out from the start of an unterminated candidate (an ``eyJ`` run, an open quote after a
secret-like key, a ``-----BEGIN`` marker) until it closes or the stream ends. A secret cut across deltas,
at any position and any delta size, never goes out whole or in part. When a longer text redacts what was
already sent differently, an event with ``reset`` carries the whole redacted text so far: a client replaces
its text instead of appending.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

import pytest

from app.agents.runtime import (
    DELTA_HOLD_CHARS,
    DELTA_RELEASE_CHARS,
    Delta,
    DeltaRedactor,
    delta_redactor,
)
from app.core.redaction import Redactor

LITERAL = "kk-literal-" + "configured-" + "q" * 24  # built, not written: a test value
SECRETS = {
    "literal": LITERAL,
    "anthropic": "sk-ant-" + "api03-" + "A1b2C3d4" * 6,
    "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
    "url_credentials": "https://" + "user:pass" + "@git.example.test/x",
    "password": "password=" + "hunter2" * 3,
    "jwt": "eyJ"
    + "hbGciOiJIUzI1NiJ9"
    + ".eyJ"
    + "zdWIiOiIxMjM0NTY3ODkwIn0"
    + "."
    + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",
    "pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow" + "IBAAKCAQEA" * 6 + "\n-----END RSA PRIVATE KEY-----",
}
LONG_JWT = (
    "eyJ" + "hbGciOiJSUzI1NiJ9" + ".eyJ" + "c3ViIjoiMTIzIiwi" * 18 + "." + "Q2lnbmF0dXJl" * 22
)  # ~600 chars
LONG_PASSWORD = 'password="' + "Zq7" * 400 + '"'  # 1 210 characters, no space inside
REDACTOR = Redactor([LITERAL])
BEFORE = "The model explains what it found in the repository. " * 8
AFTER = " Then it carries on with the rest of the answer." * 8


def deltas(text: str, size: int, offset: int) -> list[str]:
    """``text`` in pieces of ``size``, the first one ``offset`` long: every cut position is reached."""
    pieces = [text[:offset]] if offset else []
    pieces += [text[i : i + size] for i in range(offset, len(text), size)]
    return pieces


def stream(pieces: Iterable[str], redactor: DeltaRedactor) -> list[Delta]:
    out = [redactor.feed(piece) for piece in pieces]
    out.append(redactor.flush())
    return [d for d in out if d is not None]


def client(events: Iterable[Delta]) -> str:
    """What a client shows (Lead decision 3.1f2c #9): one text per model call; a reset replaces its call's
    text, anything else is appended to it; the run's text is the calls' texts in call order."""
    calls: dict[int, str] = {}
    for event in events:
        calls[event.call] = event.text if event.reset else calls.get(event.call, "") + event.text
    return "".join(calls[call] for call in sorted(calls))


def raw_pieces(secret: str, width: int = 20) -> list[str]:
    body = secret[4:-4]
    return [body[i : i + width] for i in range(0, max(len(body) - width, 0) + 1)]


def everything_sent(events: Iterable[Delta]) -> str:
    """Every event's text in order: a secret cut across events is seen whole here."""
    return "".join(e.text for e in events)


def leaks(events: Iterable[Delta], secret: str) -> list[str]:
    sent = everything_sent(events)
    return [p for p in raw_pieces(secret) if p in sent]


@pytest.mark.parametrize("name", list(SECRETS))
def test_a_secret_split_across_deltas_never_goes_out(name: str) -> None:
    secret = SECRETS[name]
    text = BEFORE + secret + AFTER
    assert secret not in REDACTOR.redact(text)  # the pattern knows it
    for size in (1, 3, 16):
        for offset in range(size):
            events = stream(deltas(text, size, offset), delta_redactor(REDACTOR))
            sent = everything_sent(events)
            assert secret not in sent and secret[4:-4] not in sent, (size, offset)
            assert client(events) == REDACTOR.redact(text)  # nothing lost either


@pytest.mark.parametrize("secret", [LONG_JWT, LONG_PASSWORD], ids=["jwt-600", "quoted-password-1200"])
def test_a_long_secret_never_leaks_while_it_streams(secret: str) -> None:
    """M3: past the 256-character window a long, still unterminated candidate stays held from its start, at
    whatever point of it the whole text is redacted again."""
    text = BEFORE + secret + AFTER
    assert raw_pieces(secret)[0] not in REDACTOR.redact(text)
    for size, release in (
        (1, DELTA_RELEASE_CHARS),
        (16, DELTA_RELEASE_CHARS),
        (64, DELTA_RELEASE_CHARS),
        (16, 1),
    ):
        events = stream(deltas(text, size, 0), delta_redactor(REDACTOR, release=release))
        assert leaks(events, secret) == [], (size, release)
        assert client(events) == REDACTOR.redact(text)


def test_a_value_cut_after_its_key_is_still_redacted() -> None:
    for hold in (4, DELTA_HOLD_CHARS):
        redactor = DeltaRedactor(REDACTOR.redact, hold=hold, release=1)
        shown = client(stream(["password=ab", "cdef rest"], redactor))
        assert "cdef" not in shown and "ab" not in shown.replace("[REDACTED]", ""), hold


def test_an_unfinished_pem_block_never_leaks_its_body() -> None:
    redactor = delta_redactor(REDACTOR)
    events = [redactor.feed(BEFORE + "-----BEGIN PRIVATE KEY-----\n")]
    body = [f"BODYLINE{i:02d}AbCdEfGh" for i in range(40)]
    events += [redactor.feed(line) for line in body]
    while_streaming = client(e for e in events if e is not None)
    assert BEFORE.startswith(while_streaming)  # stops before BEGIN
    events.append(redactor.flush())
    sent = [e for e in events if e is not None]
    assert all("BEGIN" not in e.text and not any(line in e.text for line in body) for e in sent)


def test_a_begin_marker_is_held_until_its_end_marker() -> None:
    """INFO-3: a marker cut so early that the PEM pattern does not see it yet is held from its start."""
    redactor = DeltaRedactor(REDACTOR.redact, hold=8, release=1)
    pieces = [BEFORE, "-----BEGIN RSA PRI", "VATE KEY-----\nMIIE" + "x" * 50]
    events = [e for e in (redactor.feed(piece) for piece in pieces) if e is not None]
    assert all("-----BEGIN" not in e.text and "MIIE" not in e.text for e in events)  # not even for a moment
    assert client(events).startswith(BEFORE)


def test_plain_text_comes_out_whole_and_in_order() -> None:
    text = BEFORE + AFTER * 3
    for size in (1, 7, 64):
        events = stream(deltas(text, size, 0), delta_redactor(REDACTOR))
        assert not any(e.reset for e in events)
        assert "".join(e.text for e in events) == text


def test_at_most_hold_and_one_release_step_wait() -> None:
    text = BEFORE + SECRETS["password"] + AFTER * 4
    redactor = delta_redactor(REDACTOR)
    shown, fed = "", ""
    for piece in deltas(text, 16, 0):
        fed += piece
        event = redactor.feed(piece)
        if event is not None:
            shown = event.text if event.reset else shown + event.text
        redacted = REDACTOR.redact(fed)
        held = len(redacted) - len(shown)
        assert held >= min(DELTA_HOLD_CHARS, len(redacted))  # the last window never goes out early
        assert held <= DELTA_HOLD_CHARS + DELTA_RELEASE_CHARS  # and no more than one release step waits
    assert shown


def test_the_last_window_is_never_sent_before_the_end() -> None:
    redactor = delta_redactor(REDACTOR, release=1)
    text = BEFORE + AFTER
    shown = client(e for e in (redactor.feed(piece) for piece in deltas(text, 5, 0)) if e is not None)
    assert len(REDACTOR.redact(text)) - len(shown) >= DELTA_HOLD_CHARS


def test_a_restart_drops_the_held_text() -> None:
    redactor = delta_redactor(REDACTOR, release=1)
    first = redactor.feed("A" * 300)
    assert first == Delta("A" * (300 - DELTA_HOLD_CHARS), 1)
    redactor.reset()
    rest = [redactor.feed("B" * 10), redactor.flush()]
    assert [e for e in rest if e is not None] == [Delta("B" * 10, 1)]  # the 256 held A's were never sent


def test_flush_resets_for_the_next_attempt() -> None:
    redactor = delta_redactor(REDACTOR)
    assert redactor.feed("one") is None and redactor.flush() == Delta("one", 1)
    assert redactor.feed("two") is None and redactor.flush() == Delta("two", 2)


def test_a_change_to_what_was_sent_is_a_reset_with_the_whole_text() -> None:
    """INFO-3: what a longer text redacts differently is replaced, never appended to."""

    def redact(text: str) -> str:  # once "Z" arrives, every "a" so far reads differently
        return text.replace("a", "*") if "Z" in text else text

    redactor = DeltaRedactor(redact, hold=4, release=1)
    first = redactor.feed("abc" * 10)
    assert first == Delta(("abc" * 10)[:-4], 1)
    changed = redactor.feed("Zmore")
    assert changed is not None and changed.reset
    assert changed.text == redact("abc" * 10 + "Zmore")[:-4]
    events = [first, changed, redactor.feed("text"), redactor.flush()]
    assert client(e for e in events if e is not None) == redact("abc" * 10 + "Zmoretext")


def _timed(action: Callable[[], None]) -> float:
    started = time.perf_counter()
    action()
    return time.perf_counter() - started


def test_redacting_a_long_stream_stays_fast() -> None:
    text = ("The quick brown fox jumps over the lazy dog. " * 800)[:32_000]

    def run() -> None:
        assert client(stream(deltas(text, 16, 0), delta_redactor(REDACTOR))) == text

    assert _timed(run) < 2.0
