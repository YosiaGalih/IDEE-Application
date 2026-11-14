"""Every redaction pattern stays linear on pathological input (spec WP-3.1f2c Goal 4).

``Redactor.redact`` runs on every outbound path, often on 64 KiB tool outputs. A pattern retried from every
position of a long run was quadratic before: "eyJ" repeated over 256 KiB took 17 s (AG-13 WP-3.1d2a
MEDIUM-2), an unbounded URL scheme 2.8 s on 64 KiB of letters (WP-3.1d1). One case per new or changed
pattern; the bound is loose for CI.
"""

from __future__ import annotations

import time

import pytest

from app.core.redaction import Redactor

KIB_64 = 65_536

CASES = [
    # The camel and acronym key forms (R-1): a long identifier with no separator, a hump at every second
    # character, a tail word repeated with nothing after it, and one tail after a long acronym.
    ("camel_run", "aB" * (KIB_64 // 2)),
    ("camel_tail_run", "secretKey" * 7_282),
    ("acronym_run_before_tail", "A" * KIB_64 + "Password="),
]


@pytest.mark.parametrize(("name", "text"), CASES, ids=[name for name, _ in CASES])
def test_pattern_is_linear(name: str, text: str) -> None:
    redactor = Redactor()
    started = time.perf_counter()
    redactor.redact(text)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"{name}: {elapsed:.2f} s"
