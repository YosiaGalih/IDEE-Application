"""Claude API readiness gate (PRD §7.3.6, FR-135, ADR-032; spec 3.1e Goal 2).

Nine named checks, through the ``LLMProvider`` port only (the vendor SDK stays in its adapter): the eight of
API §11 #8 plus ``pricing_verified`` (inherited AG-13 3.1a condition). A check is ``passed``, ``failed`` or
``skipped`` (its dependency did not pass); one failing check does not stop the others. ``ready`` means every
check passed, with one exception: ``pricing_verified`` may be skipped outside production.

The prompts are synthetic fixtures, never a user's repository (PRD §7.3.6). Every detail goes through the
redactor with the key as a literal, and details are built from error kinds, HTTP statuses, vendor error types
and model ids only, never from a request or response body.

``secret_not_in_logs`` reads what the process wrote to stdout and stderr (the container log) while the
calls ran, after the logging pipeline's own redaction. It swaps ``sys.stdout``/``sys.stderr`` meanwhile, so
it runs in the worker or the CLI only, never in the ``api`` process (which does not hold the key anyway,
Lead decision 3.1e #2).
"""

from __future__ import annotations

import asyncio
import secrets
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from typing import Any, Literal, TextIO, cast

from app.core.cancel import CancelToken
from app.core.logging import get_logger
from app.core.redaction import Redactor
from app.llm.base import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    TextBlock,
    TextDeltaHandler,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)
from app.llm.pricing import PricingError, PricingTable

logger = get_logger(__name__)

CHECK_KEYS: tuple[str, ...] = (
    "api_key_valid",
    "model_accessible",
    "messages_ok",
    "streaming_ok",
    "usage_reported",
    "tool_use_roundtrip",
    "timeout_cancel_handled",
    "secret_not_in_logs",
    "pricing_verified",
)
LABELS: Mapping[str, str] = {
    "api_key_valid": "API key accepted",
    "model_accessible": "Configured models reachable",
    "messages_ok": "Messages API answers",
    "streaming_ok": "Streaming delivers the answer",
    "usage_reported": "Token usage reported",
    "tool_use_roundtrip": "Tool use round trip",
    "timeout_cancel_handled": "Timeout and cancel handled",
    "secret_not_in_logs": "Key absent from the logs",
    "pricing_verified": "Pricing table verified",
}
# Checks that need an answering provider: skipped when the probe (api_key_valid) did not pass.
_NEEDS_PROBE = (
    "model_accessible",
    "messages_ok",
    "streaming_ok",
    "usage_reported",
    "tool_use_roundtrip",
    "timeout_cancel_handled",
)
_SECRET_RULES = frozenset({"configured_secret", "anthropic_key", "anthropic_admin_key"})

# Seven calls at most: 7 x (15 + 5) s stays inside the smoke's own limit (provider_smoke.SMOKE_RUN_LIMIT_S).
CALL_TIMEOUT_S = 15.0
CALL_GRACE_S = 5.0  # an adapter that ignores its time limit is cut off this much later
TIMEOUT_PROBE_S = 0.01  # far below any real answer: the provider must raise ``timeout``
TIMEOUT_GRACE_S = 5.0  # ... and promptly
SCENARIO = "readiness"  # ScriptedProvider scenario; metadata never reaches a real provider
_SYSTEM = "You are a connectivity check. Follow the user's instruction exactly and keep answers short."
ECHO_TOOL = ToolDef(
    name="readiness.echo",
    description="Echoes a value back. Used by a connectivity check.",
    input_schema={
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    },
)

CheckStatus = Literal["passed", "failed", "skipped"]
# Whether model calls may go to the provider now (services.provider_readiness.provider_ready); the agent
# catalog shows it (FR-045).
ProviderState = Literal["ready", "not_ready", "disabled", "unknown"]
_Outcome = tuple[CheckStatus, str]


@dataclass(frozen=True, slots=True)
class Check:
    key: str
    status: CheckStatus
    detail: str

    @property
    def label(self) -> str:
        return LABELS[self.key]


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """API §11 #8 shape. ``provider`` is the configured vendor row; ``mode`` is what actually answered
    (``LLM_PROVIDER``): a report from another mode never counts as ready for this one (Lead decision 3.1e
    #8)."""

    provider: str
    mode: str
    checks: tuple[Check, ...]
    ran_at: datetime

    @property
    def ready(self) -> bool:
        if tuple(check.key for check in self.checks) != CHECK_KEYS:
            return False
        return all(
            check.status == "passed" or (check.key == "pricing_verified" and check.status == "skipped")
            for check in self.checks
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "mode": self.mode,
            "ready": self.ready,
            "checks": [
                {"key": check.key, "label": check.label, "status": check.status, "detail": check.detail}
                for check in self.checks
            ],
            "ran_at": self.ran_at.isoformat(),
        }


