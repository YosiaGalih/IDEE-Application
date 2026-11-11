from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from app.core.cancel import CancelToken
from app.llm.base import (
    ErrorKind,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    TextBlock,
    TextDeltaHandler,
    Usage,
)
from app.llm.retry import AttemptRecord, RetryPolicy, create_with_retry

REQ = LLMRequest(model="claude-test-model", system="s", messages=(Message("user", "hi"),))


def ok(request_id: str = "req_ok") -> LLMResponse:
    return LLMResponse(
        content=(TextBlock("done"),),
        stop_reason="end_turn",
        usage=Usage(input_tokens=5, output_tokens=2),
        model="claude-test-model",
        request_id=request_id,
        duration_ms=1,
    )


class FakeProvider:
    """Plays back one outcome per call: a response, or an LLMError to raise."""

    name = "fake"

    def __init__(self, outcomes: Sequence[LLMResponse | LLMError]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, LLMError):
            raise outcome
        return outcome


class Recorder:
    def __init__(self) -> None:
        self.records: list[AttemptRecord] = []
        self.sleeps: list[float] = []

    async def on_attempt(self, record: AttemptRecord) -> None:
        self.records.append(record)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


async def run(provider: FakeProvider, recorder: Recorder, **kwargs: object) -> LLMResponse:
    return await create_with_retry(
        provider,
        REQ,
        on_attempt=recorder.on_attempt,
        sleep=recorder.sleep,
        rng=lambda: 0.5,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_success_first_try_one_record() -> None:
    provider, recorder = FakeProvider([ok()]), Recorder()
    response = await run(provider, recorder)
    assert response.request_id == "req_ok"
    assert [(r.attempt, r.status, r.error_kind, r.request_id) for r in recorder.records] == [
        (1, "ok", None, "req_ok")
    ]
    assert recorder.records[0].usage == Usage(input_tokens=5, output_tokens=2)
    assert recorder.records[0].provider == "fake"
    assert recorder.records[0].model == "claude-test-model"
    assert recorder.sleeps == []


async def test_retries_retryable_then_succeeds() -> None:
    provider = FakeProvider([LLMError("overloaded", request_id="r1"), LLMError("transient"), ok()])
    recorder = Recorder()
    await run(provider, recorder)
    assert [(r.attempt, r.status, r.error_kind) for r in recorder.records] == [
        (1, "error", "overloaded"),
        (2, "error", "transient"),
        (3, "ok", None),
    ]
    assert recorder.records[0].request_id == "r1"
    assert recorder.sleeps == [0.5, 1.0]  # rng 0.5 x (1, 2)


async def test_backoff_is_capped() -> None:
    provider = FakeProvider([LLMError("timeout")] * 4 + [ok()])
    recorder = Recorder()
    await run(provider, recorder, policy=RetryPolicy(base_s=10.0, cap_s=30.0))
    assert recorder.sleeps == [5.0, 10.0, 15.0, 15.0]  # 0.5 x min(30, 10, 20, 40, 80)


async def test_gives_up_after_four_retries() -> None:
    provider = FakeProvider([LLMError("rate_limited")] * 6)
    recorder = Recorder()
    with pytest.raises(LLMError) as raised:
        await run(provider, recorder)
    assert raised.value.kind == "rate_limited"
    assert provider.calls == 5
    assert len(recorder.records) == 5
    assert len(recorder.sleeps) == 4


@pytest.mark.parametrize("kind", ["refusal", "bad_request", "auth", "malformed", "max_tokens"])
async def test_non_retryable_not_retried(kind: ErrorKind) -> None:
    provider, recorder = FakeProvider([LLMError(kind), ok()]), Recorder()
    with pytest.raises(LLMError) as raised:
        await run(provider, recorder)
    assert raised.value.kind == kind
    assert provider.calls == 1
    assert [r.status for r in recorder.records] == ["error"]
    assert recorder.sleeps == []


async def test_retry_after_is_honoured() -> None:
    provider = FakeProvider([LLMError("rate_limited", retry_after_s=7.0), ok()])
    recorder = Recorder()
    await run(provider, recorder)
    assert recorder.sleeps == [7.0]


async def test_backoff_wins_over_a_shorter_retry_after() -> None:
    provider = FakeProvider([LLMError("rate_limited", retry_after_s=0.1), ok()])
    recorder = Recorder()
    await run(provider, recorder)
    assert recorder.sleeps == [0.5]


async def test_long_retry_after_is_not_waited() -> None:
    provider = FakeProvider([LLMError("rate_limited", retry_after_s=61.0), ok()])
    recorder = Recorder()
    with pytest.raises(LLMError) as raised:
        await run(provider, recorder)
    assert raised.value.retry_after_s == 61.0
    assert provider.calls == 1
    assert recorder.sleeps == []


async def test_cancel_during_backoff_stops_retrying() -> None:
    provider = FakeProvider([LLMError("overloaded"), ok()])
    token = CancelToken()
    records: list[AttemptRecord] = []

    async def on_attempt(record: AttemptRecord) -> None:
        records.append(record)

    async def cancel_soon() -> None:
        await asyncio.sleep(0.01)
        token.cancel()

    canceller = asyncio.create_task(cancel_soon())
    with pytest.raises(LLMError) as raised:
        await create_with_retry(
            provider,
            REQ,
            cancel=token,
            on_attempt=on_attempt,
            policy=RetryPolicy(base_s=30.0),
            rng=lambda: 1.0,
        )
    await canceller
    assert raised.value.kind == "cancelled"
    assert provider.calls == 1
    assert [(r.status, r.error_kind) for r in records] == [("error", "overloaded")]


async def test_already_cancelled_makes_no_attempt() -> None:
    provider, recorder = FakeProvider([ok()]), Recorder()
    token = CancelToken()
    token.cancel()
    with pytest.raises(LLMError) as raised:
        await run(provider, recorder, cancel=token)
    assert raised.value.kind == "cancelled"
    assert provider.calls == 0
    assert recorder.records == []


async def test_failed_attempt_reports_partial_usage() -> None:
    partial = Usage(input_tokens=40, output_tokens=7)
    provider = FakeProvider([LLMError("transient", usage=partial, request_id="r1"), ok()])
    recorder = Recorder()
    await run(provider, recorder)
    assert recorder.records[0].usage == partial


async def test_cancelled_attempt_record() -> None:
    provider = FakeProvider([LLMError("cancelled", usage=Usage(input_tokens=9, output_tokens=1))])
    recorder = Recorder()
    with pytest.raises(LLMError):
        await run(provider, recorder)
    assert [(r.status, r.error_kind) for r in recorder.records] == [("cancelled", "cancelled")]
    assert recorder.records[0].usage == Usage(input_tokens=9, output_tokens=1)


async def test_unexpected_exception_is_recorded_and_propagates() -> None:
    class Broken(FakeProvider):
        async def create_message(self, req, *, on_text_delta=None, cancel=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("adapter bug")

    recorder = Recorder()
    with pytest.raises(RuntimeError):
        await run(Broken([]), recorder)
    # The attempt still reached the provider, so the ledger must see it (as an error without a kind).
    assert [(r.status, r.error_kind) for r in recorder.records] == [("error", None)]


async def test_duration_is_measured_per_attempt() -> None:
    provider, recorder = FakeProvider([LLMError("overloaded"), ok()]), Recorder()
    await run(provider, recorder)
    assert all(r.duration_ms >= 0 for r in recorder.records)


class StreamingProvider(FakeProvider):
    """Sends ``deltas[i]`` through ``on_text_delta`` before playing back outcome i."""

    def __init__(self, outcomes: Sequence[LLMResponse | LLMError], deltas: Sequence[Sequence[str]]) -> None:
        super().__init__(outcomes)
        self.deltas = deltas

    async def create_message(self, req, *, on_text_delta=None, cancel=None):  # type: ignore[no-untyped-def]
        for text in self.deltas[self.calls]:
            if on_text_delta is not None:
                await on_text_delta(text)
        return await super().create_message(req, on_text_delta=on_text_delta, cancel=cancel)


async def test_outer_cancel_is_recorded_and_propagates() -> None:
    entered = asyncio.Event()

    class Hanging(FakeProvider):
        async def create_message(self, req, *, on_text_delta=None, cancel=None):  # type: ignore[no-untyped-def]
            self.calls += 1
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    records: list[AttemptRecord] = []

    async def on_attempt(record: AttemptRecord) -> None:
        records.append(record)

    caller = asyncio.create_task(create_with_retry(Hanging([]), REQ, on_attempt=on_attempt))
    await asyncio.wait_for(entered.wait(), 5)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    # The request reached the provider, so the ledger sees it; its cost is unknown, never zero.
    assert [(r.status, r.error_kind, r.usage) for r in records] == [("cancelled", "cancelled", None)]


async def test_text_sent_then_failure_is_not_retried_without_a_restart_handler() -> None:
    provider = StreamingProvider([LLMError("overloaded"), ok()], deltas=[["Hel"], ["Hello"]])
    seen: list[str] = []

    async def on_delta(text: str) -> None:
        seen.append(text)

    recorder = Recorder()
    with pytest.raises(LLMError) as raised:
        await run(provider, recorder, on_text_delta=on_delta)
    assert raised.value.kind == "overloaded"
    assert provider.calls == 1
    assert seen == ["Hel"]  # never "HelHello"


async def test_text_sent_then_failure_restarts_through_the_handler() -> None:
    provider = StreamingProvider([LLMError("overloaded"), ok()], deltas=[["Hel"], ["Hello"]])
    events: list[str] = []

    async def on_delta(text: str) -> None:
        events.append(text)

    async def on_restart(attempt: int) -> None:
        events.append(f"<restart {attempt}>")

    await run(provider, Recorder(), on_text_delta=on_delta, on_restart=on_restart)
    assert events == ["Hel", "<restart 2>", "Hello"]


async def test_failure_before_any_text_retries_without_a_restart() -> None:
    provider = StreamingProvider([LLMError("overloaded"), ok()], deltas=[[], ["Hello"]])
    restarts: list[int] = []

    async def on_restart(attempt: int) -> None:
        restarts.append(attempt)

    async def on_delta(_: str) -> None:
        return None

    await run(provider, Recorder(), on_text_delta=on_delta, on_restart=on_restart)
    assert provider.calls == 2
    assert restarts == []


@pytest.mark.parametrize("with_cancel", [False, True])
async def test_a_failing_sleep_is_not_mistaken_for_a_pause(with_cancel: bool) -> None:
    provider = FakeProvider([LLMError("overloaded"), ok()])

    async def broken_sleep(_: float) -> None:
        raise RuntimeError("clock broke")

    with pytest.raises(RuntimeError, match="clock broke"):
        await create_with_retry(
            provider, REQ, sleep=broken_sleep, rng=lambda: 0.5, cancel=CancelToken() if with_cancel else None
        )
    assert provider.calls == 1


# --- before_attempt (WP-3.1f1 Task 3; ARCHITECTURE §5.4 "before every request", spec WP-3.1f Goal 3) --------


class Refused(Exception):
    """What a budget check raises; any exception from the hook must stop the call as it is."""


class EventLog:
    """One timeline for the hook, the provider, the ledger and the backoff sleeps."""

    def __init__(self, refuse_at: int | None = None) -> None:
        self.events: list[str] = []
        self.records: list[AttemptRecord] = []
        self.sleeps: list[float] = []
        self.refuse_at = refuse_at

    async def before_attempt(self, attempt: int) -> None:
        self.events.append(f"hook{attempt}")
        if attempt == self.refuse_at:
            raise Refused(attempt)

    async def on_attempt(self, record: AttemptRecord) -> None:
        self.events.append(f"record{record.attempt}")
        self.records.append(record)

    async def sleep(self, seconds: float) -> None:
        self.events.append("sleep")
        self.sleeps.append(seconds)


class LoggedProvider(FakeProvider):
    def __init__(self, outcomes: Sequence[LLMResponse | LLMError], log: EventLog) -> None:
        super().__init__(outcomes)
        self.log = log

    async def create_message(self, req, *, on_text_delta=None, cancel=None):  # type: ignore[no-untyped-def]
        self.log.events.append(f"call{self.calls + 1}")
        return await super().create_message(req, on_text_delta=on_text_delta, cancel=cancel)


async def run_logged(provider: FakeProvider, log: EventLog, **kwargs: object) -> LLMResponse:
    return await create_with_retry(
        provider,
        REQ,
        on_attempt=log.on_attempt,
        before_attempt=log.before_attempt,
        sleep=log.sleep,
        rng=lambda: 0.5,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_before_attempt_runs_before_every_attempt() -> None:
    log = EventLog()
    provider = LoggedProvider([LLMError("overloaded"), LLMError("transient"), ok()], log)
    await run_logged(provider, log)
    assert log.events == [
        "hook1",
        "call1",
        "record1",
        "sleep",
        "hook2",
        "call2",
        "record2",
        "sleep",
        "hook3",
        "call3",
        "record3",
    ]


async def test_before_attempt_refusal_sends_nothing_and_records_nothing() -> None:
    log = EventLog(refuse_at=1)
    provider = LoggedProvider([ok()], log)
    with pytest.raises(Refused):
        await run_logged(provider, log)
    assert provider.calls == 0
    assert log.records == []
    assert log.events == ["hook1"]


async def test_before_attempt_refusal_on_a_retry_stops_the_call() -> None:
    log = EventLog(refuse_at=2)
    provider = LoggedProvider([LLMError("overloaded"), ok()], log)
    with pytest.raises(Refused):  # the hook's own exception, not a retryable LLMError
        await run_logged(provider, log)
    assert provider.calls == 1
    assert [(r.attempt, r.status, r.error_kind) for r in log.records] == [(1, "error", "overloaded")]
    assert log.events == ["hook1", "call1", "record1", "sleep", "hook2"]  # no sleep after the refusal
    assert log.sleeps == [0.5]


async def test_before_attempt_comes_before_the_restart_notice() -> None:
    log = EventLog()
    provider = StreamingProvider([LLMError("overloaded"), ok()], deltas=[["Hel"], ["Hello"]])

    async def on_delta(text: str) -> None:
        log.events.append(text)

    async def on_restart(attempt: int) -> None:
        log.events.append(f"<restart {attempt}>")

    async def hook(attempt: int) -> None:
        log.events.append(f"<hook {attempt}>")

    await create_with_retry(
        provider,
        REQ,
        on_text_delta=on_delta,
        on_restart=on_restart,
        before_attempt=hook,
        sleep=log.sleep,
        rng=lambda: 0.5,
    )
    assert [e for e in log.events if e != "sleep"] == ["<hook 1>", "Hel", "<hook 2>", "<restart 2>", "Hello"]


async def test_refused_retry_does_not_announce_a_restart() -> None:
    provider = StreamingProvider([LLMError("overloaded"), ok()], deltas=[["Hel"], ["Hello"]])
    restarts: list[int] = []

    async def on_delta(_: str) -> None:
        return None

    async def on_restart(attempt: int) -> None:
        restarts.append(attempt)

    async def refuse_retry(attempt: int) -> None:
        if attempt > 1:
            raise Refused(attempt)

    with pytest.raises(Refused):
        await create_with_retry(
            provider,
            REQ,
            on_text_delta=on_delta,
            on_restart=on_restart,
            before_attempt=refuse_retry,
            sleep=Recorder().sleep,
            rng=lambda: 0.5,
        )
    assert provider.calls == 1
    assert restarts == []  # the consumer is not told to discard text for an attempt never sent


async def test_before_attempt_is_not_called_once_cancelled() -> None:
    log = EventLog()
    provider = LoggedProvider([ok()], log)
    token = CancelToken()
    token.cancel()
    with pytest.raises(LLMError) as raised:
        await run_logged(provider, log, cancel=token)
    assert raised.value.kind == "cancelled"
    assert log.events == []
    assert provider.calls == 0


# --- a ledger failure while a cancel is reported (review L7) ----------------------------------------------


async def test_a_ledger_failure_during_a_cancel_still_cancels_and_is_logged() -> None:
    """The caller is going away, so the cancel wins; the lost ledger row is logged, never silent, and the
    CancelledError carries the ledger error as its cause."""
    from structlog.testing import capture_logs

    entered = asyncio.Event()

    class Hanging(FakeProvider):
        async def create_message(self, req, *, on_text_delta=None, cancel=None):  # type: ignore[no-untyped-def]
            self.calls += 1
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    class LedgerDown(Exception):
        pass

    async def on_attempt(record: AttemptRecord) -> None:
        raise LedgerDown("usage_records is unavailable")

    req = LLMRequest(
        model="claude-test-model",
        system="the secret system prompt",
        messages=(Message("user", "the secret user text"),),
        metadata={"call_id": "call-7"},
    )
    with capture_logs() as logs:
        caller = asyncio.create_task(create_with_retry(Hanging([]), req, on_attempt=on_attempt))
        await asyncio.wait_for(entered.wait(), 5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError) as raised:
            await caller
    assert caller.cancelled()
    assert isinstance(raised.value.__cause__, LedgerDown)
    assert [log for log in logs if log["event"] == "usage_attempt_unrecorded"] == [
        {
            "event": "usage_attempt_unrecorded",
            "log_level": "error",
            "call_id": "call-7",
            "attempt": 1,
            "error_type": "LedgerDown",
        }
    ]
    assert "secret" not in repr(logs)  # no prompt or message text in the event
