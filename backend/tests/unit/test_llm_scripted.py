from __future__ import annotations

from typing import get_args

import pytest

from app.core.cancel import CancelToken
from app.llm.base import (
    ErrorKind,
    LLMError,
    LLMRequest,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from app.llm.scenarios import SCENARIOS
from app.llm.scenarios.model import Fail, Reply, Scenario
from app.llm.scripted_provider import ScriptedProvider

SUBMIT = ToolDef(name="submit_result", description="end the run", input_schema={"type": "object"})
READ = ToolDef(name="repo.read_file", description="read", input_schema={"type": "object"})

TWO_TURNS = Scenario(
    name="two-turns",
    default=(
        Reply((TextBlock("first"),), usage=Usage(input_tokens=10, output_tokens=1)),
        Reply((TextBlock("second"),), usage=Usage(input_tokens=20, output_tokens=2)),
    ),
    scripts={"B14": (Reply((TextBlock("implementer"),)),)},
)
LONG = Scenario(name="long", default=(Reply((TextBlock("x" * 100),), usage=Usage(30, 40, 5, 6)),))


def request(*history: Message, scenario: str = "two-turns", agent_id: str = "A02", tools=()) -> LLMRequest:  # type: ignore[no-untyped-def]
    return LLMRequest(
        model="claude-test-model",
        system="system",
        messages=(Message("user", "goal"), *history),
        tools=tools,
        metadata={"scenario": scenario, "agent_id": agent_id},
    )


def provider() -> ScriptedProvider:
    return ScriptedProvider({**SCENARIOS, "two-turns": TWO_TURNS, "long": LONG}, delta_size=8)


async def test_turn_is_derived_from_history_not_from_state() -> None:
    first = await provider().create_message(request())
    after_one_turn = request(
        Message("assistant", (ToolUseBlock("toolu_1", "repo.read_file", {"path": "a"}),)),
        Message("user", (ToolResultBlock("toolu_1", "content"),)),
    )
    # A fresh provider (as after a worker restart) answers the same history the same way.
    second = await provider().create_message(after_one_turn)
    assert first.content == (TextBlock("first"),)
    assert second.content == (TextBlock("second"),)
    assert first.usage == Usage(input_tokens=10, output_tokens=1)
    assert second.request_id == "scripted-two-turns-1"
    assert first.model == "claude-test-model"


async def test_past_the_end_repeats_the_last_turn() -> None:
    history = [Message("assistant", "a"), Message("user", "b")] * 3
    response = await provider().create_message(request(*history))
    assert response.content == (TextBlock("second"),)


async def test_agent_script_is_chosen_by_agent_id() -> None:
    response = await provider().create_message(request(agent_id="B14"))
    assert response.content == (TextBlock("implementer"),)


async def test_deltas_concatenate_to_the_text() -> None:
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    response = await provider().create_message(request(scenario="long"), on_text_delta=on_delta)
    assert len(deltas) == 13  # 100 characters in deltas of 8
    assert "".join(deltas) == "x" * 100 == response.content[0].text  # type: ignore[union-attr]


async def test_cancel_between_deltas_reports_input_tokens_only() -> None:
    token = CancelToken()
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)
        token.cancel()

    with pytest.raises(LLMError) as raised:
        await provider().create_message(request(scenario="long"), on_text_delta=on_delta, cancel=token)
    assert raised.value.kind == "cancelled"
    assert deltas == ["x" * 8]
    # The prompt was "read", the answer was cut: input and cache tokens count, output does not.
    assert raised.value.usage == Usage(
        input_tokens=30, output_tokens=0, cache_creation_tokens=5, cache_read_tokens=6
    )


async def test_cancel_before_start_has_no_usage() -> None:
    token = CancelToken()
    token.cancel()
    with pytest.raises(LLMError) as raised:
        await provider().create_message(request(scenario="long"), cancel=token)
    assert raised.value.kind == "cancelled"
    assert raised.value.usage is None


@pytest.mark.parametrize("kind", get_args(ErrorKind))
async def test_fault_kinds(kind: ErrorKind) -> None:
    with pytest.raises(LLMError) as raised:
        await provider().create_message(request(scenario="faults", agent_id=kind))
    assert raised.value.kind == kind
    assert raised.value.request_id == "scripted-faults-0"
    if kind == "rate_limited":
        assert raised.value.retry_after_s == 1.0


async def test_faults_without_a_known_agent_is_a_plain_reply() -> None:
    response = await provider().create_message(request(scenario="faults", agent_id="A02"))
    assert response.stop_reason == "end_turn"


async def test_unknown_scenario_is_bad_request() -> None:
    with pytest.raises(LLMError) as raised:
        await provider().create_message(request(scenario="nope"))
    assert raised.value.kind == "bad_request"


async def test_default_scenario_is_generic() -> None:
    req = LLMRequest(model="m", system="s", messages=(Message("user", "goal"),))
    response = await ScriptedProvider().create_message(req)
    assert response.content == (TextBlock("Scripted response."),)
    assert response.request_id == "scripted-generic-0"


async def test_requests_are_recorded() -> None:
    scripted = provider()
    req = request()
    await scripted.create_message(req)
    with pytest.raises(LLMError):
        await scripted.create_message(request(scenario="nope"))
    assert scripted.requests[0] is req
    assert len(scripted.requests) == 2


async def test_generic_calls_submit_result_only_when_offered() -> None:
    offered = await provider().create_message(request(scenario="generic", tools=(READ, SUBMIT)))
    assert offered.stop_reason == "tool_use"
    assert offered.content[-1] == ToolUseBlock("toolu_scripted_0", "submit_result", {})
    plain = await provider().create_message(request(scenario="generic", tools=(READ,)))
    assert plain.stop_reason == "end_turn"
    assert all(isinstance(block, TextBlock) for block in plain.content)


async def test_turn_function_receives_the_request() -> None:
    def echo(req: LLMRequest) -> Reply | Fail:
        return Reply((TextBlock(f"{len(req.tools)} tools"),))

    scripted = ScriptedProvider({"echo": Scenario(name="echo", default=(echo,))})
    response = await scripted.create_message(request(scenario="echo", tools=(READ, SUBMIT)))
    assert response.content == (TextBlock("2 tools"),)