async def run_readiness(
    provider: LLMProvider,
    *,
    models: Mapping[str, str],
    key: str | None,
    production: bool,
    pricing: PricingTable | PricingError,
    provider_name: str = "anthropic",
    ran_at: datetime | None = None,
    timeout_s: float = CALL_TIMEOUT_S,
) -> ReadinessReport:
    """``models`` maps each alias (``default``, ``fast``, ``complex``) to the resolved model id; ``key`` is
    the key the provider was built with (``None`` in scripted mode without one), used only to redact and to
    look for it in the log output."""
    started = ran_at or datetime.now(UTC)
    outcomes: dict[str, _Outcome] = {}
    marker = f"kk-log-{secrets.token_hex(6)}"
    with _captured_output() as output:
        _log_canary(marker)
        await _provider_checks(provider, models, timeout_s, outcomes)
    outcomes["secret_not_in_logs"] = _secret_check(output.getvalue(), key, marker)
    outcomes["pricing_verified"] = _pricing_check(pricing, models, production)
    return _report(provider_name, provider.name, outcomes, key, started)


def aborted_report(
    reason: str,
    *,
    models: Mapping[str, str],
    production: bool,
    pricing: PricingTable | PricingError,
    provider_name: str = "anthropic",
    mode: str = "anthropic",
    ran_at: datetime | None = None,
    key: str | None = None,
) -> ReadinessReport:
    """A run that could not happen or finish (no key in the worker, a provider that cannot be built, a run
    past its time limit): ``reason`` is the failed ``api_key_valid``, what depends on it is skipped."""
    outcomes: dict[str, _Outcome] = {"api_key_valid": ("failed", reason)}
    outcomes["secret_not_in_logs"] = ("skipped", "skipped: no provider call was made")
    outcomes["pricing_verified"] = _pricing_check(pricing, models, production)
    return _report(provider_name, mode, outcomes, key, ran_at or datetime.now(UTC))


def _report(
    provider_name: str, mode: str, outcomes: Mapping[str, _Outcome], key: str | None, ran_at: datetime
) -> ReadinessReport:
    redactor = Redactor([key] if key else [])
    skipped: _Outcome = ("skipped", "skipped: api_key_valid did not pass")
    checks = tuple(
        Check(name, status, redactor.redact(detail))
        for name in CHECK_KEYS
        for status, detail in (outcomes.get(name, skipped),)
    )
    return ReadinessReport(provider=provider_name, mode=mode, checks=checks, ran_at=ran_at)


# --- provider checks ---------------------------------------------------------------------------------------


def _request(
    model: str,
    step: str,
    prompt: str | tuple[Message, ...],
    timeout_s: float,
    *,
    max_tokens: int,
    tools: tuple[ToolDef, ...] = (),
) -> LLMRequest:
    return LLMRequest(
        model=model,
        system=_SYSTEM,
        messages=(Message("user", prompt),) if isinstance(prompt, str) else prompt,
        tools=tools,
        max_tokens=max_tokens,
        metadata={"scenario": SCENARIO, "agent_id": f"readiness.{step}"},
        cache_system=False,
        timeout_s=timeout_s,
    )


async def _call(
    provider: LLMProvider,
    req: LLMRequest,
    *,
    on_text_delta: TextDeltaHandler | None = None,
    cancel: CancelToken | None = None,
) -> LLMResponse | LLMError | str:
    """The answer, the normalised provider error, or a description of anything else that went wrong. An
    adapter that ignores ``req.timeout_s`` is cut off ``CALL_GRACE_S`` later, so a run always ends."""
    try:
        async with asyncio.timeout(req.timeout_s + CALL_GRACE_S):
            return await provider.create_message(req, on_text_delta=on_text_delta, cancel=cancel)
    except LLMError as exc:
        return exc
    except TimeoutError:
        return f"no answer within {req.timeout_s + CALL_GRACE_S:g} s (the provider ignored its time limit)"
    except Exception as exc:
        # The adapter normalises provider failures to LLMError, so this is a bug; the check records it as
        # failed. Its message is not kept or logged: it may echo a request (and the key) unredacted.
        logger.warning(
            "readiness_unexpected_error", step=req.metadata.get("agent_id"), error=type(exc).__name__
        )
        return f"unexpected {type(exc).__name__}"


