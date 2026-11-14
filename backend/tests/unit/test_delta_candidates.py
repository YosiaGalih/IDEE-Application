"""The keys whose quoted value the delta stream holds back are the keys the redactor treats as secret (code
review of 073e68d; WP-3.1f2c Goal 2): the delta's key check is built from the redactor's own
``SECRET_KEY_PATTERN``.

A key the redactor knows but the delta stream does not could let a long value go out raw while it streams
(review M3) wherever the redactor stops at the open quote. Since WP-3.1f2c the redactor hides an open quoted
value after a key to the end of the text, so the check on ``open_candidate`` itself guards the key list.
"""

from __future__ import annotations

import pytest

from app.agents.runtime import DeltaRedactor, open_candidate
from app.core.redaction import REDACTED, Redactor, is_sensitive_key
from tests.unit.test_redaction import FORMS, PLAIN_KEYS, SECRET_KEYS

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


# WP-3.1f2c Goal 2 and Goal 6: one key predicate. The delta's key check is built from SECRET_KEY_PATTERN, so
# it holds a quoted value after exactly the keys the redactor and ``is_sensitive_key`` treat as secret (camel,
# acronyms, private names, flags), from the key, and never after a key that stays readable.
QUOTED_FORMS = [form for form in FORMS if form.split("{v}")[0][-1] in "\"'"]


@pytest.mark.parametrize("form", QUOTED_FORMS)
@pytest.mark.parametrize("key", [*SECRET_KEYS, *PLAIN_KEYS])
def test_the_delta_key_check_is_the_shared_predicate(key: str, form: str) -> None:
    head = "Some text. " + form.split("{v}")[0].format(k=key)
    start = open_candidate(head + VALUE[:9])
    assert (start is not None) == is_sensitive_key(key), start
    assert start is None or start == head.index(key)
