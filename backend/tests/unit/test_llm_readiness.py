"""Claude readiness gate runner (spec 3.1e Goal 2, ADR-032, API §11 #8, PRD §7.3.6): nine named checks
through the ``LLMProvider`` port only, each passed/failed/skipped with a redacted detail, ``ready`` only when
all pass."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from app.core.cancel import CancelToken
from app.llm.anthropic_provider import AnthropicClaudeProvider
from app.llm.base import LLMError, LLMRequest, LLMResponse, TextBlock, TextDeltaHandler, Usage
from app.llm.pricing import PricingError, PricingTable
from app.llm.readiness import CHECK_KEYS, ReadinessReport, aborted_report, run_readiness
from app.llm.scripted_provider import ScriptedProvider
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

KEY = "sk-ant-" + "smoke-" + "x" * 24  # built, not written: a test value, never a credential
MODELS = {"default": "claude-test-default", "fast": "claude-test-fast", "complex": "claude-test-default"}
RAN_AT = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
DEPENDENT = (
    "model_accessible",
    "messages_ok",
    "streaming_ok",
    "usage_reported",
    "tool_use_roundtrip",
    "timeout_cancel_handled",
)


def pricing(*, verify: bool = False, models: tuple[str, ...] = ("claude-test-default", "claude-test-fast")):
    price = {"input": "3", "output": "15", "cache_write": "3.75", "cache_read": "0.30"}
    return PricingTable.model_validate(
        {
            "version": "2026-11-08",
            "currency": "USD",
            "source": "https://example.test/pricing",
            "verify_before_production": verify,
            "models": {model: price for model in models},
        }
    )


Step = Callable[[LLMRequest, TextDeltaHandler | None, CancelToken | None], Any]


class Patched:
    """The scripted readiness answers, with one step replaced: a provider that breaks one check."""

    def __init__(self, name: str = "anthropic", **steps: Step) -> None:
        self.name = name
        self._steps = steps
        self._scripted = ScriptedProvider()

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse:
        step = req.metadata["agent_id"].removeprefix("readiness.")
        if step in self._steps:
            result = self._steps[step](req, on_text_delta, cancel)
            return await result if hasattr(result, "__await__") else result
        return await self._scripted.create_message(req, on_text_delta=on_text_delta, cancel=cancel)


def response(req: LLMRequest, text: str = "ready", **overrides: Any) -> LLMResponse:
    fields: dict[str, Any] = {
        "content": (TextBlock(text),) if text else (),
        "stop_reason": "end_turn",
        "usage": Usage(10, 2),
        "model": req.model,
        "request_id": "req_test",
        "duration_ms": 1,
    }
    return LLMResponse(**{**fields, **overrides})


def raise_(error: Exception) -> Step:
    def step(*_: Any) -> Any:
        raise error

    return step


async def run(provider: Any, **overrides: Any) -> ReadinessReport:
    options: dict[str, Any] = {
        "models": MODELS,
        "key": KEY,
        "production": False,
        "pricing": pricing(),
        "ran_at": RAN_AT,
    }
    return await run_readiness(provider, **{**options, **overrides})


def statuses(report: ReadinessReport) -> dict[str, str]:
    return {check.key: check.status for check in report.checks}


def detail(report: ReadinessReport, key: str) -> str:
    return next(check.detail for check in report.checks if check.key == key)


def only_failed(report: ReadinessReport, key: str) -> None:
    got = statuses(report)
    assert got[key] == "failed", (key, report.to_dict())
    assert all(status == "passed" for name, status in got.items() if name != key), report.to_dict()
    assert report.ready is False


async def test_scripted_provider_passes_all_nine_checks_in_the_contract_shape() -> None:
    report = await run(ScriptedProvider())
    assert [check.key for check in report.checks] == list(CHECK_KEYS)
    assert len(CHECK_KEYS) == 9
    assert set(statuses(report).values()) == {"passed"}, report.to_dict()
    assert report.ready is True and report.mode == "scripted" and report.provider == "anthropic"
    data = report.to_dict()
    assert set(data) == {"provider", "mode", "ready", "checks", "ran_at"}
    assert data["ran_at"] == RAN_AT.isoformat()
    assert all(set(check) == {"key", "label", "status", "detail"} for check in data["checks"])
    json.dumps(data)  # stored as JSONB and returned by the API as is


async def test_a_refused_key_fails_api_key_valid_and_skips_what_depends_on_it() -> None:
    refused = LLMError("auth", status_code=401, vendor_type="authentication_error")
    report = await run(Patched(probe=raise_(refused)))
    got = statuses(report)
    assert got["api_key_valid"] == "failed" and "auth" in detail(report, "api_key_valid")
    assert all(got[key] == "skipped" for key in DEPENDENT), got
    # The error path is where a key would leak, so the log check still runs; pricing does not need the key.
    assert got["secret_not_in_logs"] == "passed" and got["pricing_verified"] == "passed"
    assert report.ready is False


async def test_a_probe_that_fails_for_another_reason_does_not_claim_the_key_is_valid() -> None:
    report = await run(Patched(probe=raise_(LLMError("overloaded", status_code=529))))
    assert statuses(report)["api_key_valid"] == "failed"
    assert "overloaded" in detail(report, "api_key_valid") and report.ready is False


async def test_every_configured_model_is_called_and_a_refused_one_is_named() -> None:
    seen: list[str] = []

    def model(req: LLMRequest, *_: Any) -> LLMResponse:
        seen.append(req.model)
        if req.model == "claude-test-fast":
            raise LLMError("bad_request", status_code=404, vendor_type="not_found_error")
        return response(req, "ok")

    report = await run(Patched(model=model))
    only_failed(report, "model_accessible")
    assert "fast=claude-test-fast" in detail(report, "model_accessible")
    assert "not_found_error" in detail(report, "model_accessible")
    assert seen == ["claude-test-fast"]  # the default model is proven by the probe; complex is the same id


async def test_an_empty_answer_fails_messages_ok() -> None:
    report = await run(Patched(probe=lambda req, on_delta, _: response(req, "")))
    assert statuses(report)["messages_ok"] == "failed" and report.ready is False


async def test_an_answer_without_text_deltas_fails_streaming_ok() -> None:
    report = await run(Patched(probe=lambda req, on_delta, _: response(req, "ready")))  # never calls on_delta
    only_failed(report, "streaming_ok")


async def test_deltas_that_do_not_add_up_to_the_answer_fail_streaming_ok() -> None:
    async def probe(req: LLMRequest, on_delta: TextDeltaHandler | None, _: Any) -> LLMResponse:
        assert on_delta is not None
        await on_delta("rea")
        return response(req, "ready")

    only_failed(await run(Patched(probe=probe)), "streaming_ok")


@pytest.mark.parametrize("usage", [None, Usage(0, 0), Usage(10, 0)])
async def test_missing_usage_fails_usage_reported(usage: Usage | None) -> None:
    async def probe(req: LLMRequest, on_delta: TextDeltaHandler | None, _: Any) -> LLMResponse:
        assert on_delta is not None
        await on_delta("ready")
        return response(req, "ready", usage=usage)

    only_failed(await run(Patched(probe=probe)), "usage_reported")


async def test_a_text_answer_to_the_tool_request_fails_tool_use_roundtrip() -> None:
    report = await run(Patched(tool=lambda req, *_: response(req, "I will not call tools")))
    only_failed(report, "tool_use_roundtrip")


async def test_a_tool_call_with_the_wrong_input_fails_tool_use_roundtrip() -> None:
    from app.llm.base import ToolUseBlock

    def tool(req: LLMRequest, *_: Any) -> LLMResponse:
        call = ToolUseBlock("toolu_1", "readiness.echo", {"value": "something else"})
        return response(req, "", content=(call,), stop_reason="tool_use")

    report = await run(Patched(tool=tool))
    only_failed(report, "tool_use_roundtrip")


async def test_a_request_that_ignores_its_time_limit_fails_timeout_cancel_handled() -> None:
    report = await run(Patched(timeout=lambda req, *_: response(req, "too late")))
    only_failed(report, "timeout_cancel_handled")
    assert "timeout" in detail(report, "timeout_cancel_handled")


async def test_a_stream_that_ignores_cancel_fails_timeout_cancel_handled() -> None:
    async def cancel(req: LLMRequest, on_delta: TextDeltaHandler | None, token: CancelToken | None) -> Any:
        assert on_delta is not None and token is not None
        await on_delta("one ")
        await on_delta("two ")  # the token is set by now; a provider that honours it raises
        return response(req, "one two ")

    report = await run(Patched(cancel=cancel))
    only_failed(report, "timeout_cancel_handled")
    assert "cancel" in detail(report, "timeout_cancel_handled")


async def test_the_key_written_to_the_log_output_fails_secret_not_in_logs() -> None:
    async def probe(req: LLMRequest, on_delta: TextDeltaHandler | None, _: Any) -> LLMResponse:
        print(f"debug: sending with {KEY}")  # what an unredacted SDK debug line would do
        assert on_delta is not None
        await on_delta("ready")
        return response(req, "ready")

    report = await run(Patched(probe=probe))
    only_failed(report, "secret_not_in_logs")
    assert KEY not in json.dumps(report.to_dict())


async def test_the_key_on_stderr_or_in_a_stdlib_log_record_written_there_is_caught_too() -> None:
    async def probe(req: LLMRequest, on_delta: TextDeltaHandler | None, _: Any) -> LLMResponse:
        handler = logging.StreamHandler(sys.stderr)  # resolved now, inside the capture
        log = logging.getLogger("tests.readiness.leak")
        log.addHandler(handler)
        try:
            log.warning("leaking %s", KEY)
        finally:
            log.removeHandler(handler)
        assert on_delta is not None
        await on_delta("ready")
        return response(req, "ready")

    only_failed(await run(Patched(probe=probe)), "secret_not_in_logs")


async def test_the_key_never_reaches_a_detail_even_when_the_provider_echoes_it() -> None:
    echoed = LLMError("auth", status_code=401, vendor_type=f"authentication_error {KEY}")
    report = await run(Patched(probe=raise_(echoed)))
    text = json.dumps(report.to_dict())
    assert KEY not in text and "[REDACTED]" in detail(report, "api_key_valid"), text


async def test_an_unexpected_exception_is_a_failed_check_named_by_type_only() -> None:
    report = await run(Patched(tool=raise_(RuntimeError(f"boom with {KEY}"))))
    only_failed(report, "tool_use_roundtrip")
    assert "RuntimeError" in detail(report, "tool_use_roundtrip")
    assert KEY not in json.dumps(report.to_dict()) and "boom" not in detail(report, "tool_use_roundtrip")


async def test_unverified_pricing_is_skipped_outside_production_and_still_ready() -> None:
    report = await run(ScriptedProvider(), pricing=pricing(verify=True))
    assert statuses(report)["pricing_verified"] == "skipped" and report.ready is True
    assert "verify_before_production" in detail(report, "pricing_verified")


async def test_unverified_pricing_fails_in_production() -> None:
    """Inherited AG-13 3.1a condition: verify_before_production is enforced (mutant E10)."""
    report = await run(ScriptedProvider(), pricing=pricing(verify=True), production=True)
    only_failed(report, "pricing_verified")


async def test_a_model_without_a_price_fails_pricing_verified() -> None:
    report = await run(ScriptedProvider(), pricing=pricing(models=("claude-test-default",)))
    only_failed(report, "pricing_verified")
    assert "claude-test-fast" in detail(report, "pricing_verified")


async def test_an_unreadable_pricing_file_fails_pricing_verified() -> None:
    report = await run(ScriptedProvider(), pricing=PricingError("cannot read pricing file x: OSError"))
    only_failed(report, "pricing_verified")
    assert "cannot read pricing file" in detail(report, "pricing_verified")


async def test_ready_needs_every_check_to_pass() -> None:
    """Mutant E2: one failed (or skipped, but for pricing outside production) check makes it not ready."""
    report = await run(ScriptedProvider())
    for key in CHECK_KEYS:
        for status in ("failed", "skipped"):
            checks = tuple(replace(c, status=status) if c.key == key else c for c in report.checks)
            ready = replace(report, checks=checks).ready
            assert ready is (key == "pricing_verified" and status == "skipped"), (key, status)


def test_a_worker_without_a_key_gets_a_report_that_says_so() -> None:
    report = aborted_report(
        "ANTHROPIC_API_KEY is not set in the worker", models=MODELS, production=False, pricing=pricing(),
        ran_at=RAN_AT,
    )  # fmt: skip
    got = statuses(report)
    assert got["api_key_valid"] == "failed" and "not set" in detail(report, "api_key_valid")
    assert all(got[key] == "skipped" for key in (*DEPENDENT, "secret_not_in_logs")), got
    assert report.mode == "anthropic" and report.ready is False


# --- the real adapter, over a fake Messages API -------------------------------------------------------------


def fake_api(*, refuse: bool = False) -> FakeAnthropic:
    """Answers like the Messages API: the echo tool call, the finish after its result, a stalled stream for
    the counting prompt (the timeout and cancel probes), and a short text otherwise."""

    def answer(request: Any) -> Any:
        if refuse:
            return error_response(401, "authentication_error", message="invalid x-api-key")
        body = json.loads(request.content)
        first = body["messages"][0]["content"]
        if len(body["messages"]) > 1:
            return stream_response(sse(message_start(), *text_block(0, "done"), *message_end("end_turn")))
        if "tools" in body:
            value = re.search(r'value "([^"]+)"', first).group(1)  # type: ignore[union-attr]
            call = tool_block(0, "toolu_1", "readiness__echo", json.dumps({"value": value}))
            return stream_response(sse(message_start(), *call, *message_end("tool_use")))
        if first.startswith("Count"):
            stalled = ChunkStream(sse(message_start(), *text_block(0, "1\n")[:2]), hold=asyncio.Event())
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=stalled)
        return stream_response(sse(message_start(), *text_block(0, "rea", "dy"), *message_end("end_turn")))

    return FakeAnthropic(answer)


async def test_the_anthropic_adapter_passes_the_gate_against_a_conforming_api() -> None:
    report = await run(AnthropicClaudeProvider(KEY, transport=fake_api().transport()))
    assert report.mode == "anthropic" and report.ready is True, report.to_dict()


async def test_the_anthropic_adapter_with_a_refused_key_leaks_it_nowhere(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = await run(AnthropicClaudeProvider(KEY, transport=fake_api(refuse=True).transport()))
    assert statuses(report)["api_key_valid"] == "failed" and "authentication_error" in detail(
        report, "api_key_valid"
    )
    assert statuses(report)["secret_not_in_logs"] == "passed"
    captured = capsys.readouterr()
    assert KEY not in json.dumps(report.to_dict()) + captured.out + captured.err


# --- review fixes (silent-failure 3.1e1 L7, L8) ---------------------------------------------------------


async def test_a_provider_that_hangs_past_its_limit_fails_the_check_in_bounded_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.llm import readiness

    monkeypatch.setattr(readiness, "CALL_GRACE_S", 0.05)

    async def hang(*_: Any) -> Any:
        await asyncio.sleep(30)

    started = asyncio.get_running_loop().time()
    report = await run(Patched(tool=hang), timeout_s=0.05)
    assert asyncio.get_running_loop().time() - started < 5
    only_failed(report, "tool_use_roundtrip")
    assert "no answer within" in detail(report, "tool_use_roundtrip")


async def test_the_cancel_probe_uses_the_runs_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []

    async def cancel(req: LLMRequest, on_delta: TextDeltaHandler | None, token: CancelToken | None) -> Any:
        seen.append(req.timeout_s)
        assert on_delta is not None and token is not None
        await on_delta("one ")
        raise LLMError("cancelled")

    await run(Patched(cancel=cancel), timeout_s=7.0)
    assert seen == [7.0]


async def test_log_output_that_cannot_be_observed_is_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero observed lines would make secret_not_in_logs pass vacuously (LOG_LEVEL above INFO, or no log
    pipeline): the canary line must be seen."""
    from app.llm import readiness

    monkeypatch.setattr(readiness, "_log_canary", lambda marker: None)
    report = await run(ScriptedProvider())
    only_failed(report, "secret_not_in_logs")
    assert "could not be observed" in detail(report, "secret_not_in_logs")


async def test_a_default_model_the_provider_refuses_is_named_in_model_accessible() -> None:
    """Code review 3.1e1 spec c4: a wrong default model id is a model problem, not only an unconfirmed key."""
    missing = LLMError("bad_request", status_code=404, vendor_type="not_found_error")
    report = await run(Patched(probe=raise_(missing)))
    got = statuses(report)
    assert got["api_key_valid"] == "failed" and got["model_accessible"] == "failed"
    assert "default=claude-test-default" in detail(report, "model_accessible")
    assert all(got[key] == "skipped" for key in DEPENDENT if key != "model_accessible"), got
