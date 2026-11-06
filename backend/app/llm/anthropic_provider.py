"""Claude through the official Anthropic SDK (PRD §7.3, FR-129). The only module that imports ``anthropic``.

One call is one streamed attempt: the SDK's own retries are off and ``app.llm.retry`` decides. The raw stream
is assembled here (text deltas forwarded as they arrive, tool input JSON parsed at the end) and every failure
is normalised to an ``LLMError`` that carries the request id and the usage seen so far. SDK exceptions are
never chained: their messages and request objects can hold the prompt or echo the key.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2

from app.core.cancel import CancelToken
from app.core.logging import quiet_vendor_loggers
from app.llm.base import (
    ErrorKind,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    TextDeltaHandler,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

quiet_vendor_loggers()  # the SDK reads ANTHROPIC_LOG on import and may have lowered its level to DEBUG

_EPHEMERAL = {"type": "ephemeral"}
_STOP_OK: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "stop_sequence": "stop_sequence",
}
_STOP_ERRORS: dict[str, ErrorKind] = {
    "refusal": "refusal",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
}
# Error events inside a 200 stream carry no HTTP status; their type decides. Unknown types are retried.
_STREAM_ERRORS: dict[str, ErrorKind] = {
    "overloaded_error": "overloaded",
    "api_error": "transient",
    "rate_limit_error": "rate_limited",
    "timeout_error": "timeout",
    "invalid_request_error": "bad_request",
    "not_found_error": "bad_request",
    "request_too_large": "bad_request",
    "authentication_error": "auth",
    "permission_error": "auth",
    "billing_error": "auth",
}


def encode_tool_name(name: str) -> str:
    """Canonical names keep their dots; the provider only accepts ``[a-zA-Z0-9_-]`` (ADR-023)."""
    return name.replace(".", "__")


def decode_tool_name(name: str) -> str:
    return name.replace("__", ".")


@dataclass
class _Block:
    kind: str  # "text" | "tool_use"
    parts: list[str] = field(default_factory=list)
    tool_id: str = ""
    tool_name: str = ""


@dataclass
class _Progress:
    """What the stream has delivered so far; read by the error paths for the request id and partial usage."""

    request_id: str | None = None
    usage: Usage | None = None
    stop_reason: str | None = None
    blocks: dict[int, _Block] = field(default_factory=dict)

    def error(self, kind: ErrorKind, **details: Any) -> LLMError:
        details.setdefault("request_id", self.request_id)
        return LLMError(kind, usage=self.usage, **details)


class _HandlerFailed(Exception):
    """Carries an exception raised by the caller's ``on_text_delta`` past the provider-error mapping."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(type(cause).__name__)
        self.cause = cause


async def _stop(work: asyncio.Future[Any]) -> None:
    """Cancel the streaming task and wait until it has closed the HTTP stream. Its own outcome is already
    reported by the caller, so it is retrieved here (no "exception was never retrieved") but not raised.
    A cancel of the caller during this wait propagates."""
    work.cancel()
    await asyncio.wait({work})
    if not work.cancelled():
        work.exception()


