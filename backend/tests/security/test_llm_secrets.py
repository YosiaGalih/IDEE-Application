"""The provider key and the prompt never reach logs, errors or reprs (PRD §7.3.3, SECURITY_MODEL T5, AC-15).

The snapshot adapter leaked prompts through the SDK DEBUG log ("Request options"); these tests pin the fix.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

from app.core.logging import configure_logging
from app.core.redaction import Redactor
from app.llm.anthropic_provider import AnthropicClaudeProvider
from app.llm.base import LLMError, LLMRequest, Message
from tests.llm_fakes import (
    FakeAnthropic,
    error_response,
    message_end,
    message_start,
    sse,
    stream_response,
    text_block,
)

APP = Path(__file__).resolve().parents[2] / "app"
KEY = "sk-ant-api03-" + "Q" * 40
PROMPT_MARKER = "PROMPT-MARKER-7f3a9c"
REQ = LLMRequest(
    model="claude-test-model", system=f"system {PROMPT_MARKER}", messages=(Message("user", PROMPT_MARKER),)
)
VENDOR_MODULES = ("anthropic", "httpx2", "httpcore2")


@pytest.fixture
def debug_logging():  # type: ignore[no-untyped-def]
    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    configure_logging("DEBUG", Redactor())
    # What the SDK does on import with ANTHROPIC_LOG=debug, after our logging was configured.
    for name in VENDOR_MODULES:
        logging.getLogger(name).setLevel(logging.DEBUG)
    yield
    root.setLevel(saved[0])
    root.handlers[:] = saved[1]


async def test_debug_logging_leaks_no_prompt_or_key(debug_logging, capsys) -> None:  # type: ignore[no-untyped-def]
    body = sse(message_start(), *text_block(0, "fine"), *message_end("end_turn"))
    fake = FakeAnthropic(lambda _: stream_response(body), lambda _: error_response(500, "api_error"))
    provider = AnthropicClaudeProvider(KEY, transport=fake.transport())
    await provider.create_message(REQ)
    with pytest.raises(LLMError):
        await provider.create_message(REQ)
    logging.getLogger("app.test").debug("a DEBUG line from our own code still prints")
    out = capsys.readouterr()
    logged = out.out + out.err
    assert "a DEBUG line from our own code still prints" in logged  # DEBUG is really on
    assert PROMPT_MARKER not in logged
    assert KEY not in logged
    assert "Q" * 40 not in logged


async def test_error_text_repr_and_traceback_carry_no_secret() -> None:
    # A 400 whose body echoes the prompt and the key, as some proxies and error pages do.
    echo = f"bad request: {PROMPT_MARKER} {KEY}"
    fake = FakeAnthropic(lambda _: error_response(400, "invalid_request_error", message=echo))
    provider = AnthropicClaudeProvider(KEY, transport=fake.transport())
    with pytest.raises(LLMError) as raised:
        await provider.create_message(REQ)
    error = raised.value
    rendered = "".join(traceback.format_exception(error))
    for text in (str(error), repr(error), rendered, repr(provider)):
        assert PROMPT_MARKER not in text
        assert KEY not in text
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def test_every_root_handler_drops_vendor_debug(debug_logging) -> None:  # type: ignore[no-untyped-def]
    from app.core.logging import QuietVendorFilter

    handlers = logging.getLogger().handlers
    assert handlers
    assert all(any(isinstance(f, QuietVendorFilter) for f in h.filters) for h in handlers)


_ORDER_PROBE = """
import json, logging, sys
order = sys.argv[1]
from app.core.logging import configure_logging
from app.core.redaction import Redactor
levels = {}
if order == "sdk-alone":
    import anthropic  # noqa: F401
elif order == "logging-first":
    configure_logging("DEBUG", Redactor())
    import app.llm.anthropic_provider  # noqa: F401
else:
    import anthropic  # noqa: F401
    configure_logging("DEBUG", Redactor())
for name in ("anthropic", "httpx2"):
    levels[name] = logging.getLevelName(logging.getLogger(name).level)
print(json.dumps(levels))
"""


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        ("sdk-alone", "DEBUG"),  # the hazard is real: ANTHROPIC_LOG=debug lowers the SDK's logger on import
        ("logging-first", "WARNING"),  # the adapter pins it again after importing the SDK
        ("sdk-first", "WARNING"),  # configure_logging pins it whichever came first
    ],
)
def test_anthropic_log_cannot_reopen_debug(order: str, expected: str) -> None:
    # A fresh interpreter per order: the SDK reads ANTHROPIC_LOG only when it is first imported.
    env = {**os.environ, "ANTHROPIC_LOG": "debug"}
    result = subprocess.run(  # noqa: S603 - fixed argv: this interpreter running a probe written above
        [sys.executable, "-c", _ORDER_PROBE, order],
        cwd=APP.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1])["anthropic"] == expected