def _text(response: LLMResponse) -> str:
    return "".join(block.text for block in response.content if isinstance(block, TextBlock))


async def _provider_checks(
    provider: LLMProvider, models: Mapping[str, str], timeout_s: float, outcomes: dict[str, _Outcome]
) -> None:
    default = models["default"]
    deltas: list[str] = []

    async def collect(delta: str) -> None:
        deltas.append(delta)

    probe = await _call(
        provider,
        _request(default, "probe", "Reply with the single word: ready", timeout_s, max_tokens=16),
        on_text_delta=collect,
    )
    if not isinstance(probe, LLMResponse):
        outcomes["api_key_valid"] = (
            "failed",
            f"a request with the default model ({default}) failed: {probe}",
        )
        if not (isinstance(probe, LLMError) and probe.kind == "auth"):
            # Not a refused key: the default model itself may be the cause (a wrong id is a 404), so it is
            # named where a reader looks for model problems (code review 3.1e1 spec c4).
            outcomes["model_accessible"] = ("failed", f"default={default}: {probe}")
        return
    outcomes["api_key_valid"] = (
        "passed",
        f"the provider answered a request with the default model ({default})",
    )
    outcomes["messages_ok"] = _messages_check(probe)
    outcomes["streaming_ok"] = _streaming_check(probe, deltas)
    outcomes["usage_reported"] = _usage_check(probe)
    outcomes["model_accessible"] = await _models_check(provider, models, timeout_s)
    outcomes["tool_use_roundtrip"] = await _tool_check(provider, default, timeout_s)
    outcomes["timeout_cancel_handled"] = await _timeout_cancel_check(provider, default, timeout_s)


def _messages_check(probe: LLMResponse) -> _Outcome:
    text = _text(probe)
    if probe.stop_reason not in ("end_turn", "stop_sequence") or not text.strip():
        return "failed", f"no text answer (stop reason {probe.stop_reason}, {len(text)} characters)"
    return "passed", f"{probe.stop_reason}, {len(text)} characters"


def _streaming_check(probe: LLMResponse, deltas: list[str]) -> _Outcome:
    if not deltas:
        return "failed", "no text delta arrived while the answer streamed"
    if "".join(deltas) != _text(probe):
        return "failed", f"the {len(deltas)} text deltas do not add up to the answer"
    return "passed", f"{len(deltas)} text deltas add up to the answer"


def _usage_check(probe: LLMResponse) -> _Outcome:
    usage = probe.usage
    if usage is None:
        return "failed", "the answer reported no token usage"
    counts = f"input {usage.input_tokens}, output {usage.output_tokens} tokens"
    if usage.input_tokens <= 0 or usage.output_tokens <= 0:
        return "failed", f"the answer reported zero tokens ({counts})"
    return "passed", counts


async def _models_check(provider: LLMProvider, models: Mapping[str, str], timeout_s: float) -> _Outcome:
    """The default model answered the probe; every other distinct model id gets one small request."""
    checked = {models["default"]}
    failures: list[str] = []
    for alias, model in models.items():
        if model in checked:
            continue
        checked.add(model)
        answer = await _call(provider, _request(model, "model", "Reply with: ok", timeout_s, max_tokens=8))
        if not isinstance(answer, LLMResponse):
            failures.append(f"{alias}={model}: {answer}")
    if failures:
        return "failed", "; ".join(failures)
    return "passed", ", ".join(f"{alias}={model}" for alias, model in models.items())


async def _tool_check(provider: LLMProvider, model: str, timeout_s: float) -> _Outcome:
    value = f"kk-{secrets.token_hex(4)}"
    asked = Message("user", f'Call the readiness.echo tool once with value "{value}". Do not answer in text.')
    first = await _call(
        provider, _request(model, "tool", (asked,), timeout_s, tools=(ECHO_TOOL,), max_tokens=128)
    )
    if not isinstance(first, LLMResponse):
        return "failed", f"the tool request failed: {first}"
    calls = [block for block in first.content if isinstance(block, ToolUseBlock)]
    if first.stop_reason != "tool_use" or not calls or calls[0].name != ECHO_TOOL.name:
        return "failed", f"the model did not call {ECHO_TOOL.name} (stop reason {first.stop_reason})"
    call = calls[0]
    if call.input.get("value") != value:
        return "failed", f"{ECHO_TOOL.name} was called with another value than asked"
    history = (
        asked,
        Message("assistant", first.content),
        Message("user", (ToolResultBlock(call.id, value),)),
    )
    second = await _call(
        provider, _request(model, "tool", history, timeout_s, tools=(ECHO_TOOL,), max_tokens=64)
    )
    if not isinstance(second, LLMResponse):
        return "failed", f"the request with the tool result failed: {second}"
    if second.stop_reason not in ("end_turn", "stop_sequence"):
        return "failed", f"after the tool result the model stopped with {second.stop_reason}"
    return "passed", f"{ECHO_TOOL.name} was called, its result sent back, and the model finished"


