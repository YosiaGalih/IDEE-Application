"""``agent.delta`` redacted across deltas (spec WP-3.1f Keputusan Lead #24, #34; plan WP-3.1f2a Task 6).

The whole text of the attempt is redacted, and only the redacted text outside the last ``DELTA_HOLD_CHARS``
goes out. A secret cut across deltas, at any position and any delta size, never goes out whole or in part; the
part already sent is never rewritten; a restarted attempt drops what it held.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

import pytest

from app.agents.runtime import DELTA_HOLD_CHARS, DELTA_RELEASE_CHARS, DeltaRedactor
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
REDACTOR = Redactor([LITERAL])
BEFORE = "The model explains what it found in the repository. " * 8
AFTER = " Then it carries on with the rest of the answer." * 8


def deltas(text: str, size: int, offset: int) -> list[str]:
    """``text`` in pieces of ``size``, the first one ``offset`` long: every cut position is reached."""
    pieces = [text[:offset]] if offset else []
    pieces += [text[i : i + size] for i in range(offset, len(text), size)]
    return pieces


def stream(pieces: Iterable[str], redactor: DeltaRedactor) -> list[str]:
    out = [redactor.feed(piece) for piece in pieces]
    out.append(redactor.flush())
    return out


@pytest.mark.parametrize("name", list(SECRETS))
def test_a_secret_split_across_deltas_never_goes_out(name: str) -> None:
    secret = SECRETS[name]
    text = BEFORE + secret + AFTER
    assert secret not in REDACTOR.redact(text)  # the pattern knows it
    for size in (1, 3, 16):
        for offset in range(size):
            sent = "".join(stream(deltas(text, size, offset), DeltaRedactor(REDACTOR.redact)))
            assert secret not in sent and secret[4:-4] not in sent, (size, offset)
            assert sent == REDACTOR.redact(text)  # nothing lost either


def test_a_value_cut_after_its_key_is_still_redacted() -> None:
    for hold in (4, DELTA_HOLD_CHARS):
        redactor = DeltaRedactor(REDACTOR.redact, hold=hold, release=1)
        sent = "".join(stream(["password=ab", "cdef rest"], redactor))
        assert "cdef" not in sent and "ab" not in sent.replace("[REDACTED]", ""), hold


def test_an_unfinished_pem_block_never_leaks_its_body() -> None:
    redactor = DeltaRedactor(REDACTOR.redact)
    out = [redactor.feed(BEFORE + "-----BEGIN PRIVATE KEY-----\n")]
    body = [f"BODYLINE{i:02d}AbCdEfGh" for i in range(40)]
    out += [redactor.feed(line) for line in body]
    while_streaming = "".join(out)
    assert BEFORE.startswith(while_streaming)  # stops before BEGIN
    out.append(redactor.flush())
    sent = "".join(out)
    assert "BEGIN" not in sent and not any(line in sent for line in body)


def test_plain_text_comes_out_whole_and_in_order() -> None:
    text = BEFORE + AFTER * 3
    for size in (1, 7, 64):
        assert "".join(stream(deltas(text, size, 0), DeltaRedactor(REDACTOR.redact))) == text


def test_at_most_hold_and_one_release_step_wait() -> None:
    text = BEFORE + SECRETS["password"] + AFTER * 4
    redactor = DeltaRedactor(REDACTOR.redact)
    sent, fed = "", ""
    for piece in deltas(text, 16, 0):
        fed += piece
        sent += redactor.feed(piece)
        redacted = REDACTOR.redact(fed)
        held = len(redacted) - len(sent)
        assert held >= min(DELTA_HOLD_CHARS, len(redacted))  # the last window never goes out early
        assert held <= DELTA_HOLD_CHARS + DELTA_RELEASE_CHARS  # and no more than one release step waits
    assert len(sent) > 0


def test_the_last_window_is_never_sent_before_the_end() -> None:
    redactor = DeltaRedactor(REDACTOR.redact, release=1)
    text = BEFORE + AFTER
    sent = "".join(redactor.feed(piece) for piece in deltas(text, 5, 0))
    assert len(REDACTOR.redact(text)) - len(sent) >= DELTA_HOLD_CHARS


def test_a_restart_drops_the_held_text() -> None:
    redactor = DeltaRedactor(REDACTOR.redact, release=1)
    first = redactor.feed("A" * 300)
    assert first == "A" * (300 - DELTA_HOLD_CHARS)
    redactor.reset()
    rest = redactor.feed("B" * 10) + redactor.flush()
    assert rest == "B" * 10  # the 256 held A's were never sent


def test_flush_resets_for_the_next_attempt() -> None:
    redactor = DeltaRedactor(REDACTOR.redact)
    assert redactor.feed("one") + redactor.flush() == "one"
    assert redactor.feed("two") + redactor.flush() == "two"


def test_a_change_to_the_sent_part_stops_sending_until_the_end() -> None:
    """Lead decision #34: what was sent is never rewritten. A redaction that changes it stops the stream;
    the end sends the final redacted text from the first character that differs."""

    def redact(text: str) -> str:  # once "Z" arrives, every "a" so far reads differently
        return text.replace("a", "*") if "Z" in text else text

    redactor = DeltaRedactor(redact, hold=4, release=1)
    out = [redactor.feed("abc" * 10)]
    assert out[0] == ("abc" * 10)[:-4]
    after = [redactor.feed(piece) for piece in ("Z", "more", "text")]
    assert after == ["", "", ""]
    final = redactor.flush()
    assert final == redact("abc" * 10 + "Zmoretext")[0:]  # sent nothing that differs, so from position 0
    assert out[0] + "".join(after) == ("abc" * 10)[:-4]  # the sent prefix was never touched


def _timed(action: Callable[[], None]) -> float:
    started = time.perf_counter()
    action()
    return time.perf_counter() - started


def test_redacting_a_long_stream_stays_fast() -> None:
    text = ("The quick brown fox jumps over the lazy dog. " * 800)[:32_000]

    def run() -> None:
        assert "".join(stream(deltas(text, 16, 0), DeltaRedactor(REDACTOR.redact))) == text

    assert _timed(run) < 2.0
