from __future__ import annotations

import asyncio
import dataclasses

import httpx2
import pytest

from app.core.cancel import CancelToken
from app.llm.anthropic_provider import AnthropicClaudeProvider, decode_tool_name, encode_tool_name
from app.llm.base import (
    LLMError,
    LLMRequest,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from tests.llm_fakes import (
    ChunkStream,
    FakeAnthropic,
    error_response,
    message_end,
    message_start,
    sse,
    stream_response,
    text_block,
    tool_block,
)

READ = ToolDef(name="repo.read_file", description="Read a file", input_schema={"type": "object"})
SUBMIT = ToolDef(name="submit_result", description="End the run", input_schema={"type": "object"})
REQ = LLMRequest(
    model="claude-test-model",
    system="You are a test agent.",
    messages=(Message("user", "Read app.py"),),
    tools=(READ, SUBMIT),
    max_tokens=1024,
    metadata={"task_id": "t-1", "agent_id": "B14", "agent_run_id": "r-1"},
    timeout_s=5.0,
)
HELLO = sse(message_start(), *text_block(0, "Hel", "lo"), *message_end("end_turn"))


def provider(fake: FakeAnthropic) -> AnthropicClaudeProvider:
    return AnthropicClaudeProvider("sk-ant-api03-test", transport=fake.transport())


async def call(fake: FakeAnthropic, req: LLMRequest = REQ, **kwargs):  # type: ignore[no-untyped-def]
    return await provider(fake).create_message(req, **kwargs)


async def failure(fake: FakeAnthropic, req: LLMRequest = REQ, **kwargs) -> LLMError:  # type: ignore[no-untyped-def]
    with pytest.raises(LLMError) as raised:
        await call(fake, req, **kwargs)
    return raised.value


def test_tool_names_round_trip() -> None:
    assert encode_tool_name("repo.read_file") == "repo__read_file"
    assert decode_tool_name("repo__read_file") == "repo.read_file"
    assert decode_tool_name(encode_tool_name("git.commit_task")) == "git.commit_task"


async def test_text_and_tool_use_are_assembled() -> None:
    body = sse(
        message_start(),
        *text_block(0, "Reading ", "the file."),
        *tool_block(1, "toolu_1", "repo__read_file", '{"pa', 'th": "app', '.py"}'),
        *message_end("tool_use"),
    )
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    response = await call(FakeAnthropic(lambda _: stream_response(body)), on_text_delta=on_delta)
    assert response.content == (
        TextBlock("Reading the file."),
        ToolUseBlock("toolu_1", "repo.read_file", {"path": "app.py"}),
    )
    assert response.stop_reason == "tool_use"
    assert deltas == ["Reading ", "the file."]
    assert response.model == "claude-test-model"
    assert response.duration_ms >= 0


async def test_tool_use_without_input_deltas_has_empty_input() -> None:
    body = sse(message_start(), *tool_block(0, "toolu_1", "submit_result"), *message_end("tool_use"))
    response = await call(FakeAnthropic(lambda _: stream_response(body)))
    assert response.content == (ToolUseBlock("toolu_1", "submit_result", {}),)


async def test_usage_takes_the_cumulative_message_delta() -> None:
    body = sse(
        message_start(input_tokens=40, cache_creation=7, cache_read=1000),
        *text_block(0, "x"),
        *message_end("end_turn", output_tokens=15),
    )
    response = await call(FakeAnthropic(lambda _: stream_response(body)))
    assert response.usage == Usage(
        input_tokens=40, output_tokens=15, cache_creation_tokens=7, cache_read_tokens=1000
    )


async def test_message_delta_input_counts_replace_the_start_counts() -> None:
    end = message_end("end_turn", output_tokens=9)
    end[0]["usage"] = {"output_tokens": 9, "input_tokens": 41, "cache_read_input_tokens": 1001}
    body = sse(message_start(input_tokens=40, cache_read=1000), *text_block(0, "x"), *end)
    response = await call(FakeAnthropic(lambda _: stream_response(body)))
    assert response.usage == Usage(
        input_tokens=41, output_tokens=9, cache_creation_tokens=0, cache_read_tokens=1001
    )


async def test_request_shape() -> None:
    fake = FakeAnthropic(lambda _: stream_response(HELLO))
    history = (
        Message("user", "Read app.py"),
        Message("assistant", (TextBlock("ok"), ToolUseBlock("toolu_1", "repo.read_file", {"path": "a"}))),
        Message("user", (ToolResultBlock("toolu_1", "print(1)", is_error=False),)),
    )
    await call(fake, dataclasses.replace(REQ, messages=history))
    body = fake.body()
    assert body["stream"] is True
    assert body["model"] == "claude-test-model"
    assert body["max_tokens"] == 1024
    assert body["system"] == [
        {"type": "text", "text": "You are a test agent.", "cache_control": {"type": "ephemeral"}}
    ]
    assert [tool["name"] for tool in body["tools"]] == ["repo__read_file", "submit_result"]
    assert "cache_control" not in body["tools"][0]
    assert body["tools"][1]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": "ok"},
        {"type": "tool_use", "id": "toolu_1", "name": "repo__read_file", "input": {"path": "a"}},
    ]
    assert body["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "print(1)", "is_error": False}
    ]
    # KodeKantor's own context never leaves the process.
    assert "metadata" not in body
    assert "t-1" not in fake.requests[-1].content.decode()
    assert fake.requests[-1].headers["x-api-key"] == "sk-ant-api03-test"


