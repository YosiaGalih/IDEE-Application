"""The keys whose quoted value the delta stream holds back are the keys the redactor treats as secret (code
review of 073e68d): ``_OPEN_CANDIDATES`` is built from the redactor's own ``SENSITIVE_KEY_TAIL``.

A quoted value is redacted only once its closing quote arrives, so a key the redactor knows but the delta
stream does not would let a long value go out raw while it streams (review M3).
"""

from __future__ import annotations

import pytest

from app.agents.runtime import DeltaRedactor
from app.core.redaction import REDACTED, Redactor

# Every alternative of the redactor's tail, and compound names built on it.
KEYS = [
    "password", "passwords", "passwd", "passphrase", "secret", "secrets", "secret_key", "token", "api_key",
    "apikey", "access_key", "private_key", "ticket", "credential", "credentials", "authorization", "cookie",
    "cookies", "set_cookie", "session", "jwt", "bearer", "client_secret", "GITHUB_TOKEN", "db-password",
]  # fmt: skip
VALUE = "Zq7" * 6


@pytest.mark.parametrize("key", KEYS)
def test_every_key_the_redactor_treats_as_secret_opens_a_delta_candidate(key: str) -> None:
    redactor = Redactor()
    assert redactor.redact(f'{key}="{VALUE}"') != f'{key}="{VALUE}"'  # the redactor treats it as secret
    assert REDACTED in redactor.redact(f'{key}="{VALUE}"')
    deltas = DeltaRedactor(redactor.redact, hold=0, release=1)
    sent = [deltas.feed(piece) for piece in ("Some text. ", f'{key}="', VALUE[:9], VALUE[9:])]
    shown = "".join(d.text for d in sent if d is not None)
    assert "Zq7" not in shown, shown  # the open value is held from its key, not sent raw
