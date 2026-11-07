"""Credentials in URLs are redacted wherever the URL starts (SECURITY_MODEL §6.2, T5).

Regression for WP-3.1d1 (AG-13 HIGH): a lookbehind added to make the pattern linear stopped redacting a URL
preceded by "+", "-", ".", a digit or a long run of letters, which is exactly how a unified diff line starts.
"""

from __future__ import annotations

import time

import pytest

from app.core.redaction import Redactor

SECRET = "Pw0rdXyzSecret"  # gitleaks:allow (synthetic)


@pytest.mark.parametrize(
    "text",
    [
        f"https://alice:{SECRET}@host/x",
        f"+https://alice:{SECRET}@host/x",
        f"-postgresql+asyncpg://kk:{SECRET}@db/kk",
        f"@@ -1 +1 @@\n-https://old:{SECRET}@h/x\n+https://new@h/x",
        f"...https://alice:{SECRET}@host",
        f"1.https://alice:{SECRET}@host",
        "A" * 40 + f"https://alice:{SECRET}@host",
        f"+redis://:{SECRET}@cache:6379/0",
        f"prefixhttps://user:{SECRET}@example.test/x",
    ],
)
def test_url_credentials_are_redacted_whatever_precedes_them(text: str) -> None:
    assert SECRET not in Redactor().redact(text)


def test_the_url_pattern_stays_linear_on_long_runs() -> None:
    redactor = Redactor()
    started = time.perf_counter()
    for chunk in ("a", "a+", "a://b:", "9.", "-"):
        redactor.redact(chunk * (262_144 // len(chunk)))
    assert time.perf_counter() - started < 2.0