async def test_request_without_cache_or_tools() -> None:
    fake = FakeAnthropic(lambda _: stream_response(HELLO))
    req = LLMRequest(model="m", system="s", messages=(Message("user", "hi"),), cache_system=False)
    await call(fake, req)
    body = fake.body()
    assert body["system"] == [{"type": "text", "text": "s"}]
    assert "tools" not in body


async def test_request_id_comes_from_the_header() -> None:
    response = await call(FakeAnthropic(lambda _: stream_response(HELLO, request_id="req_abc")))
    assert response.request_id == "req_abc"


@pytest.mark.parametrize(
    ("status", "error_type", "kind"),
    [
        (400, "invalid_request_error", "bad_request"),
        (401, "authentication_error", "auth"),
        (403, "permission_error", "auth"),
        (404, "not_found_error", "bad_request"),
        (408, "timeout_error", "timeout"),
        (409, "conflict_error", "bad_request"),
        (413, "request_too_large", "bad_request"),
        (422, "invalid_request_error", "bad_request"),
        (429, "rate_limit_error", "rate_limited"),
        (500, "api_error", "transient"),
        (502, "api_error", "transient"),
        (503, "api_error", "transient"),
        (504, "timeout_error", "transient"),
        (529, "overloaded_error", "overloaded"),
    ],
)
async def test_http_error_mapping(status: int, error_type: str, kind: str) -> None:
    error = await failure(FakeAnthropic(lambda _: error_response(status, error_type)))
    assert error.kind == kind
    assert error.status_code == status
    assert error.vendor_type == error_type
    assert error.request_id == "req_err"
    assert error.usage is None


async def test_retry_after_seconds_are_read() -> None:
    fake = FakeAnthropic(lambda _: error_response(429, "rate_limit_error", headers={"retry-after": "3"}))
    error = await failure(fake)
    assert error.retry_after_s == 3.0


@pytest.mark.parametrize("value", ["Wed, 21 Oct 2015 07:28:00 GMT", "abc", "-5", "nan", "inf"])
async def test_unusable_retry_after_is_ignored(value: str) -> None:
    fake = FakeAnthropic(lambda _: error_response(429, "rate_limit_error", headers={"retry-after": value}))
    error = await failure(fake)
    assert error.kind == "rate_limited"
    assert error.retry_after_s is None


async def test_one_http_request_per_attempt() -> None:
    fake = FakeAnthropic(lambda _: error_response(500, "api_error"))
    await failure(fake)
    assert len(fake.requests) == 1  # the SDK's own retries are off; app.llm.retry decides


@pytest.mark.parametrize(
    ("error_type", "kind"),
    [
        ("overloaded_error", "overloaded"),
        ("api_error", "transient"),
        ("rate_limit_error", "rate_limited"),
        ("invalid_request_error", "bad_request"),
        ("something_new", "transient"),
    ],
)
async def test_stream_error_event_after_text(error_type: str, kind: str) -> None:
    body = sse(
        message_start(input_tokens=30),
        *text_block(0, "partial answer"),
        {"type": "error", "error": {"type": error_type, "message": "mid-stream"}},
    )
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    error = await failure(FakeAnthropic(lambda _: stream_response(body)), on_text_delta=on_delta)
    assert error.kind == kind
    assert error.vendor_type == error_type
    assert error.status_code is None  # the HTTP status was 200; the error came inside the stream
    assert error.usage == Usage(input_tokens=30, output_tokens=1)
    assert error.request_id == "req_test"
    assert deltas == ["partial answer"]


@pytest.mark.parametrize(
    ("stop_reason", "kind"),
    [
        ("refusal", "refusal"),
        ("max_tokens", "max_tokens"),
        ("model_context_window_exceeded", "max_tokens"),
        ("pause_turn", "malformed"),
        ("something_new", "malformed"),
    ],
)
async def test_stop_reasons_that_are_errors(stop_reason: str, kind: str) -> None:
    body = sse(message_start(), *text_block(0, "x"), *message_end(stop_reason, output_tokens=50))
    error = await failure(FakeAnthropic(lambda _: stream_response(body)))
    assert error.kind == kind
    assert error.vendor_type == stop_reason
    assert error.usage == Usage(input_tokens=25, output_tokens=50)


