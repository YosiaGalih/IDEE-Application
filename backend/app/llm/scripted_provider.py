"""Deterministic provider for tests and demos without an API key (ADR-015). Refused in production by config.

The scenario comes from ``req.metadata["scenario"]`` (default ``generic``); the script within it from
``req.metadata["agent_id"]``. Every request is kept in ``requests`` so leak tests can inspect what would have
been sent to a provider (SECURITY_MODEL T5) when ``record_requests`` is on.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

from app.core.cancel import CancelToken
from app.llm.base import LLMError, LLMRequest, LLMResponse, TextBlock, TextDeltaHandler, Usage
from app.llm.scenarios import SCENARIOS
from app.llm.scenarios.model import Fail, Reply, Scenario


class ScriptedProvider:
    name = "scripted"

    def __init__(
        self,
        scenarios: Mapping[str, Scenario] = SCENARIOS,
        *,
        default_scenario: str = "generic",
        delta_size: int = 16,
        record_requests: bool = False,
    ) -> None:
        self._scenarios = scenarios
        self._default_scenario = default_scenario
        self._delta_size = delta_size
        self._record_requests = record_requests  # tests only: a demo worker must not keep every prompt
        self.requests: list[LLMRequest] = []

    async def aclose(self) -> None:
        """Nothing to release; the same shape as the adapter's (one provider per run, Keputusan Lead #28).
        ``requests`` stay readable for tests."""

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse:
        started = time.monotonic()
        if self._record_requests:
            self.requests.append(req)
        if cancel is not None and cancel.cancelled:
            raise LLMError("cancelled")
        name = req.metadata.get("scenario", self._default_scenario)
        scenario = self._scenarios.get(name)
        if scenario is None:
            raise LLMError("bad_request", vendor_type="unknown_scenario")
        script = scenario.scripts.get(req.metadata.get("agent_id", ""), scenario.default)
        turn_index = sum(1 for message in req.messages if message.role == "assistant")
        turn = script[min(turn_index, len(script) - 1)]
        outcome = turn if isinstance(turn, Reply | Fail) else turn(req)
        request_id = f"scripted-{name}-{turn_index}"
        if isinstance(outcome, Fail):
            raise LLMError(
                outcome.kind, retry_after_s=outcome.retry_after_s, request_id=request_id, usage=outcome.usage
            )
        await self._stream(outcome, request_id, on_text_delta, cancel)
        return LLMResponse(
            content=outcome.content,
            stop_reason=outcome.stop_reason,
            usage=outcome.usage,
            model=req.model,
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _stream(
        self,
        reply: Reply,
        request_id: str,
        on_text_delta: TextDeltaHandler | None,
        cancel: CancelToken | None,
    ) -> None:
        # Like a real stream cut short: the prompt was read (input and cache tokens count), no output billed.
        cut = Usage(
            input_tokens=reply.usage.input_tokens,
            output_tokens=0,
            cache_creation_tokens=reply.usage.cache_creation_tokens,
            cache_read_tokens=reply.usage.cache_read_tokens,
        )
        for block in reply.content:
            if not isinstance(block, TextBlock):
                continue
            for start in range(0, len(block.text), self._delta_size):
                if cancel is not None and cancel.cancelled:
                    raise LLMError("cancelled", request_id=request_id, usage=cut)
                if on_text_delta is not None:
                    await on_text_delta(block.text[start : start + self._delta_size])
                await asyncio.sleep(0)
        if cancel is not None and cancel.cancelled:
            raise LLMError("cancelled", request_id=request_id, usage=cut)