async def _timeout_cancel_check(provider: LLMProvider, model: str, timeout_s: float) -> _Outcome:
    problems: list[str] = []
    counting_prompt = "Count from 1 to 200, one number per line."

    started = time.monotonic()
    timed = await _call(
        provider, _request(model, "timeout", counting_prompt, TIMEOUT_PROBE_S, max_tokens=512)
    )
    elapsed = time.monotonic() - started
    if isinstance(timed, LLMResponse):
        problems.append(f"a request with a {TIMEOUT_PROBE_S} s limit was answered instead of a timeout")
    elif not (isinstance(timed, LLMError) and timed.kind == "timeout"):
        problems.append(f"a request with a {TIMEOUT_PROBE_S} s limit failed with {timed}, not timeout")
    elif elapsed > TIMEOUT_GRACE_S:
        problems.append(f"a request with a {TIMEOUT_PROBE_S} s limit timed out only after {elapsed:.1f} s")

    token = CancelToken()
    deltas = 0

    async def cancel_on_first(_: str) -> None:
        nonlocal deltas
        deltas += 1
        token.cancel()

    cut = await _call(
        provider, _request(model, "cancel", counting_prompt, timeout_s, max_tokens=512),
        on_text_delta=cancel_on_first, cancel=token,
    )  # fmt: skip
    if isinstance(cut, LLMResponse):
        problems.append(
            "no text delta arrived to cancel on"
            if deltas == 0
            else "the stream finished after cancel was set"
        )
    elif not (isinstance(cut, LLMError) and cut.kind == "cancelled"):
        problems.append(f"cancel after the first text delta gave {cut}, not cancelled")

    if problems:
        return "failed", "; ".join(problems)
    return (
        "passed",
        f"a {TIMEOUT_PROBE_S} s limit raised timeout; cancel after the first delta raised cancelled",
    )


# --- log and pricing checks --------------------------------------------------------------------------------


class _Tee:
    """Writes through to the real stream and keeps a copy; anything else is the real stream's."""

    def __init__(self, target: TextIO, copy: StringIO) -> None:
        self._target = target
        self._copy = copy

    def write(self, text: str) -> int:
        self._copy.write(text)
        return self._target.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


@contextmanager
def _captured_output() -> Iterator[StringIO]:
    copy = StringIO()
    stdout, stderr = sys.stdout, sys.stderr
    sys.stdout = cast(TextIO, _Tee(stdout, copy))
    sys.stderr = cast(TextIO, _Tee(stderr, copy))
    try:
        yield copy
    finally:
        sys.stdout, sys.stderr = stdout, stderr


def _log_canary(marker: str) -> None:
    """One line through the logging pipeline, so the check knows it can see what the pipeline writes."""
    logger.info("readiness_log_check", marker=marker)


def _secret_check(output: str, key: str | None, marker: str) -> _Outcome:
    findings = [f for f in Redactor([key] if key else []).find(output) if f.rule in _SECRET_RULES]
    if findings:
        lines = sorted({finding.line for finding in findings})
        return "failed", f"a key appeared in the log output on {len(lines)} line(s) while the checks ran"
    if marker not in output:
        # Evidence over claims: no observed log output is not proof that the key stayed out of it.
        return "failed", "the log output could not be observed (LOG_LEVEL above INFO, or no log pipeline)"
    return "passed", f"no key in the {len(output.splitlines())} log lines written while the checks ran"


def _pricing_check(
    pricing: PricingTable | PricingError, models: Mapping[str, str], production: bool
) -> _Outcome:
    if isinstance(pricing, PricingError):
        return "failed", str(pricing).splitlines()[0]
    missing = sorted({model for model in models.values() if model not in pricing.models})
    if missing:
        return (
            "failed",
            f"pricing {pricing.version} has no price for {', '.join(missing)}: costs would be unknown",
        )
    if pricing.verify_before_production:
        flagged = f"pricing {pricing.version} is marked verify_before_production"
        if production:
            return "failed", f"{flagged}: verify the prices and clear the flag before production"
        return "skipped", f"{flagged} (allowed outside production)"
    return "passed", f"pricing {pricing.version} prices every configured model"