async def test_stop_sequence_is_a_normal_reply() -> None:
    body = sse(message_start(), *text_block(0, "x"), *message_end("stop_sequence"))
    response = await call(FakeAnthropic(lambda _: stream_response(body)))
    assert response.stop_reason == "stop_sequence"


@pytest.mark.parametrize("json_parts", [('{"path": ',), ('["a"]',), ("null",)])
async def test_invalid_tool_input_is_malformed(json_parts: tuple[str, ...]) -> None:
    body = sse(
        message_start(), *tool_block(0, "toolu_1", "repo__read_file", *json_parts), *message_end("tool_use")
    )
    error = await failure(FakeAnthropic(lambda _: stream_response(body)))
    assert error.kind == "malformed"
    assert error.usage is not None


async def test_unrequested_block_type_is_malformed() -> None:
    thinking = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {"type": "content_block_stop", "index": 0},
    ]
    body = sse(message_start(), *thinking, *message_end("end_turn"))
    error = await failure(FakeAnthropic(lambda _: stream_response(body)))
    assert error.kind == "malformed"


async def test_stream_that_ends_without_a_stop_reason_is_transient() -> None:
    body = sse(message_start(), *text_block(0, "cut"))
    error = await failure(FakeAnthropic(lambda _: stream_response(body)))
    assert error.kind == "transient"
    assert error.usage == Usage(input_tokens=25, output_tokens=1)


async def test_connection_drop_mid_stream_is_transient() -> None:
    chunks = sse(message_start(), *text_block(0, "half"))

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", "request-id": "req_drop"},
            stream=ChunkStream(chunks, fail=httpx2.ReadError("connection reset")),
        )

    error = await failure(FakeAnthropic(handler))
    assert error.kind == "transient"
    assert error.request_id == "req_drop"
    assert error.usage == Usage(input_tokens=25, output_tokens=1)


async def test_connection_refused_is_transient() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    error = await failure(FakeAnthropic(handler))
    assert error.kind == "transient"
    assert error.usage is None


async def test_attempt_timeout() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ChunkStream(sse(message_start()), hold=asyncio.Event()),
        )

    req = LLMRequest(model="m", system="s", messages=(Message("user", "hi"),), timeout_s=0.2)
    error = await failure(FakeAnthropic(handler), req)
    assert error.kind == "timeout"
    assert error.usage == Usage(input_tokens=25, output_tokens=1)


async def test_cancel_mid_stream_closes_the_stream() -> None:
    stream = ChunkStream(sse(message_start(), *text_block(0, "first")), hold=asyncio.Event())

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream", "request-id": "req_c"}, stream=stream
        )

    token = CancelToken()

    async def on_delta(_: str) -> None:
        token.cancel()

    error = await asyncio.wait_for(failure(FakeAnthropic(handler), on_text_delta=on_delta, cancel=token), 5)
    assert error.kind == "cancelled"
    assert error.request_id == "req_c"
    assert error.usage == Usage(input_tokens=25, output_tokens=1)
    assert stream.closed


async def test_cancelled_before_start_sends_nothing() -> None:
    fake = FakeAnthropic(lambda _: stream_response(HELLO))
    token = CancelToken()
    token.cancel()
    error = await failure(fake, cancel=token)
    assert error.kind == "cancelled"
    assert error.usage is None
    assert fake.requests == []


async def test_unknown_tool_name_passes_through_decoded() -> None:
    body = sse(
        message_start(), *tool_block(0, "toolu_1", "shell__exec", '{"cmd": "ls"}'), *message_end("tool_use")
    )
    response = await call(FakeAnthropic(lambda _: stream_response(body)))
    # The adapter neither guesses nor drops: ToolGateway's allowlist refuses it (WP-3.1d).
    assert response.content == (ToolUseBlock("toolu_1", "shell.exec", {"cmd": "ls"}),)


async def test_ping_events_are_ignored() -> None:
    body = sse(
        message_start(), {"type": "ping"}, *text_block(0, "x"), {"type": "ping"}, *message_end("end_turn")
    )
    response = await call(FakeAnthropic(lambda _: stream_response(body)))
    assert response.content == (TextBlock("x"),)


def test_provider_repr_hides_the_key() -> None:
    adapter = AnthropicClaudeProvider("sk-ant-api03-secret-value")
    assert "secret-value" not in repr(adapter)
    assert adapter.name == "anthropic"


def held(stream: ChunkStream, request_id: str = "req_h") -> FakeAnthropic:
    return FakeAnthropic(
        lambda _: httpx2.Response(
            200, headers={"content-type": "text/event-stream", "request-id": request_id}, stream=stream
        )
    )


