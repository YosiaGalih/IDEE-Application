"""``agent.delta`` streams of a whole run (reviews of 073e68d: AG-13 D-M1, D-L1; /code-review Spec item 11).

- The open-candidate checks are linear: pathological 64 KiB attempts stream in well under a second.
- A long quoted value under a key the redactor treats as secret never streams raw, whatever its spelling:
  candidates are found in the raw text (``_NETRC`` rewrites ``password = "…`` before a candidate could be
  seen in the redacted text) and from the redactor's own key list.
- A reset carries the run's whole text shown so far: the calls already ended, then the current call.

``tests/security/test_delta_redaction.py`` holds the single-attempt cases.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

import pytest

from app.agents.runtime import DELTA_RELEASE_CHARS as DELTA_RELEASE
from app.agents.runtime import Delta, DeltaRedactor
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


PATHOLOGICAL = {
    # An open quote pins the origin while ended eyJ runs follow: each release used to rescan every run.
    "eyJ runs": filled('password="' + "eyJ" * 10_000 + " ", "w"),
    "BEGIN markers": filled('password="' + "-----BEGIN" * 3_000 + "-----END x", "w"),
    "keys and quotes": filled('password="a" ' * 4_000 + 'password="', "w"),
    "open quote": filled('password="', "w"),
    "open token": filled("eyJ", "A"),
    "open BEGIN": filled("-----BEGIN PRIVATE KEY-----\n", "MIIE"),
    "plain": filled("", "The quick brown fox jumps over the lazy dog. "),
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
