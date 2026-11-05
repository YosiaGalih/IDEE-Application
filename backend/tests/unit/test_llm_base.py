from __future__ import annotations

import asyncio

import pytest

from app.core.cancel import CancelToken
from app.llm.base import RETRYABLE, LLMError, ToolDef, Usage


@pytest.mark.parametrize(
    "name",
    ["repo.read_file", "submit_result", "git.commit_task", "a" * 64],
)
def test_tool_name_accepted(name: str) -> None:
    assert ToolDef(name=name, description="d", input_schema={"type": "object"}).name == name


@pytest.mark.parametrize(
    "name",
    [
        "Repo.read",  # upper case
        "repo__read",  # the adapter's encoding marker (ADR-023)
        "repo.",  # empty segment
        ".repo",
        "repo read",
        "1repo",
        "",
        "a" * 65,  # over the provider's 64-character limit
        "a." + "b" * 62,  # 64 characters, but 65 once '.' becomes '__'
    ],
)
def test_tool_name_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="tool name"):
        ToolDef(name=name, description="d", input_schema={"type": "object"})


def test_retryable_kinds() -> None:
    assert frozenset({"rate_limited", "overloaded", "transient", "timeout"}) == RETRYABLE
    for kind in ("refusal", "bad_request", "auth", "malformed", "cancelled", "max_tokens"):
        assert LLMError(kind).retryable is False
    assert LLMError("overloaded").retryable is True


def test_llm_error_message_carries_only_kind_status_and_vendor_type() -> None:
    error = LLMError("bad_request", status_code=400, vendor_type="invalid_request_error", request_id="req_1")
    assert str(error) == "bad_request (HTTP 400, invalid_request_error)"
    assert str(LLMError("cancelled")) == "cancelled"
    assert error.request_id == "req_1"


def test_llm_error_keeps_partial_usage_and_retry_after() -> None:
    usage = Usage(input_tokens=10, output_tokens=3)
    error = LLMError("rate_limited", retry_after_s=2.5, usage=usage)
    assert error.usage == usage
    assert error.retry_after_s == 2.5


def test_unknown_error_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="kind"):
        LLMError("whatever")  # type: ignore[arg-type]


async def test_cancel_token_wakes_waiters() -> None:
    token = CancelToken()
    assert token.cancelled is False
    waiter = asyncio.create_task(token.wait())
    await asyncio.sleep(0)
    assert not waiter.done()
    token.cancel()
    await asyncio.wait_for(waiter, timeout=1)
    assert token.cancelled is True


def test_cancel_token_can_be_made_outside_a_loop() -> None:
    token = CancelToken()
    token.cancel()
    assert token.cancelled is True


@pytest.mark.parametrize(
    "field", ["input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"]
)
def test_usage_rejects_negative_counts(field: str) -> None:
    counts = {"input_tokens": 1, "output_tokens": 1, field: -1}
    with pytest.raises(ValueError, match="token"):
        Usage(**counts)


def test_request_repr_hides_the_prompt() -> None:
    from app.llm.base import LLMRequest, Message

    req = LLMRequest(model="m", system="SYSTEM-SECRET", messages=(Message("user", "USER-SECRET"),))
    assert "SYSTEM-SECRET" not in repr(req)
    assert "USER-SECRET" not in repr(req)
    assert "m" in repr(req)
