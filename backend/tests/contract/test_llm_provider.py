"""Both providers honour the same port (ARCHITECTURE §13.4): what the runtime sees does not depend on which
one answered. The Anthropic adapter is fed the stream the scripted scenario would produce."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.core.cancel import CancelToken
from app.llm.anthropic_provider import AnthropicClaudeProvider
from app.llm.base import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    TextBlock,
    ToolDef,
    ToolUseBlock,
)
from app.llm.scripted_provider import ScriptedProvider
from tests.llm_fakes import (
    FakeAnthropic,
    error_response,
    message_end,
    message_start,
    sse,
    stream_response,
    text_block,
    tool_block,
)

SUBMIT = ToolDef(name="submit_result", description="End the run", input_schema={"type": "object"})


def request(
    *, tools: tuple[ToolDef, ...] = (), agent_id: str = "A02", scenario: str = "generic"
) -> LLMRequest:
    return LLMRequest(
        model="claude-test-model",
        system="system",
        messages=(Message("user", "goal"),),
        tools=tools,
        metadata={"scenario": scenario, "agent_id": agent_id},
    )


def anthropic_answering_like_generic(req: LLMRequest) -> LLMProvider:
    blocks = [*text_block(0, "Scripted ", "response.")]
    stop = "end_turn"
    if any(tool.name == "submit_result" for tool in req.tools):
        blocks += tool_block(1, "toolu_scripted_0", "submit_result", "{}")
        stop = "tool_use"
    body = sse(message_start(), *blocks, *message_end(stop))
    return AnthropicClaudeProvider(
        "sk-ant-api03-x", transport=FakeAnthropic(lambda _: stream_response(body)).transport()
    )


def anthropic_failing(_: LLMRequest) -> LLMProvider:
    return AnthropicClaudeProvider(
        "sk-ant-api03-x",
        transport=FakeAnthropic(lambda _: error_response(529, "overloaded_error")).transport(),
    )


def scripted(_: LLMRequest) -> LLMProvider:
    return ScriptedProvider()


PROVIDERS: dict[str, Callable[[LLMRequest], LLMProvider]] = {
    "scripted": scripted,
    "anthropic": anthropic_answering_like_generic,
}


def assert_response_shape(response: LLMResponse, req: LLMRequest) -> None:
    assert response.model == req.model
    assert response.usage is not None
    assert response.usage.input_tokens > 0
    assert response.duration_ms >= 0
    assert response.request_id
    assert all(isinstance(block, TextBlock | ToolUseBlock) for block in response.content)


@pytest.mark.parametrize("make", PROVIDERS.values(), ids=PROVIDERS.keys())
async def test_text_reply(make: Callable[[LLMRequest], LLMProvider]) -> None:
    req = request()
    response = await make(req).create_message(req)
    assert_response_shape(response, req)
    assert response.stop_reason == "end_turn"
    assert response.content == (TextBlock("Scripted response."),)


@pytest.mark.parametrize("make", PROVIDERS.values(), ids=PROVIDERS.keys())
async def test_tool_use_reply(make: Callable[[LLMRequest], LLMProvider]) -> None:
    req = request(tools=(SUBMIT,))
    response = await make(req).create_message(req)
    assert_response_shape(response, req)
    assert response.stop_reason == "tool_use"
    assert response.content[-1] == ToolUseBlock("toolu_scripted_0", "submit_result", {})


@pytest.mark.parametrize("make", PROVIDERS.values(), ids=PROVIDERS.keys())
async def test_cancelled_before_start(make: Callable[[LLMRequest], LLMProvider]) -> None:
    token = CancelToken()
    token.cancel()
    req = request()
    with pytest.raises(LLMError) as raised:
        await make(req).create_message(req, cancel=token)
    assert raised.value.kind == "cancelled"
    assert raised.value.usage is None


@pytest.mark.parametrize(
    "make",
    [anthropic_failing, lambda _: ScriptedProvider()],
    ids=["anthropic", "scripted"],
)
async def test_overload_is_a_retryable_llm_error(make: Callable[[LLMRequest], LLMProvider]) -> None:
    req = request(scenario="faults", agent_id="overloaded")
    with pytest.raises(LLMError) as raised:
        await make(req).create_message(req)
    assert raised.value.kind == "overloaded"
    assert raised.value.retryable is True
    assert raised.value.request_id
