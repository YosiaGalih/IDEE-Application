from __future__ import annotations

import pytest

from app.core.config import load_settings
from app.llm.anthropic_provider import AnthropicClaudeProvider
from app.llm.base import LLMRequest, Message
from app.llm.factory import ProviderNotConfigured, build_provider
from app.llm.scripted_provider import ScriptedProvider
from tests.conftest import make_test_env
from tests.llm_fakes import FakeAnthropic, message_end, message_start, sse, stream_response, text_block

KEY = "sk-ant-api03-" + "F" * 40


def test_scripted() -> None:
    assert isinstance(build_provider(load_settings(make_test_env())), ScriptedProvider)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_anthropic_requires_a_key(key: str | None) -> None:
    env = make_test_env(LLM_PROVIDER="anthropic")
    if key is not None:
        env["ANTHROPIC_API_KEY"] = key
    with pytest.raises(ProviderNotConfigured) as raised:
        build_provider(load_settings(env))
    assert "ANTHROPIC_API_KEY" in str(raised.value)


async def test_anthropic_uses_the_configured_key_and_base_url() -> None:
    settings = load_settings(
        make_test_env(
            LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY, ANTHROPIC_BASE_URL="https://llm.example.test"
        )
    )
    fake = FakeAnthropic(
        lambda _: stream_response(sse(message_start(), *text_block(0, "x"), *message_end("end_turn")))
    )
    provider = build_provider(settings, transport=fake.transport())
    assert isinstance(provider, AnthropicClaudeProvider)
    await provider.create_message(LLMRequest(model="m", system="s", messages=(Message("user", "hi"),)))
    sent = fake.requests[0]
    assert sent.headers["x-api-key"] == KEY
    assert str(sent.url).startswith("https://llm.example.test/")


async def test_blank_base_url_reaches_the_default_host() -> None:
    settings = load_settings(
        make_test_env(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY, ANTHROPIC_BASE_URL="")
    )
    fake = FakeAnthropic(
        lambda _: stream_response(sse(message_start(), *text_block(0, "x"), *message_end("end_turn")))
    )
    provider = build_provider(settings, transport=fake.transport())
    await provider.create_message(LLMRequest(model="m", system="s", messages=(Message("user", "hi"),)))
    assert str(fake.requests[0].url).startswith("https://api.anthropic.com/")
