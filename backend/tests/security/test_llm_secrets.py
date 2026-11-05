"""The provider key and the prompt never reach logs, errors or reprs (PRD §7.3.3, SECURITY_MODEL T5, AC-15).

The snapshot adapter leaked prompts through the SDK DEBUG log ("Request options"); these tests pin the fix.
"""

from __future__ import annotations

import ast
import logging
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
    provider = AnthropicClaudeProvider(KEY, http_client=fake.client())
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
    provider = AnthropicClaudeProvider(KEY, http_client=fake.client())
    with pytest.raises(LLMError) as raised:
        await provider.create_message(REQ)
    error = raised.value
    rendered = "".join(traceback.format_exception(error))
    for text in (str(error), repr(error), rendered, repr(provider)):
        assert PROMPT_MARKER not in text
        assert KEY not in text
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def test_only_the_adapter_imports_the_vendor_sdk() -> None:
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            if any(name.split(".")[0] in VENDOR_MODULES for name in names):
                offenders.append(str(path.relative_to(APP)))
    assert sorted(set(offenders)) == ["llm/anthropic_provider.py"]
