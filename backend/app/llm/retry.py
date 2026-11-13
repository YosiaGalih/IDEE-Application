"""Bounded retry around any ``LLMProvider`` (ARCHITECTURE §6.1, PRD §7.3.3, FR-056).

Every attempt that reaches the provider is reported through ``on_attempt``, including the failed and the
cancelled ones, so the usage ledger can record each request with its own id, tokens and duration (FR-133). A
failure of ``on_attempt`` itself propagates: losing a ledger entry is not silent. Only transient kinds are
retried; ``retry-after`` is honoured up to a ceiling, past which the error is passed on instead of holding a
worker (and its task lease) asleep.

``before_attempt(attempt)`` runs before every attempt, the first and each retry, ahead of any restart
notice (ARCHITECTURE §5.4, "before every request"; WP-3.1f1). An exception from it stops the call as it is:
no request is sent and nothing is reported through ``on_attempt``, since that attempt never reached the
provider. The previous attempt's ``on_attempt`` has already completed, so a budget check sees it.

Text already streamed to ``on_text_delta`` cannot be taken back. After such text, an attempt is retried only
when ``on_restart(attempt)`` is given, so the consumer can discard the partial text first; without it the
error is passed on rather than streaming the answer twice.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from app.core.cancel import CancelToken
from app.core.logging import get_logger
from app.llm.base import ErrorKind, LLMError, LLMProvider, LLMRequest, LLMResponse, TextDeltaHandler, Usage


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 4
    base_s: float = 1.0
    factor: float = 2.0
    cap_s: float = 30.0
    max_retry_after_s: float = 60.0


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    provider: str
    model: str
    attempt: int  # 1-based
    request_id: str | None
    usage: Usage | None  # partial when the attempt failed after the provider started answering
    duration_ms: int
    status: Literal["ok", "error", "cancelled"]
    error_kind: ErrorKind | None  # None for an unexpected (non-LLMError) failure


_log = get_logger(__name__)

AttemptHandler = Callable[[AttemptRecord], Awaitable[None]]
RestartHandler = Callable[[int], Awaitable[None]]
BeforeAttemptHandler = Callable[[int], Awaitable[None]]


async def create_with_retry(
    provider: LLMProvider,
    req: LLMRequest,
    *,
    policy: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen, so a shared default is safe
    on_text_delta: TextDeltaHandler | None = None,
    on_restart: RestartHandler | None = None,
    cancel: CancelToken | None = None,
    on_attempt: AttemptHandler | None = None,
    before_attempt: BeforeAttemptHandler | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
) -> LLMResponse:
    attempt = 0
    streamed = False  # text of a failed attempt already reached the consumer
    while True:
        if cancel is not None and cancel.cancelled:
            raise LLMError("cancelled")
        attempt += 1
        if before_attempt is not None:
            await before_attempt(attempt)  # raises to refuse: nothing sent, nothing to record
        if streamed and on_restart is not None:
            await on_restart(attempt)
        streamed = False

        async def forward(text: str) -> None:
            nonlocal streamed
            streamed = True
            assert on_text_delta is not None
            await on_text_delta(text)

        started = time.monotonic()
        try:
            response = await provider.create_message(
                req, on_text_delta=forward if on_text_delta is not None else None, cancel=cancel
            )
        except LLMError as error:
            await _report(on_attempt, provider, req, attempt, started, error=error)
            delay = _delay(policy, attempt, error, rng)
            if delay is None or (streamed and on_restart is None):
                raise
            await _pause(delay, sleep, cancel)
            continue
        except asyncio.CancelledError as cancelled:
            # The caller was cancelled mid-attempt: the request still reached the provider. Its usage is not
            # known here, so the record says so (None, priced "unavailable"), never zero.
            try:
                await _report(on_attempt, provider, req, attempt, started, cancelled=True)
            except Exception as unrecorded:
                # The cancel still wins (review L7), but a lost ledger row is never silent: ids only, no text.
                _log.error(
                    "usage_attempt_unrecorded",
                    call_id=req.metadata.get("call_id"),
                    attempt=attempt,
                    error_type=type(unrecorded).__name__,
                )
                raise cancelled from unrecorded
            raise
        except Exception:
            await _report(on_attempt, provider, req, attempt, started, unexpected=True)
            raise
        await _report(on_attempt, provider, req, attempt, started, response=response)
        return response


def _delay(policy: RetryPolicy, attempt: int, error: LLMError, rng: Callable[[], float]) -> float | None:
    """Seconds to wait before the next attempt, or None when the error must be passed on."""
    if not error.retryable or attempt > policy.max_retries:
        return None
    if error.retry_after_s is not None and error.retry_after_s > policy.max_retry_after_s:
        return None
    backoff = rng() * min(policy.cap_s, policy.base_s * policy.factor ** (attempt - 1))
    return max(backoff, error.retry_after_s) if error.retry_after_s is not None else backoff


async def _pause(delay: float, sleep: Callable[[float], Awaitable[None]], cancel: CancelToken | None) -> None:
    if cancel is None:
        await sleep(delay)
        return
    sleeper = asyncio.ensure_future(sleep(delay))
    waiter = asyncio.ensure_future(cancel.wait())
    try:
        await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for pending in (sleeper, waiter):
            pending.cancel()
        await asyncio.wait({sleeper, waiter})
    if cancel.cancelled:
        raise LLMError("cancelled")
    failure = None if sleeper.cancelled() else sleeper.exception()
    if failure is not None:
        raise failure  # a broken sleep is not a pause


async def _report(
    on_attempt: AttemptHandler | None,
    provider: LLMProvider,
    req: LLMRequest,
    attempt: int,
    started: float,
    *,
    response: LLMResponse | None = None,
    error: LLMError | None = None,
    cancelled: bool = False,
    unexpected: bool = False,
) -> None:
    if on_attempt is None:
        return
    duration_ms = int((time.monotonic() - started) * 1000)
    if response is not None:
        record = AttemptRecord(
            provider.name, req.model, attempt, response.request_id, response.usage, duration_ms, "ok", None
        )
    elif error is not None:
        status: Literal["error", "cancelled"] = "cancelled" if error.kind == "cancelled" else "error"
        record = AttemptRecord(
            provider.name, req.model, attempt, error.request_id, error.usage, duration_ms, status, error.kind
        )
    elif cancelled:
        record = AttemptRecord(
            provider.name, req.model, attempt, None, None, duration_ms, "cancelled", "cancelled"
        )
    else:
        assert unexpected
        record = AttemptRecord(provider.name, req.model, attempt, None, None, duration_ms, "error", None)
    await on_attempt(record)