class AnthropicClaudeProvider:
    name = "anthropic"

    def __init__(
        self, api_key: str, *, base_url: str | None = None, transport: httpx2.AsyncBaseTransport | None = None
    ):
        # The SDK's default client follows redirects, and a cross-origin redirect keeps X-Api-Key and the body
        # (AG-13 M1). This client never follows one. ``transport`` is the test seam; the client is the same.
        options: dict[str, Any] = {"follow_redirects": False}
        if transport is not None:
            options["transport"] = transport
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(**options),
        )

    def __repr__(self) -> str:
        return "AnthropicClaudeProvider()"

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse:
        if cancel is not None and cancel.cancelled:
            raise LLMError("cancelled")
        started = time.monotonic()
        progress = _Progress()
        work = asyncio.ensure_future(self._attempt(req, progress, on_text_delta))
        watcher = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
        try:
            done, _ = await asyncio.wait(
                {work} if watcher is None else {work, watcher},
                timeout=req.timeout_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            # The caller itself was cancelled (shutdown, lease lost): close the stream, then let it propagate.
            await _stop(work)
            raise
        finally:
            if watcher is not None:
                watcher.cancel()
        if cancel is not None and cancel.cancelled:
            # Cancel wins even over a reply that finished at the same moment; its usage is still reported.
            await _stop(work)
            raise progress.error("cancelled")
        if work not in done:
            await _stop(work)
            raise progress.error("timeout")
        content, stop_reason = work.result()
        return LLMResponse(
            content=content,
            stop_reason=stop_reason,
            usage=progress.usage,
            model=req.model,
            request_id=progress.request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _attempt(
        self, req: LLMRequest, progress: _Progress, on_text_delta: TextDeltaHandler | None
    ) -> tuple[tuple[TextBlock | ToolUseBlock, ...], StopReason]:
        try:
            stream = await self._client.messages.create(**_params(req), stream=True, timeout=req.timeout_s)
            progress.request_id = _request_id(stream.response.headers.get("request-id"))
            async with stream:
                async for event in stream:
                    await _apply(event, progress, on_text_delta)
        except anthropic.APIStatusError as exc:
            raise _status_error(exc, progress) from None
        except (anthropic.APITimeoutError, httpx2.TimeoutException):
            raise progress.error("timeout") from None
        except (anthropic.APIConnectionError, httpx2.TransportError):
            raise progress.error("transient") from None
        except _HandlerFailed as failed:
            raise failed.cause from None  # the caller's own callback broke: not a provider failure
        except (
            ValueError,  # JSON and validation errors from the SDK's event parsing
            AttributeError,  # an event without the fields the API documents
            KeyError,
            TypeError,
            httpx2.HTTPError,  # e.g. an undecodable body
            anthropic.AnthropicError,
        ):
            raise progress.error("malformed") from None
        return _finish(progress)


def _params(req: LLMRequest) -> dict[str, Any]:
    system: dict[str, Any] = {"type": "text", "text": req.system}
    if req.cache_system:
        system["cache_control"] = _EPHEMERAL
    params: dict[str, Any] = {
        "model": req.model,
        "max_tokens": req.max_tokens,
        "system": [system],
        "messages": [_message(message) for message in req.messages],
    }
    if req.tools:
        tools = [
            {
                "name": encode_tool_name(tool.name),
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in req.tools
        ]
        if req.cache_system:
            tools[-1]["cache_control"] = _EPHEMERAL
        params["tools"] = tools
    return params


def _message(message: Message) -> dict[str, Any]:
    if isinstance(message.content, str):
        return {"role": message.role, "content": message.content}
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": encode_tool_name(block.name),
                    "input": block.input,
                }
            )
        elif isinstance(block, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {"role": message.role, "content": blocks}


async def _apply(event: Any, progress: _Progress, on_text_delta: TextDeltaHandler | None) -> None:
    kind = getattr(event, "type", None)
    if kind == "message_start":
        usage = event.message.usage
        progress.usage = Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=usage.cache_creation_input_tokens or 0,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
        )
    elif kind == "content_block_start":
        block = event.content_block
        if block.type == "text":
            progress.blocks[event.index] = _Block("text", [block.text] if block.text else [])
        elif block.type == "tool_use":
            progress.blocks[event.index] = _Block("tool_use", tool_id=block.id, tool_name=block.name)
        else:  # thinking, server tools, …: never requested by this adapter
            raise progress.error("malformed", vendor_type=f"block:{_vendor(block.type)}")
    elif kind == "content_block_delta":
        target = progress.blocks.get(event.index)
        delta = event.delta
        if target is not None and target.kind == "text" and delta.type == "text_delta":
            target.parts.append(delta.text)
            if on_text_delta is not None:
                try:
                    await on_text_delta(delta.text)
                except Exception as exc:
                    raise _HandlerFailed(exc) from exc
        elif target is not None and target.kind == "tool_use" and delta.type == "input_json_delta":
            target.parts.append(delta.partial_json)
        else:
            raise progress.error("malformed", vendor_type=f"delta:{_vendor(delta.type)}")
    elif kind == "message_delta":
        progress.stop_reason = event.delta.stop_reason
        usage = event.usage
        previous = progress.usage or Usage(input_tokens=0, output_tokens=0)
        progress.usage = Usage(  # cumulative counts: replace, never add
            input_tokens=usage.input_tokens if usage.input_tokens is not None else previous.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=(
                usage.cache_creation_input_tokens
                if usage.cache_creation_input_tokens is not None
                else previous.cache_creation_tokens
            ),
            cache_read_tokens=(
                usage.cache_read_input_tokens
                if usage.cache_read_input_tokens is not None
                else previous.cache_read_tokens
            ),
        )


def _finish(progress: _Progress) -> tuple[tuple[TextBlock | ToolUseBlock, ...], StopReason]:
    stop = progress.stop_reason
    if stop is None:
        raise progress.error("transient", vendor_type="stream_ended_early")
    if stop in _STOP_ERRORS:
        raise progress.error(_STOP_ERRORS[stop], vendor_type=stop)
    if stop not in _STOP_OK:
        raise progress.error("malformed", vendor_type=_vendor(stop))
    content: list[TextBlock | ToolUseBlock] = []
    for index in sorted(progress.blocks):
        block = progress.blocks[index]
        if block.kind == "text":
            content.append(TextBlock("".join(block.parts)))
            continue
        try:
            tool_input = json.loads("".join(block.parts) or "{}")
        except json.JSONDecodeError:
            raise progress.error("malformed", vendor_type="tool_input") from None
        if not isinstance(tool_input, dict):
            raise progress.error("malformed", vendor_type="tool_input")
        content.append(ToolUseBlock(block.tool_id, decode_tool_name(block.tool_name), tool_input))
    return tuple(content), _STOP_OK[stop]


def _status_error(exc: anthropic.APIStatusError, progress: _Progress) -> LLMError:
    body = exc.body
    error = body.get("error") if isinstance(body, dict) else None
    raw_type = error.get("type") if isinstance(error, dict) else None
    vendor_type = _vendor(raw_type) if isinstance(raw_type, str) else None
    request_id = _request_id(exc.response.headers.get("request-id")) or progress.request_id
    if exc.status_code < 300:  # an error event inside a 200 stream
        kind = _STREAM_ERRORS.get(vendor_type or "", "transient")
        return progress.error(kind, vendor_type=vendor_type, request_id=request_id)
    if exc.status_code < 400:  # never followed: see __init__
        return progress.error(
            "bad_request", status_code=exc.status_code, vendor_type="redirect", request_id=request_id
        )
    return progress.error(
        _kind_for_status(exc.status_code),
        status_code=exc.status_code,
        vendor_type=vendor_type,
        request_id=request_id,
        retry_after_s=_retry_after(exc.response.headers.get("retry-after")),
    )


_VENDOR_TEXT = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _vendor(value: str) -> str:
    """Vendor-chosen names (error types, stop reasons) end up in messages, logs and the ledger: bounded."""
    return value if _VENDOR_TEXT.fullmatch(value) else "unrecognised"


def _request_id(value: str | None) -> str | None:
    return value if value is not None and _REQUEST_ID.fullmatch(value) else None


def _kind_for_status(status: int) -> ErrorKind:
    if status == 429:
        return "rate_limited"
    if status == 529:
        return "overloaded"
    if status == 408:
        return "timeout"
    if status in (401, 403):
        return "auth"
    if status >= 500:
        return "transient"
    return "bad_request"


def _retry_after(value: str | None) -> float | None:
    """Seconds only; an HTTP date or a junk value is ignored and the normal backoff applies."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None
