"""``ModelCall``: one constructor of the budget bound and the ledger context (spec WP-3.1f Keputusan Lead #17;
AG-13 3.1f1 L1; plan WP-3.1f2a Task 3)."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import cast, get_args

import pytest

from app.agents.runtime import (
    HEARTBEAT_INTERVAL_S,
    SUBMIT_RESULT,
    ModelCall,
    RunErrorCode,
    RunScope,
    request_size_bytes,
)
from app.llm.base import LLMRequest, Message, TextBlock, ToolDef, ToolResultBlock, ToolUseBlock
from app.models.enums import AGENT_RUN_ERROR_CODES
from app.services.agent_runtime import bind_model_call
from app.tools.gateway import TERMINAL_TOOL
from app.workflow.lease import HEARTBEAT_SECONDS
from tests.runtime_fakes import a_model_call, a_scope

TOOL = ToolDef(
    "repo.read_file", "Read one file.", {"type": "object", "properties": {"path": {"type": "string"}}}
)
CALLS = [
    a_model_call(),
    a_model_call(tools=(TOOL,)),
    a_model_call(system="Système naïve 日本語", messages=(Message("user", "héllo ✓"),)),
    a_model_call(
        messages=(
            Message("user", "read it"),
            Message("assistant", (TextBlock("ok"), ToolUseBlock("toolu_1", "repo.read_file", {"path": "a"}))),
            Message("user", (ToolResultBlock("toolu_1", "contents", is_error=False),)),
        ),
        max_tokens=4096,
    ),
]


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


@pytest.mark.parametrize("call", CALLS)
def test_bound_and_ledger_context_come_from_one_call(call: ModelCall) -> None:
    bound = bind_model_call(call)
    assert bound.bound.model == call.request.model == call.model
    assert bound.bound.input_token_bound == bound.ledger.input_token_bound == request_size_bytes(call.request)
    assert bound.bound.output_token_cap == bound.ledger.output_token_cap == call.request.max_tokens
    scope = call.scope
    assert (
        bound.ledger.project_id,
        bound.ledger.task_id,
        bound.ledger.agent_run_id,
        bound.ledger.agent_id,
        bound.ledger.call_id,
    ) == (scope.project_id, scope.task_id, scope.agent_run_id, scope.agent_id, call.call_id)


def test_input_bound_is_the_utf8_size_of_the_request() -> None:
    plain = a_model_call(system="x")
    accented = a_model_call(system="x" + "é" * 100)
    assert accented.input_token_bound - plain.input_token_bound == 200


def test_input_bound_counts_tools_and_every_block() -> None:
    base = a_model_call()
    with_tool = a_model_call(tools=(TOOL,))
    assert with_tool.input_token_bound - base.input_token_bound >= _json_size(
        {"name": TOOL.name, "description": TOOL.description, "input_schema": TOOL.input_schema}
    )
    use = ToolUseBlock("toolu_1", "repo.read_file", {"path": "src/app.py"})
    result = ToolResultBlock("toolu_1", "x" * 500, is_error=True)
    with_use = a_model_call(messages=(Message("user", "hi"), Message("assistant", (use,))))
    with_result = a_model_call(
        messages=(Message("user", "hi"), Message("assistant", (use,)), Message("user", (result,)))
    )
    assert with_use.input_token_bound - base.input_token_bound >= _json_size(
        {"id": use.id, "name": use.name, "input": use.input}
    )
    assert with_result.input_token_bound - with_use.input_token_bound >= _json_size(
        {"tool_use_id": result.tool_use_id, "content": result.content, "is_error": True}
    )


def test_output_cap_is_max_tokens() -> None:
    for max_tokens in (1, 1024, 8192):
        call = a_model_call(max_tokens=max_tokens)
        assert call.output_token_cap == max_tokens == bind_model_call(call).bound.output_token_cap


def test_a_model_call_needs_a_task() -> None:
    with pytest.raises(TypeError):
        a_scope(task_id=cast(uuid.UUID, None))
    with pytest.raises(TypeError):
        a_scope(task_id=cast(uuid.UUID, str(uuid.uuid4())))
    # A scope built around the check is still refused by the call itself.
    scope = a_scope()
    object.__setattr__(scope, "task_id", None)
    with pytest.raises(TypeError):
        a_model_call(scope)


def test_call_id_must_be_in_the_request_metadata() -> None:
    call = a_model_call()
    with pytest.raises(ValueError, match="call_id"):
        ModelCall(request=call.request, call_id=uuid.uuid4(), scope=call.scope)
    without = replace(call.request, metadata={"agent_id": "B14"})
    with pytest.raises(ValueError, match="call_id"):
        ModelCall(request=without, call_id=call.call_id, scope=call.scope)


def test_a_model_call_is_frozen_and_its_bound_is_derived() -> None:
    call = a_model_call()
    with pytest.raises(AttributeError):
        call.input_token_bound = 0  # type: ignore[misc]
    request = LLMRequest(
        model="m", system="s", messages=(Message("user", "x"),), metadata={"call_id": str(call.call_id)}
    )
    assert ModelCall(request=request, call_id=call.call_id, scope=call.scope).input_token_bound == (
        request_size_bytes(request)
    )


def test_run_error_codes_match_the_model() -> None:
    assert get_args(RunErrorCode) == AGENT_RUN_ERROR_CODES


def test_submit_result_is_the_gateway_terminal_tool() -> None:
    assert SUBMIT_RESULT == TERMINAL_TOOL


def test_heartbeat_interval_is_the_lease_heartbeat() -> None:
    assert HEARTBEAT_INTERVAL_S == HEARTBEAT_SECONDS


def test_scope_task_id_is_required_by_type() -> None:
    assert RunScope.__dataclass_fields__["task_id"].type in ("uuid.UUID", uuid.UUID)
