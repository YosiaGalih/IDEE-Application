"""Regex on the path to the model under hostile text: linear time, and secrets found in every shape the
builtins print them (security regressions of WP-3.1d2a: AG-13 MEDIUM-2, silent-failure delta N0/HIGH,
/code-review spec HIGH)."""

from __future__ import annotations

import time

import pytest

from app.core.redaction import Redactor
from app.tools.untrusted import neutralise

ZWSP = chr(0x200B)
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


@pytest.mark.parametrize(
    "hostile",
    [
        "<" + ZWSP * 50_000 + "x",
        "<" + " " * 50_000 + "x",
        "<" + chr(0xFFFD) * 50_000 + "x",
        ("<" + ZWSP * 300) * 200,
        ("<" + "/ " * 150) * 200,
        "<" + (ZWSP + " ") * 25_000 + "untrusted_conten",
    ],
    ids=["zwsp-run", "space-run", "replacement-run", "many-starts", "slash-space", "almost-a-tag"],
)
def test_neutralise_is_linear_on_hostile_text(hostile: str) -> None:
    # Delta reviews WP-3.1d2a N0/HIGH: adjacent overlapping quantifiers made 400 ZWSP take 9 s.
    started = time.perf_counter()
    neutralise(hostile)
    assert time.perf_counter() - started < 0.2


@pytest.mark.parametrize(
    "hostile",
    [
        "eyJ" * 87_000,
        "eyJ" + "a" * 260_000,
        ("eyJ" + "b" * 5 + ".") * 30_000,
        "-eyJ" * 65_000,
        ".-eyJ" * 52_000,
        "\n-eyJ" + "c" * 260_000,
    ],
    ids=["eyJ-run", "one-long-segment", "short-dotted-segments", "dash-run", "dot-dash-run", "dash-line"],
)
def test_the_jwt_pattern_is_linear_on_hostile_text(hostile: str) -> None:
    # AG-13 WP-3.1d2a MEDIUM-2: 256 KiB of "eyJ" took 17 s (a match attempt from every "eyJ" in a run).
    redactor = Redactor()
    started = time.perf_counter()
    redactor.redact(hostile)
    redactor.find(hostile)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    "text",
    [JWT, f"Bearer {JWT}", f'"{JWT}"', f"x={JWT}", f"a,{JWT};", f"-{JWT}", f"+{JWT}",
     f"@@ -1 +1 @@\n-{JWT}\n", f"12\t{JWT}", f"src/a.py:3: {JWT}"],
    ids=["bare", "bearer", "quoted", "assigned", "listed", "diff-removed", "diff-added", "diff-hunk",
         "read-file-line", "search-hit"],
)  # fmt: skip
def test_a_jwt_is_redacted_and_found_as_the_builtins_print_it(text: str) -> None:
    """/code-review WP-3.1d2a spec HIGH: the linear-time lookbehind also skipped a JWT right after "-", which
    is how git.diff prints a removed line."""
    redactor = Redactor()
    assert JWT not in redactor.redact(text)
    assert [finding.rule for finding in redactor.find(text)] == ["jwt"]