def live_tasks() -> set[asyncio.Task[object]]:
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()}


async def test_outer_cancel_closes_the_stream_and_propagates() -> None:
    stream = ChunkStream(sse(message_start(), *text_block(0, "first")), hold=asyncio.Event())
    started = asyncio.Event()

    async def on_delta(_: str) -> None:
        started.set()

    before = live_tasks()
    caller = asyncio.create_task(call(held(stream), on_text_delta=on_delta))
    await asyncio.wait_for(started.wait(), 5)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert stream.closed
    await asyncio.sleep(0)
    assert live_tasks() - before == set()  # nothing keeps streaming after the caller is gone


async def test_outer_cancel_during_teardown_is_not_swallowed() -> None:
    gate = asyncio.Event()
    stream = ChunkStream(sse(message_start(), *text_block(0, "first")), hold=asyncio.Event(), close_gate=gate)
    token = CancelToken()

    async def on_delta(_: str) -> None:
        token.cancel()  # the adapter now stops the stream; closing it waits on the gate

    caller = asyncio.create_task(call(held(stream), on_text_delta=on_delta, cancel=token))
    await asyncio.wait_for(stream.closing.wait(), 5)
    caller.cancel()  # e.g. worker shutdown while the stream is being closed
    with pytest.raises(asyncio.CancelledError):
        await caller  # the caller's own cancel wins over LLMError("cancelled")
    gate.set()
    await asyncio.sleep(0)


async def test_cancel_set_on_the_last_delta_wins_over_the_finished_reply() -> None:
    body = sse(message_start(), *text_block(0, "only"), *message_end("end_turn", output_tokens=15))
    token = CancelToken()

    async def on_delta(_: str) -> None:
        token.cancel()

    error = await failure(
        FakeAnthropic(lambda _: stream_response(body)), on_text_delta=on_delta, cancel=token
    )
    assert error.kind == "cancelled"
    assert error.usage == Usage(input_tokens=25, output_tokens=15)  # what was billed, kept for the ledger


async def test_unexpected_event_shape_is_malformed() -> None:
    start = message_start()
    del start["message"]["usage"]
    body = sse(start, *text_block(0, "x"), *message_end("end_turn"))
    error = await failure(FakeAnthropic(lambda _: stream_response(body)))
    assert error.kind == "malformed"


async def test_undecodable_body_is_normalised() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", "content-encoding": "gzip"},
            content=b"not gzip",
        )

    error = await failure(FakeAnthropic(handler))
    # The SDK reports a body it cannot decode as a connection error: retried, within the retry bound.
    assert error.kind == "transient"


async def test_text_handler_failure_propagates_unchanged() -> None:
    stream = ChunkStream(sse(message_start(), *text_block(0, "first")), hold=asyncio.Event())

    async def on_delta(_: str) -> None:
        raise RuntimeError("event bus down")

    with pytest.raises(RuntimeError, match="event bus down"):
        await asyncio.wait_for(call(held(stream), on_text_delta=on_delta), 5)
    assert stream.closed


async def test_a_redirect_is_not_followed() -> None:
    # Following it would send X-Api-Key and the prompt to whatever host the Location names (AG-13 M1).
    fake = FakeAnthropic(
        lambda _: httpx2.Response(307, headers={"location": "https://collector.example/v1/messages"})
    )
    error = await failure(fake)
    assert len(fake.requests) == 1
    assert error.kind == "bad_request"
    assert error.status_code == 307
    assert error.vendor_type == "redirect"


@pytest.mark.parametrize("bad", ["x" * 65, "bad type\nInjected: 1", "<script>", ""])
async def test_vendor_error_type_is_bounded(bad: str) -> None:
    error = await failure(FakeAnthropic(lambda _: error_response(400, bad)))
    assert error.vendor_type == "unrecognised"
    assert bad not in str(error) or bad == ""


async def test_vendor_stop_reason_is_bounded() -> None:
    body = sse(message_start(), *text_block(0, "x"), *message_end("y" * 200))
    error = await failure(FakeAnthropic(lambda _: stream_response(body)))
    assert error.kind == "malformed"
    assert error.vendor_type == "unrecognised"


@pytest.mark.parametrize("request_id", ["r" * 129, "req 1", "req;x=1", "../etc"])
async def test_odd_request_ids_are_dropped(request_id: str) -> None:
    response = await call(FakeAnthropic(lambda _: stream_response(HELLO, request_id=request_id)))
    assert response.request_id is None


async def test_a_long_plain_request_id_is_kept() -> None:
    response = await call(FakeAnthropic(lambda _: stream_response(HELLO, request_id="req_" + "a" * 124)))
    assert response.request_id == "req_" + "a" * 124
