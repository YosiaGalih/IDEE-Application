"""Fake Anthropic Messages API for adapter tests: SSE bodies served through ``httpx2.MockTransport``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx2

Handler = Callable[[httpx2.Request], httpx2.Response | Awaitable[httpx2.Response]]


def sse(*events: dict[str, Any]) -> bytes:
    return b"".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events)


def message_start(input_tokens: int = 25, cache_creation: int = 0, cache_read: int = 0) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-test-model",
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 1,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def text_block(index: int, *chunks: str) -> list[dict[str, Any]]:
    return [
        {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
        *(
            {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": chunk}}
            for chunk in chunks
        ),
        {"type": "content_block_stop", "index": index},
    ]


def tool_block(index: int, tool_id: str, name: str, *json_parts: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}},
        },
        *(
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": p},
            }
            for p in json_parts
        ),
        {"type": "content_block_stop", "index": index},
    ]


def message_end(stop_reason: str, output_tokens: int = 15) -> list[dict[str, Any]]:
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
        {"type": "message_stop"},
    ]


def stream_response(body: bytes, *, request_id: str = "req_test") -> httpx2.Response:
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream", "request-id": request_id}, content=body
    )


def error_response(
    status: int, error_type: str, *, headers: dict[str, str] | None = None, message: str = "x"
) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers={"content-type": "application/json", "request-id": "req_err", **(headers or {})},
        json={"type": "error", "error": {"type": error_type, "message": message}},
    )


class ChunkStream(httpx2.AsyncByteStream):
    """Yields ``chunks``, then either raises ``fail`` or waits on ``hold`` (never set: a stalled stream).

    ``close_gate`` makes closing wait until it is set, to hold a teardown in progress.
    """

    def __init__(
        self,
        *chunks: bytes,
        fail: Exception | None = None,
        hold: asyncio.Event | None = None,
        close_gate: asyncio.Event | None = None,
    ) -> None:
        self.chunks = chunks
        self.fail = fail
        self.hold = hold
        self.close_gate = close_gate
        self.closing = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        if self.fail is not None:
            raise self.fail
        if self.hold is not None:
            await self.hold.wait()

    async def aclose(self) -> None:
        self.closing.set()
        if self.close_gate is not None:
            await self.close_gate.wait()
        self.closed = True


class FakeAnthropic:
    """Records requests; answers each with the next handler (the last one repeats)."""

    def __init__(self, *handlers: Handler) -> None:
        self.handlers = handlers
        self.requests: list[httpx2.Request] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        handler = self.handlers[min(len(self.requests) - 1, len(self.handlers) - 1)]
        response = handler(request)
        if not isinstance(response, httpx2.Response):
            response = await response
        return response

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)
