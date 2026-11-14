"""The AgentRuntime (ARCHITECTURE §4.2; spec WP-3.1f Keputusan Lead #15-#35; plan WP-3.1f2a).

One agent run: the model is called through one path, ``AgentRuntime._call_model``, the only
``create_with_retry`` in app/ (Keputusan Lead #17; tests/unit/test_model_call_guard.py). Before every
attempt, the first and each retry, ``before_attempt`` renews the task lease, reads the provider gate afresh
and checks the budget; after every attempt that reached the provider, the ledger records it, and a ledger
failure fails the call (Keputusan Lead #8, #20).

The runtime reaches the database, providers, events and tools only through the ports below (ADR-022); the
adapters live in ``app.services.agent_runtime``. It holds no database session, ends agent runs and never
moves a task (Keputusan Lead #15): two import-linter contracts keep both true.

Nothing here logs a ``Message``, an ``LLMRequest`` or a tool result (their repr carries content): log lines
carry ids and type names only.
"""

from __future__ import annotations

import asyncio
import json
import re
import string
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from pydantic import BaseModel, ValidationError

from app.agents.contracts import INPUT_CONTRACTS, OUTPUT_CONTRACTS, Contract
from app.agents.grants import AgentNotRunnable, grant_for
from app.agents.prompt_book import Prompt, PromptBook
from app.agents.registry import AgentRegistry, AgentSpec
from app.core.cancel import CancelToken
from app.core.logging import get_logger
from app.core.redaction import (
    CREDENTIAL_HEADER_NAMES,
    CREDENTIAL_HEADER_START,
    KEY_HINT,
    SECRET_KEY_PATTERN,
    URL_SCHEME_PATTERN,
    Redactor,
    stopped_at_open_quote,
)
from app.llm.base import (
    ContentBlock,
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
from app.llm.retry import AttemptHandler, AttemptRecord, RestartHandler, RetryPolicy, create_with_retry
from app.tools.base import AgentGrant, ToolContext, ToolResult

if TYPE_CHECKING:
    from app.workspace.fs import TaskWorkspace

SUBMIT_RESULT: Final = "submit_result"  # == app.tools.gateway.TERMINAL_TOOL (test)
HEARTBEAT_INTERVAL_S: Final = 20.0  # == app.workflow.lease.HEARTBEAT_SECONDS (test)

RunStatus = Literal["succeeded", "failed", "cancelled", "budget_exceeded", "approval_required"]
# == app.models.enums.AGENT_RUN_ERROR_CODES (test), in the same order.
RunErrorCode = Literal[
    "PROVIDER_DISABLED",
    "provider_not_ready",
    "provider_auth",
    "usage_unrecorded",
    "invalid_output",
    "iteration_limit",
    "timeout",
    "lease_lost",
    "model_error",
    "internal_error",
    "worker_cancelled",
    "interrupted",
]
StopReason = Literal["cancelled", "timeout", "lease_lost"]
# The same values as app.workflow.budget.BudgetKind and app.llm.pricing.EstimateStatus, which the runtime may
# not import (layers; the runtime contract in pyproject.toml): the services that fill these ports pass the
# owners' values in, so pyright checks the two agree there, and tests/unit/test_agent_runtime.py keeps them
# equal.
BudgetKind = Literal["duration", "tool_iterations", "tokens", "cost"]
CostStatus = Literal["estimated", "partial", "unavailable"]

# One fixed sentence per error code for agent_runs.error (code review of 073e68d). A run's error text is its
# code's sentence plus, in brackets, an identifier: one the runtime knows (the time limit, the iteration
# cap) or one the error carries as a field (an LLMError's kind, a ProviderBlocked reason, an unrecorded
# attempt's ids). Never an exception's own message, which may carry a host, a path or a value.
ERROR_TEXTS: Final[Mapping[RunErrorCode, str]] = MappingProxyType(
    {
        "PROVIDER_DISABLED": "the provider is switched off",
        "provider_not_ready": "the provider is not ready",
        "provider_auth": "the provider refused its credentials",
        "usage_unrecorded": "the usage of a model call was not recorded",
        "invalid_output": "the model ended its turn twice without submit_result",
        "iteration_limit": "the run used its iterations without a valid submit_result",
        "timeout": "the run passed its time limit",
        "lease_lost": "the task lease was lost",
        "model_error": "the model call failed",
        "internal_error": "an internal error ended the run",
        "worker_cancelled": "the run was cancelled: its worker stopped",
        "interrupted": "the run was interrupted: its worker is gone",
    }
)

CONTEXT_MAX_BYTES: Final = 60 * 1024  # ADR-011: the first input of a run
ABSORBED_PROFILES: Final = frozenset({"A01", "C16"})  # AGENT_CATALOG §4.2: never dispatched alone
STEPS: Final[Mapping[str, str]] = MappingProxyType(
    {"A02": "intake", "A03": "plan", "B14": "implement", "C20": "debug", "D22": "review", "A07": "user_proxy"}
)
NUDGE: Final = (
    "You ended your turn without calling submit_result. Call submit_result now with input that matches its "
    "schema."
)
SUBMIT_DESCRIPTION: Final = (
    "Submit the result of this run: call it exactly once, with input that matches this schema. It ends the "
    "run."
)
ONE_SUBMIT: Final = "Only one submit_result per turn is accepted: none of them was taken. Call it once."
ERROR_TEXT_MAX: Final = 1000
VALIDATION_ERRORS_SHOWN: Final = 20
REQUEST_BASE_S: Final = 30.0  # connect + time to first token for a long prompt [Menebak]
MIN_OUTPUT_TOKENS_PER_S: Final = 40.0  # the slowest output rate still waited for [Menebak]
# A held task row lock must never stall a run (reviews of 42bcb2e): every port the runtime awaits is bounded
# by the run's deadline, a beat try by its own timeout, the close by CLOSE_TIMEOUT_S.
BEAT_RETRIES: Final = 2  # a failed beat is tried again twice before the lease counts as lost
BEAT_TIMEOUT_MIN_S: Final = 1.0
CLOSE_TIMEOUT_S: Final = 5.0
# A paid attempt's ledger write may end this long after the run's deadline (review N1 of 073e68d): the money
# is spent, so its row gets a short grace; past it the attempt is unrecorded.
LEDGER_GRACE_S: Final = 5.0
TOOL_STOP_GRACE_S: Final = 10.0  # past the deadline, for the gateway to record a cancelled tool
# A field location is named only when it looks like a field name: a key the model invented is input too.
_FIELD = re.compile(r"[a-z_][a-z0-9_]{0,39}")

DELTA_HOLD_CHARS: Final = 256  # Keputusan Lead #24, #34: the redacted tail held back
# The whole text is redacted again only after this many more raw characters (Keputusan Lead #34 keeps the
# whole text; one redaction per 16-character delta took 5.3 s for 32 000 characters, measured in Task 6).
DELTA_RELEASE_CHARS: Final = 256
# Past DELTA_RELEASE_CHARS x DELTA_RELEASE_SHARE characters (8 KiB) the step grows with the text, to 1/32
# of it: a 64 KiB attempt is redacted about 100 times instead of 256, in well under a second (AG-13 D-M1).
DELTA_RELEASE_SHARE: Final = 32

_log = get_logger(__name__)


# Where a secret may still be growing at the end of the RAW text: nothing goes out from the start of one until
# it closes or the call's stream ends (reviews of 42bcb2e: M3, AG-13 INFO-3; of 073e68d: AG-13 D-M1, D-L1;
# WP-3.1f2c D2-L2, D2-I1). Each check is a scan from the end at C speed, or a search in a short window that
# ends there, linear in the text: a `\Z`-anchored search tried from every start was quadratic (a 64 KiB
# attempt took 90 s). The raw text, not the redacted one: a rule can take the key before its value's quote is
# closed (`Cookie: session="…` reads `Cookie: [REDACTED]"…` until the quote closes).
_TOKEN_CHARS: Final = string.ascii_letters + string.digits + "_.-"
_PEM_BEGIN: Final = "-----BEGIN"
_PEM_END: Final = "-----END"
# A URL's userinfo grows until "@"; a "/" or a space ends the URL before any (Lead decision 3.1f2c #11).
_URL_CLOSED: Final = re.compile(r"[@/\s]")
_URL_SCHEME: Final = re.compile(rf"(?:{URL_SCHEME_PATTERN})\Z")  # the redactor's own scheme, leftmost
_SCHEME_WINDOW: Final = 64  # longer than any scheme URL_SCHEME_PATTERN takes (32)
# What opens a quoted value before its quote: a string prefix (b"…, rb'…, as the redactor reads one) or the
# backslash of an escaped JSON quote (\"…). Spaces before it are the gap, not part of the key's separator.
_VALUE_OPENER: Final = re.compile(r"(?:(?<![A-Za-z0-9_])[bBfFrRuU]{1,2}|\\)\Z")
# What precedes a quoted value the redactor redacts, up to the last character before the gap, in the forms its
# rules read (Lead decision 3.1f2c #10; the Lead's Task 2 carry):
# - a key and its assignment, as _KEY_VALUE reads them: password=, "password": , \"password\": , --token=
# - an argv item and its comma, as _ARGV_VALUE reads them: "--password",
# - a flag word, as _FLAG_VALUE and _ARGV_VALUE read it: --password, "--password". A word opens a value only
#   after a gap of spaces (the "word" group): the closing quote of "--password" in prose opens none.
# The key is SECRET_KEY_PATTERN, the redactor's own key names (Goal 2), with no left boundary: a token pattern
# that ran on into the key (ghp_…password="V", which the redactor reads as [REDACTED]="V") leaves the key in
# the raw text.
_KEY_BEFORE_QUOTE: Final = re.compile(
    rf"""(?ix)
    (?: (?:{SECRET_KEY_PATTERN}) \\?["']? \s*[:=]
      | ["'](?=-)(?:{SECRET_KEY_PATTERN}) \\?["'] \s*,
      | (?P<word> (?=-)(?:{SECRET_KEY_PATTERN}) | ["'](?=-)(?:{SECRET_KEY_PATTERN}) \\?["'] )
    )\Z
    """
)
_KEY_WINDOW: Final = 128  # how far before the last character before the gap the key is looked for
_LONGEST_HEADER_NAME: Final = max(map(len, CREDENTIAL_HEADER_NAMES))


def open_candidate(text: str) -> int | None:
    """Where the first candidate still open at the end of ``text`` starts, or None:
    - an ``eyJ`` token run that has not ended;
    - a ``-----BEGIN`` with no ``-----END`` after it;
    - a ``scheme://`` with no "@", "/" or space after it yet: userinfo that may still grow (``_open_url``);
    - an opening quote with no closing one after it, after a key the redactor treats as secret
      (``_key_before``);
    - the value of a header whose line is the last line (``_open_header``)."""
    found: list[int] = []
    run = text.find("eyJ", len(text.rstrip(_TOKEN_CHARS)))
    if run != -1:
        found.append(run)
    last_end = text.rfind(_PEM_END)
    begin = text.find(_PEM_BEGIN, 0 if last_end == -1 else last_end + 1)
    if begin != -1:
        found.append(begin)
    if (url := _open_url(text)) is not None:
        found.append(url)
    for quote in "\"'":
        at = text.rfind(quote)  # the last one: no quote of its kind closes it
        if at != -1 and (key := _key_before(text, at)) is not None:
            found.append(key)
    if (header := _open_header(text)) is not None:
        found.append(header)
    return min(found, default=None)


def _open_url(text: str) -> int | None:
    """Where a URL whose userinfo may still grow starts: the last ``://`` (an earlier one has the "//" of
    this one after it), with no "@", "/" or space after it, from its scheme (Lead decision 3.1f2c #11). A URL
    longer than the hold is then redacted whole once its "@" arrives, never streamed in part before."""
    at = text.rfind("://")
    if at == -1 or _URL_CLOSED.search(text, at + 3) is not None:
        return None
    scheme = _URL_SCHEME.search(text, max(at - _SCHEME_WINDOW, 0), at)
    return None if scheme is None else scheme.start()


def _key_before(text: str, quote: int) -> int | None:
    """Where the key starts whose value the quote at ``quote`` opens, or None (Lead decision 3.1f2c #10). The
    key and its separator end at the last non-space character before the value's opening and are looked for
    within ``_KEY_WINDOW`` characters of it, however many spaces or line breaks come between."""
    opener = _VALUE_OPENER.search(text, max(quote - 2, 0), quote)
    head = quote if opener is None else opener.start()
    stop = len(text[:head].rstrip())  # one scan from the end at C speed
    key = _KEY_BEFORE_QUOTE.search(text, max(stop - _KEY_WINDOW, 0), stop)
    if key is None or (key["word"] is not None and stop == head):
        return None
    return key.start()


def _open_header(text: str) -> int | None:
    """Where the value of a header whose line is still open starts, or None (Lead rulings, WP-3.1f2c fix
    rounds). Without an opening quote after its colon a header value is the rest of its line, and a quote
    that opens a parameter may take later lines or the rest of the text (CREDENTIAL_HEADER): while the
    header's line is the last line of the text, its value is held from its start. No quote is paired here:
    the redactor reads the text with tokens, URL credentials and literals replaced, and a replaced span may
    hold a quote or a backslash, so the raw text pairs its quotes differently (review I-1 of 50610b5).

    A value starts on the last line when its header's name is on that line, or when only the separator comes
    between the name and that line (line breaks, the colon, a JSON key's closing quote and backslash): the
    search starts that far back, less the longest name, and no earlier than the first KEY_HINT word allows.
    Scans from the end, then ":" and KEY_HINT, then one search of CREDENTIAL_HEADER_START: linear."""
    line = max(text.rfind("\n"), text.rfind("\r")) + 1
    head = text[:line].rstrip()
    head = head[:-1].rstrip() if head.endswith(":") else head
    head = head[:-1] if head.endswith(('"', "'")) else head
    head = head[:-1] if head.endswith("\\") else head
    start = max(len(head) - _LONGEST_HEADER_NAME, 0)
    if text.find(":", start) == -1 or (hint := KEY_HINT.search(text, start)) is None:
        return None  # no separator, or no header name
    for header in CREDENTIAL_HEADER_START.finditer(text, max(start, hint.start() - _LONGEST_HEADER_NAME)):
        if header.end() >= line:
            return header.end()
    return None


def delta_hold(redactor: Redactor) -> int:
    """The ``hold`` of a run's delta stream (Lead decision 3.1f2c #11): DELTA_HOLD_CHARS, or the longest
    configured literal less one when that is more. A literal is redacted only once it is whole, so a part of
    one cut at the end of the text, at most that long, stays in the held tail."""
    return max(DELTA_HOLD_CHARS, redactor.longest_literal - 1)


def _common_prefix(a: str, b: str) -> int:
    low, high = 0, min(len(a), len(b))
    while low < high:  # slices compared at C speed: O(n log n)
        middle = (low + high + 1) // 2
        if a[:middle] == b[:middle]:
            low = middle
        else:
            high = middle - 1
    return low


@dataclass(frozen=True, slots=True)
class Delta:
    """One ``agent.delta``: ``text`` is appended to what the client shows, or replaces it when ``reset``."""

    text: str
    reset: bool = False


class DeltaRedactor:
    """The streamed text of a run, redacted across deltas (Keputusan Lead #24, #34; Keputusan plan #12;
    reviews of 42bcb2e: M3, AG-13 INFO-3, M4; of 073e68d: item 11, AG-13 D-M1, D-L1).

    The text of the current model call is redacted whole, never a piece of it, so a secret cut across deltas
    is seen whole. What goes out is the stable part of that redacted text: everything but its last ``hold``
    characters (a run's is ``delta_hold``: never less than its longest configured literal less one), and
    nothing from the start of a candidate still open at the end of the raw text (``open_candidate``), nor
    from a value the header rule ended at a quote its last line may still close (``redact_with_stop``, a
    run's ``Redactor.redact_with_stop``; without it, ``stopped_at_open_quote`` of the redacted text, which
    misses a stop a later rule took: a test's fake redaction only, as ``app`` builds a run's stream with
    ``delta_redactor``), minus what already went out. ``flush`` ends the call: its final redacted text goes
    out and is committed, never redacted again. When redacting a longer text changes what already went out, a
    ``reset`` delta carries the committed text of the calls before, then the current call's stable text: the
    client replaces, never appends. ``restart`` (a retried attempt) voids the current call's text, held or
    sent: its reset carries the committed text. The whole text is redacted again every ``release`` raw
    characters, or every 1/``DELTA_RELEASE_SHARE`` of the text once that is longer, so a stream costs about
    ``DELTA_RELEASE_SHARE`` redactions of its length, never a quadratic number."""

    def __init__(
        self,
        redact: Callable[[str], str],
        *,
        hold: int = DELTA_HOLD_CHARS,
        release: int = DELTA_RELEASE_CHARS,
        redact_with_stop: Callable[[str], tuple[str, int | None]] | None = None,
    ) -> None:
        self._redact = redact
        self._redact_with_stop = redact_with_stop
        self._hold = hold
        self._release = max(release, 1)
        self._committed = ""
        self.reset()

    def feed(self, text: str) -> Delta | None:
        """What is now safe to send; ``None`` when nothing is yet."""
        self._text += text
        step = max(self._release, len(self._text) // DELTA_RELEASE_SHARE)
        if len(self._text) - self._redacted_at < step:
            return None
        self._redacted_at = len(self._text)
        if self._redact_with_stop is None:
            redacted = self._redact(self._text)
            return self._emit(redacted, final=False, stop=stopped_at_open_quote(redacted))
        redacted, stop = self._redact_with_stop(self._text)
        return self._emit(redacted, final=False, stop=stop)

    def flush(self) -> Delta | None:
        """End of the call: the rest, redacted; the call's text is committed; the next call starts empty."""
        redacted = self._redact(self._text)
        out = self._emit(redacted, final=True)
        self._committed += redacted
        self.reset()
        return out

    def restart(self) -> Delta:
        """A retried attempt: its text is void; the client goes back to the committed text."""
        self.reset()
        return Delta(self._committed, reset=True)

    def reset(self) -> None:
        """Drops the current call's text, never sending what is held."""
        self._text = ""
        self._sent = ""
        self._redacted_at = 0
        self._before: tuple[int, str] | None = None  # (candidate start, redaction of the raw text before it)

    def _emit(self, redacted: str, *, final: bool, stop: int | None = None) -> Delta | None:
        consistent = redacted.startswith(self._sent)
        safe = len(redacted)
        if not final:
            safe -= self._hold
            start = open_candidate(self._text)
            if start is not None:
                safe = min(safe, self._redacted_before(start, redacted))
            if stop is not None:
                safe = min(safe, stop)
        stable = redacted[: max(safe, 0)]
        if not consistent:
            self._sent = stable
            return Delta(self._committed + stable, reset=True)
        if len(stable) <= len(self._sent):
            return None
        out = Delta(stable[len(self._sent) :])
        self._sent = stable
        return out

    def _redacted_before(self, start: int, redacted: str) -> int:
        """How much of ``redacted`` comes from the raw text before ``start`` alone: its common prefix with the
        redaction of that text (kept while the same candidate stays open)."""
        if self._before is None or self._before[0] != start:
            self._before = (start, self._redact(self._text[:start]))
        before = self._before[1]
        return len(before) if redacted.startswith(before) else _common_prefix(before, redacted)


def delta_redactor(redactor: Redactor, *, release: int = DELTA_RELEASE_CHARS) -> DeltaRedactor:
    """A run's delta stream (WP-3.1f2c): the redactor's redaction, its hold (``delta_hold``) and its header
    stops (``Redactor.redact_with_stop``). It is the one place in ``app`` that builds a ``DeltaRedactor``
    (``tests/unit/test_delta_redactor_guard.py``): one built without ``redact_with_stop`` reads the stop on
    the redacted text, where a rule after the header rule may have taken it (review of 45c2ec7). A run keeps
    the default ``release``; a test that checks every cut passes 1."""
    return DeltaRedactor(
        redactor.redact,
        hold=delta_hold(redactor),
        release=release,
        redact_with_stop=redactor.redact_with_stop,
    )


class AgentInputInvalid(ValueError):
    """The input is not an instance of the runtime agent's input contract (a caller error: no run row)."""


class AgentInputTooLarge(AgentInputInvalid):
    """The input is over ``CONTEXT_MAX_BYTES`` (ADR-011)."""


class ScenarioNotAllowed(ValueError):
    """A scripted scenario was asked of a provider that is not scripted (ADR-015)."""


# AG-13 3.1f2a LOW-1: "<" occurs in JSON only inside a string, so its JSON escape keeps the document valid and
# its value the same, and no tag in the input (a closing </agent_input>, a forged <untrusted_content>) can
# start. The fullwidth brackets app.tools.untrusted also treats as "<" are escaped the same way.
_INPUT_BRACKETS: Final = str.maketrans({"<": "\\u003c", "\uff1c": "\\uff1c", "\ufe64": "\\ufe64"})


def input_content(input_json: str, redact: Callable[[str], str]) -> str:
    """What ``<agent_input>`` carries: the input JSON redacted, then with no bracket that could open a tag.
    ADR-011's limit is measured on it, since it is what the model is sent (code review of 073e68d)."""
    return redact(input_json).translate(_INPUT_BRACKETS)


def input_block(content: str) -> str:
    """The run's first message (``content`` from ``input_content``)."""
    return f"<agent_input>\n{content}\n</agent_input>"


def run_cap(registry_max_iterations: int, global_max: int) -> int:
    """Model calls a run may make (Keputusan Lead #19): the smaller of the agent's and the global cap."""
    return min(registry_max_iterations, global_max)


def attempt_timeout_s(max_tokens: int, *, cap_s: float, remaining_s: float) -> float:
    """Time one attempt may take (Keputusan Lead #19, syarat 3.1a): a base plus the output cap at the slowest
    rate still waited for, never past ``CLAUDE_REQUEST_TIMEOUT_SECONDS`` or the run's deadline."""
    return min(REQUEST_BASE_S + max_tokens / MIN_OUTPUT_TOKENS_PER_S, cap_s, remaining_s)


def _chain(error: BaseException) -> Iterator[BaseException]:
    """``error`` and every exception it was raised from or during (``__cause__`` and ``__context__``)."""
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)


def classify(error: BaseException, stop: StopReason | None) -> tuple[RunStatus, RunErrorCode | None]:
    """How a run that ended with ``error`` ends (Keputusan plan #8; Keputusan Lead #15, #20, #35).

    A ledger write that failed wins over everything, a cancel included (Keputusan Lead #20). A provider
    error caused by the run's own stop token takes the stop's reason. An asyncio cancel of the runtime from
    outside is ``worker_cancelled`` (``interrupted`` is recovery's); anything else unexpected is
    ``internal_error``."""
    if any(isinstance(e, UsageUnrecorded) for e in _chain(error)):
        return "failed", "usage_unrecorded"
    if isinstance(error, BudgetRefused):
        return "budget_exceeded", None
    if isinstance(error, ProviderBlocked):
        return "failed", error.code
    if isinstance(error, LeaseLostError):
        return "failed", "lease_lost"
    if isinstance(error, asyncio.CancelledError):
        return "failed", "worker_cancelled"
    if isinstance(error, LLMError):
        if stop == "cancelled":
            return "cancelled", None
        if stop is not None:
            return "failed", stop
        return "failed", "provider_auth" if error.kind == "auth" else "model_error"
    return "failed", "internal_error"


def end_text(code: RunErrorCode, error: BaseException | None = None, *, detail: str | None = None) -> str:
    """``agent_runs.error`` for ``code`` (``ERROR_TEXTS``): the sentence, and ``detail`` or the identifier
    ``error`` carries as a field, in brackets. Only ``internal_error`` names the exception's type."""
    sentence = ERROR_TEXTS[code]
    if code == "internal_error":
        return f"{type(error).__name__}: {sentence}" if error is not None else sentence
    if detail is None and error is not None:
        detail = _error_detail(error)
    return f"{sentence} ({detail})" if detail else sentence


def _error_detail(error: BaseException) -> str | None:
    unrecorded = next((e for e in _chain(error) if isinstance(e, UsageUnrecorded)), None)
    if unrecorded is not None:
        return f"attempt {unrecorded.attempt} of call {unrecorded.call_id}"
    if isinstance(error, ProviderBlocked):
        return error.reason
    if isinstance(error, LLMError):
        return error.kind
    return None


def _field_location(loc: Iterable[int | str]) -> str:
    parts = [str(p) if isinstance(p, int) or _FIELD.fullmatch(p) else "<key>" for p in loc]
    return ".".join(parts) or "(root)"


def _invalid_submit(contract: type[Contract], error: ValidationError) -> str:
    """Field locations and error types only, never a value the model sent (Global Constraints)."""
    problems = [
        f"{_field_location(e['loc'])}: {e['type']}"
        for e in error.errors(include_input=False, include_url=False, include_context=False)
    ]
    shown = "; ".join(problems[:VALIDATION_ERRORS_SHOWN])
    more = (
        f" (and {len(problems) - VALIDATION_ERRORS_SHOWN} more)"
        if len(problems) > VALIDATION_ERRORS_SHOWN
        else ""
    )
    return (
        f"submit_result input does not match the {contract.__name__} schema: {shown}{more}. Call "
        "submit_result again with input that matches its schema."
    )


# --- port exceptions: adapters translate domain errors into these (ADR-022) ---------------------------------


class ProviderBlocked(Exception):
    """The provider gate refused: ``PROVIDER_DISABLED`` (switched off, kill switch) or ``provider_not_ready``.
    ``reason`` is a short identifier (a readiness verdict reason, ``kill_switch``, ``project_missing``,
    ``no_provider``), never free text."""

    def __init__(self, code: Literal["PROVIDER_DISABLED", "provider_not_ready"], reason: str) -> None:
        super().__init__(f"{code} ({reason})")
        self.code: Literal["PROVIDER_DISABLED", "provider_not_ready"] = code
        self.reason = reason


class BudgetRefused(Exception):
    """The task budget refused the next attempt or tool (from ``BudgetExceeded``)."""

    def __init__(
        self,
        kind: BudgetKind,
        limit: int | Decimal,
        used: int | Decimal | None,
        *,
        next_bound: int | Decimal | None,
    ) -> None:
        super().__init__(f"budget exceeded: {kind}")
        self.kind: BudgetKind = kind
        self.limit = limit
        self.used = used
        self.next_bound = next_bound


class LeaseLostError(Exception):
    """The worker no longer holds the task lease: it must stop without sending anything."""


class UsageUnrecorded(Exception):
    """The ledger port failed to record an attempt: raised by the runtime, from the ledger's error. It carries
    the attempt's ids only."""

    def __init__(self, attempt: int, call_id: uuid.UUID | str) -> None:
        super().__init__(f"attempt {attempt} of call {call_id} was not recorded")
        self.attempt = attempt
        self.call_id = call_id


class AgentRunUnfinished(Exception):
    """The run's end is not known to the caller: it could not be written within ``close_timeout_s``, or a
    retried write found the row already finished (the first write committed, its ack was lost). The
    ``agent_runs`` row is the truth: ``running`` (recovery, WP-3.1f2b), the minimal ``failed internal_error``
    or the end already written. Raised from the cause; a result without its record is never returned."""


class RunAlreadyFinished(LookupError):
    """``RunStore.finish``: the run is not ``running`` any more; a run is finished once, never overwritten."""


class _DeadlineReached(Exception):
    """A port was still waiting when the run's deadline came: the run ends ``timeout``."""


# --- the call -----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunScope:
    project_id: uuid.UUID
    task_id: uuid.UUID  # required (Keputusan Lead #17): checked at run time, not only by type
    agent_run_id: uuid.UUID
    agent_id: str

    def __post_init__(self) -> None:
        _require_task(self.task_id)


def _require_task(task_id: object) -> None:
    if not isinstance(task_id, uuid.UUID):
        raise TypeError("a model call needs the UUID of its task")


def _block(block: ContentBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {
        "type": "tool_result",
        "tool_use_id": block.tool_use_id,
        "content": block.content,
        "is_error": block.is_error,
    }


def request_size_bytes(req: LLMRequest) -> int:
    """UTF-8 size of compact JSON of system, every message block and the tool definitions.

    The input token bound of a call (Keputusan Lead #17): a byte-level BPE token covers at least one byte, and
    the JSON overhead (keys, quotes, every tool schema) outweighs the tool prompt a provider adds.
    """
    payload = {
        "system": req.system,
        "messages": [
            {
                "role": message.role,
                "content": message.content
                if isinstance(message.content, str)
                else [_block(block) for block in message.content],
            }
            for message in req.messages
        ],
        "tools": [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            for tool in req.tools
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return len(text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ModelCall:
    """One logical model call of a run. The budget bound and the ledger context are both built from it
    (``app.services.agent_runtime.bind_model_call``), so the two never disagree (AG-13 3.1f1 L1)."""

    request: LLMRequest
    call_id: uuid.UUID
    scope: RunScope
    input_token_bound: int = field(init=False)

    def __post_init__(self) -> None:
        _require_task(self.scope.task_id)
        # retry.py logs a lost ledger row under req.metadata["call_id"]: it must name this call.
        if self.request.metadata.get("call_id") != str(self.call_id):
            raise ValueError("the request metadata must carry this call's call_id")
        object.__setattr__(self, "input_token_bound", request_size_bytes(self.request))

    @property
    def model(self) -> str:
        return self.request.model

    @property
    def output_token_cap(self) -> int:
        return self.request.max_tokens


# --- results ------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunUsage:
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    estimated_cost: Decimal | None
    cost_status: CostStatus


@dataclass(frozen=True, slots=True)
class BudgetBreach:
    kind: BudgetKind
    limit: int | Decimal
    used: int | Decimal | None
    next_bound: int | Decimal | None


@dataclass(frozen=True, slots=True)
class PendingTool:
    tool_use_id: str
    tool_name: str
    tool_call_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RunStart:
    task_id: uuid.UUID
    project_id: uuid.UUID
    agent_id: str
    agent_version: int
    step: str
    model_provider: str | None  # None when the run is refused before a provider exists
    model_name: str | None
    prompt_version: str
    input: Mapping[str, Any]  # agent_input.model_dump(mode="json"); the store redacts


@dataclass(frozen=True, slots=True)
class RunEnd:
    status: RunStatus
    iterations: int
    error_code: RunErrorCode | None = None
    error: str | None = None  # short text; the store redacts and cuts it
    output: Mapping[str, Any] | None = None  # succeeded: the output; approval_required: the pending tool
    budget: BudgetBreach | None = None
    # False when an attempt reached the provider without its ledger row being written by the close (review
    # H1): the store then reports the known sum as partial, or unknown, never a complete zero.
    usage_complete: bool = True

    def __post_init__(self) -> None:
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("a failed run has an error code, and only a failed run")
        if (self.status == "budget_exceeded") != (self.budget is not None):
            raise ValueError("a budget breach comes with budget_exceeded, and only with it")
        if self.output is not None and self.status not in ("succeeded", "approval_required"):
            raise ValueError("only a succeeded or approval_required run has an output")


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    agent_id: str
    task_id: uuid.UUID
    project_id: uuid.UUID
    agent_input: BaseModel
    cancel: CancelToken
    workspace: TaskWorkspace | None = None
    scenario: str | None = None  # scripted only (Keputusan Lead #27)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    agent_run_id: uuid.UUID
    status: RunStatus
    output: BaseModel | None
    usage: RunUsage
    error_code: RunErrorCode | None
    error: str | None
    budget: BudgetBreach | None
    pending_tool: PendingTool | None


# --- ports --------------------------------------------------------------------------------------------------


class ProviderGate(Protocol):
    async def check(self, project_id: uuid.UUID) -> Mapping[str, str]:
        """The effective model per tier, or ``ProviderBlocked``."""
        ...


class BudgetCheck(Protocol):
    async def before_model_attempt(self, call: ModelCall) -> None: ...
    async def before_tool(self, scope: RunScope) -> None: ...


class UsageLedgerPort(Protocol):
    def on_attempt(self, call: ModelCall) -> AttemptHandler: ...


class RunStore(Protocol):
    async def start(self, run: RunStart) -> uuid.UUID: ...
    async def finish(self, agent_run_id: uuid.UUID, end: RunEnd) -> RunUsage:
        """Raises ``RunAlreadyFinished`` for a row that is not ``running``."""
        ...

    async def fail(self, agent_run_id: uuid.UUID) -> None:
        """The minimal end when ``finish`` could not be written: ``failed internal_error``."""
        ...


class RunEvents(Protocol):
    async def delta(self, scope: RunScope, text: str, *, reset: bool = False) -> None: ...
    async def usage_updated(self, scope: RunScope) -> None: ...


class ToolExecutor(Protocol):
    def tool_defs_for(self, grant: AgentGrant) -> list[ToolDef]: ...
    async def execute(
        self, ctx: ToolContext, tool_name: str, raw_input: object, *, tool_use_id: str | None = None
    ) -> ToolResult: ...


class Heartbeat(Protocol):
    async def beat(self, task_id: uuid.UUID) -> None: ...


LogContext = Callable[[], Mapping[str, str | None]]


class ProviderFactory(Protocol):
    def open(self, log_context: LogContext) -> AbstractAsyncContextManager[LLMProvider]:
        """``log_context()`` gives the ids a log line of the run carries; it is read when the line is
        written, so a line after ``runs.start`` names the run too (ARCHITECTURE §6.1)."""
        ...


@dataclass(frozen=True, slots=True)
class RuntimePorts:
    gate: ProviderGate
    budget: BudgetCheck
    ledger: UsageLedgerPort
    runs: RunStore
    events: RunEvents
    tools: ToolExecutor
    heartbeat: Heartbeat
    providers: ProviderFactory


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    max_tool_iterations: int  # CLAUDE_MAX_TOOL_ITERATIONS
    max_output_tokens: int  # CLAUDE_MAX_OUTPUT_TOKENS
    request_timeout_s: float  # CLAUDE_REQUEST_TIMEOUT_SECONDS
    scripted: bool  # LLM_PROVIDER == "scripted"
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S
    beat_timeout_s: float | None = None  # one try of a beat; None: max(interval / 3, BEAT_TIMEOUT_MIN_S)
    close_timeout_s: float = CLOSE_TIMEOUT_S
    ledger_grace_s: float = LEDGER_GRACE_S
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)


# --- the runtime --------------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Tally:
    """Attempts that reached the provider, and attempts the ledger recorded."""

    sent: int = 0
    recorded: int = 0


class _RunStop:
    """One stop token per run (Keputusan plan #6): the caller's cancel, the deadline or a lost lease cancels
    it, and the first reason wins. Providers and tools get the token."""

    def __init__(self) -> None:
        self.token = CancelToken()
        self.reason: StopReason | None = None

    def stop(self, reason: StopReason) -> None:
        if self.reason is None:
            self.reason = reason
        self.token.cancel()


@dataclass(slots=True)
class _Run:
    request: AgentRunRequest
    spec: AgentSpec
    grant: AgentGrant
    prompt: Prompt
    runtime_id: str
    output_contract: type[Contract]
    input_content: str  # the <agent_input> content: redacted, escaped, within CONTEXT_MAX_BYTES
    deadline: float
    deltas: (
        DeltaRedactor  # the run's streamed text, one for all its model calls (a reset shows the run's text)
    )
    stop: _RunStop = field(default_factory=_RunStop)
    tally: _Tally = field(default_factory=_Tally)
    agent_run_id: uuid.UUID | None = None
    iterations: int = 0
    output: BaseModel | None = None
    pending: PendingTool | None = None


def _id(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _ctx(run: _Run) -> dict[str, str | None]:
    """What every runtime log line carries: ids only (review L5)."""
    run_id = _id(run.agent_run_id)
    return {
        "task_id": str(run.request.task_id),
        "agent_run_id": run_id,
        "correlation_id": f"agent_run:{run_id}" if run_id is not None else None,
    }


async def _stop(watchers: list[asyncio.Task[None]], run: _Run) -> None:
    """Cancel the watchers and wait for them; one that failed is logged, never discarded (review L1)."""
    for watcher in watchers:
        watcher.cancel()
    for outcome in await asyncio.gather(*watchers, return_exceptions=True):
        if isinstance(outcome, Exception):
            _log.error("agent_run_watcher_failed", **_ctx(run), error_type=type(outcome).__name__)


def _retrieve(write: asyncio.Future[Any]) -> None:
    """Done callback of a close write: its error is retrieved (the close loop logged it, or gave up and said
    the run is unfinished)."""
    if not write.cancelled():
        write.exception()


class AgentRuntime:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        prompts: PromptBook,
        ports: RuntimePorts,
        config: RuntimeConfig,
        redactor: Redactor,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._registry = registry
        self._prompts = prompts
        self._ports = ports
        self._config = config
        self._redactor = redactor
        self._clock = clock
        self._sleep = sleep

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        """One agent run (plan WP-3.1f2a Task 5). A caller error (unknown or disabled agent, tool mode, an
        absorbed profile, no prompt, the wrong input contract, an input over 60 KB, a scenario outside
        scripted) raises before any row exists. From then on every exit leaves a closed ``agent_runs`` row:
        a classified end is returned; an asyncio cancel or an unexpected error closes the run and is raised
        again."""
        run = self._admit(request)
        watchers = self._watch(run)
        try:
            end = await self._execute(run)
        except asyncio.CancelledError as cancelled:
            _log.warning("agent_run_worker_cancelled", **_ctx(run), agent_id=run.spec.agent_id)
            await asyncio.shield(self._wrap_up_after_error(watchers, run, self._end_for(cancelled, run)))
            raise
        except Exception as error:
            _log.error(
                "agent_run_internal_error",
                **_ctx(run),
                agent_id=run.spec.agent_id,
                error_type=type(error).__name__,
            )
            await asyncio.shield(self._wrap_up_after_error(watchers, run, self._end_for(error, run)))
            raise
        # Shielded with the watchers' stop (review L1): a cancel now cannot leave the run open.
        return await asyncio.shield(self._wrap_up(watchers, run, end))

    async def _wrap_up(self, watchers: list[asyncio.Task[None]], run: _Run, end: RunEnd) -> AgentRunResult:
        await _stop(watchers, run)
        return await self._close(run, end)

    async def _wrap_up_after_error(self, watchers: list[asyncio.Task[None]], run: _Run, end: RunEnd) -> None:
        """Closes the run before the original error is raised again; a close that fails is already logged
        and its row left to recovery (WP-3.1f2b)."""
        await _stop(watchers, run)
        try:
            await self._close(run, end)
        except AgentRunUnfinished:
            pass

    # --- before the run -------------------------------------------------------------------------------------

    def _admit(self, request: AgentRunRequest) -> _Run:
        """The checks that leave no row (Keputusan plan #10), and the input's ``task_id`` is the run's."""
        grant = grant_for(self._registry, request.agent_id)  # AgentNotRunnable: unknown or not enabled
        spec = self._registry.by_id(request.agent_id)
        assert spec is not None
        if spec.mvp_mode == "llm":
            runtime_id = spec.agent_id
        elif spec.mvp_mode == "profile" and spec.runtime_agent is not None:
            runtime_id = spec.runtime_agent
        else:
            raise AgentNotRunnable(f"{spec.agent_id} is not run by the agent runtime ({spec.mvp_mode})")
        if spec.agent_id in ABSORBED_PROFILES:
            raise AgentNotRunnable(f"{spec.agent_id} is absorbed into its runtime agent's run")
        output_contract = OUTPUT_CONTRACTS.get(runtime_id)
        input_contract = INPUT_CONTRACTS.get(runtime_id)
        if output_contract is None or input_contract is None or runtime_id not in STEPS:
            raise AgentNotRunnable(f"{spec.agent_id} has no runtime contract")
        prompt = self._prompts.get(spec.agent_id)
        if prompt is None:
            raise AgentNotRunnable(f"{spec.agent_id} has no runtime prompt")
        if type(request.agent_input) is not input_contract:
            raise AgentInputInvalid(f"{spec.agent_id} takes a {input_contract.__name__}")
        if getattr(request.agent_input, "task_id", None) != request.task_id:
            raise AgentInputInvalid("the input names another task than the run")
        content = input_content(request.agent_input.model_dump_json(), self._redactor.redact)
        if len(content.encode("utf-8")) > CONTEXT_MAX_BYTES:
            raise AgentInputTooLarge(f"the input sent is over {CONTEXT_MAX_BYTES} bytes (ADR-011)")
        if request.scenario is not None and not self._config.scripted:
            raise ScenarioNotAllowed("a scenario is only for the scripted provider (ADR-015)")
        return _Run(
            request=request,
            spec=spec,
            grant=grant,
            prompt=prompt,
            runtime_id=runtime_id,
            output_contract=output_contract,
            input_content=content,
            deltas=delta_redactor(self._redactor),
            deadline=self._clock() + spec.limits.timeout_seconds,
        )

    def _run_start(self, run: _Run, provider: str | None, model: str | None) -> RunStart:
        return RunStart(
            task_id=run.request.task_id,
            project_id=run.request.project_id,
            agent_id=run.spec.agent_id,
            agent_version=run.spec.version,
            step=STEPS[run.runtime_id],
            model_provider=provider,
            model_name=model,
            prompt_version=run.prompt.version,
            input=run.request.agent_input.model_dump(mode="json"),
        )

    # --- the stop token and its sources (Keputusan plan #5, #6; Keputusan Lead #33) -------------------------

    def _watch(self, run: _Run) -> list[asyncio.Task[None]]:
        return [
            asyncio.create_task(self._on_caller_cancel(run)),
            asyncio.create_task(self._on_deadline(run)),
            asyncio.create_task(self._keep_alive(run)),
        ]

    async def _on_caller_cancel(self, run: _Run) -> None:
        await run.request.cancel.wait()
        run.stop.stop("cancelled")

    async def _on_deadline(self, run: _Run) -> None:
        # Wall clock: an attempt in flight is cut at the deadline. Between attempts the loop also compares the
        # deadline with ``clock``.
        remaining = run.deadline - self._clock()
        if remaining > 0:
            await asyncio.sleep(remaining)
        run.stop.stop("timeout")

    async def _keep_alive(self, run: _Run) -> None:
        """The lease (60 s) is shorter than one request may take: renew it every ``heartbeat_interval_s``
        for as long as the run is active. A renewal that fails after its retries stops the run: without a
        lease nothing may go on."""
        while True:
            await asyncio.sleep(self._config.heartbeat_interval_s)
            try:
                await self._beat(run)
            except LeaseLostError:
                run.stop.stop("lease_lost")
                return
            except _DeadlineReached:
                return  # the deadline watcher stops the run

    # --- the run --------------------------------------------------------------------------------------------

    async def _execute(self, run: _Run) -> RunEnd:
        request = run.request
        try:
            await self._beat(run)
            models = await self._port(run, self._ports.gate.check(request.project_id))
            async with self._ports.providers.open(lambda: _ctx(run)) as provider:
                model = models[run.spec.model_tier]
                start = self._run_start(run, provider.name, model)
                run.agent_run_id = await self._port(run, self._ports.runs.start(start))
                return await self._loop(run, provider, model)
        except _DeadlineReached:
            return self._timed_out(run)
        except (ProviderBlocked, LeaseLostError, BudgetRefused, UsageUnrecorded, LLMError) as error:
            return self._end_for(error, run)

    # --- bounded ports (reviews of 42bcb2e: a held lock never stalls a run) ---------------------------------

    async def _port[T](self, run: _Run, awaitable: Awaitable[T], *, grace: float = 0.0) -> T:
        """``awaitable`` bounded by the run's remaining time (plus ``grace``): then, ``_DeadlineReached``. A
        ``TimeoutError`` the port raises itself (a database connect timeout, say) is the port's error and
        propagates as it is (review N2 of 073e68d)."""
        limit = run.deadline - self._clock() + grace
        if limit <= 0:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise _DeadlineReached
        bound = asyncio.timeout(limit)
        try:
            async with bound:
                return await awaitable
        except TimeoutError:
            if bound.expired():
                raise _DeadlineReached from None
            raise

    def _beat_timeout(self) -> float:
        configured = self._config.beat_timeout_s
        if configured is not None:
            return configured
        return max(self._config.heartbeat_interval_s / 3, BEAT_TIMEOUT_MIN_S)

    async def _beat(self, run: _Run) -> None:
        """Renew the lease: each try bounded, a failed try retried ``BEAT_RETRIES`` times. ``LeaseLostError``
        at once when the lease is gone, or when no try succeeded (a beat that cannot be confirmed is a lost
        lease); ``_DeadlineReached`` when the run's time ran out first."""
        failure: BaseException | None = None
        for _ in range(1 + BEAT_RETRIES):
            remaining = run.deadline - self._clock()
            if remaining <= 0:
                raise _DeadlineReached
            try:
                async with asyncio.timeout(min(self._beat_timeout(), remaining)):
                    await self._ports.heartbeat.beat(run.request.task_id)
                return
            except LeaseLostError:
                raise
            except Exception as error:  # a timeout or a database error: tried again
                if isinstance(error, TimeoutError) and run.deadline - self._clock() <= 0:
                    raise _DeadlineReached from None
                failure = error
                _log.warning("agent_run_beat_failed", **_ctx(run), error_type=type(error).__name__)
        raise LeaseLostError("the task lease could not be renewed") from failure

    async def _loop(self, run: _Run, provider: LLMProvider, model: str) -> RunEnd:
        assert run.agent_run_id is not None
        request = run.request
        scope = RunScope(request.project_id, request.task_id, run.agent_run_id, run.spec.agent_id)
        # What the gateway offers (grant ∩ registered ∩ preconditions, sorted; spec #22), then submit_result
        # last: a stable prompt prefix.
        defs = [d for d in self._ports.tools.tool_defs_for(run.grant) if d.name != SUBMIT_RESULT]
        tools = (*defs, ToolDef(SUBMIT_RESULT, SUBMIT_DESCRIPTION, run.output_contract.model_json_schema()))
        ctx = ToolContext(
            task_id=request.task_id,
            project_id=request.project_id,
            agent_run_id=run.agent_run_id,
            grant=run.grant,
            offered=frozenset(d.name for d in defs),
            correlation_id=f"agent_run:{run.agent_run_id}",
            cancel=run.stop.token,
            workspace=request.workspace,
        )
        messages = [Message("user", input_block(run.input_content))]
        cap = run_cap(run.spec.limits.max_iterations, self._config.max_tool_iterations)
        nudged = False
        while run.iterations < cap:
            if run.stop.reason is not None:
                return self._stopped(run)
            remaining = run.deadline - self._clock()
            if remaining <= 0:
                return self._timed_out(run)
            run.iterations += 1
            response = await self._attempt(run, provider, model, scope, tools, messages, remaining)
            if run.stop.reason is not None:  # review M1: a stop during the attempt wins over its answer
                return self._stopped(run)
            if response.content:
                messages.append(Message("assistant", response.content))
            uses = [block for block in response.content if isinstance(block, ToolUseBlock)]
            submits = [block for block in uses if block.name == SUBMIT_RESULT]
            submit_error = ONE_SUBMIT
            if len(submits) == 1:
                try:
                    output = run.output_contract.model_validate(submits[0].input)
                except ValidationError as error:
                    submit_error = _invalid_submit(run.output_contract, error)
                else:
                    await self._beat(run)  # review M1: no success without the lease, checked last
                    if run.stop.reason is not None:
                        return self._stopped(run)
                    run.output = output
                    return RunEnd("succeeded", run.iterations, output=output.model_dump(mode="json"))
            if not uses:
                if nudged:
                    return self._failed(run, "invalid_output")
                nudged = True
                messages.append(Message("user", NUDGE))
                continue
            results: list[ToolResultBlock] = []
            for block in uses:
                if block.name == SUBMIT_RESULT:
                    results.append(ToolResultBlock(block.id, submit_error, is_error=True))
                    continue
                if run.stop.reason is not None:
                    return self._stopped(run)
                # One tool at a time (syarat 3.1d2a #5): budget, then the lease, then the gateway.
                await self._port(run, self._ports.budget.before_tool(scope))
                await self._beat(run)
                result = await self._port(
                    run,
                    self._ports.tools.execute(ctx, block.name, block.input, tool_use_id=block.id),
                    grace=TOOL_STOP_GRACE_S,
                )
                if run.stop.reason is not None:  # review M1: after every tool too
                    return self._stopped(run)
                if result.status == "approval_required":
                    run.pending = PendingTool(block.id, block.name, result.tool_call_id)
                    pending = {
                        "tool_use_id": block.id,
                        "tool_name": block.name,
                        "tool_call_id": str(result.tool_call_id),
                    }
                    return RunEnd("approval_required", run.iterations, output={"pending_tool": pending})
                results.append(ToolResultBlock(block.id, result.content, is_error=result.is_error))
            messages.append(Message("user", tuple(results)))
        if run.stop.reason is not None:  # review M2: a stop during the last allowed step wins
            return self._stopped(run)
        return self._failed(run, "iteration_limit", detail=f"{cap} iterations")

    async def _attempt(
        self,
        run: _Run,
        provider: LLMProvider,
        model: str,
        scope: RunScope,
        tools: tuple[ToolDef, ...],
        messages: list[Message],
        remaining_s: float,
    ) -> LLMResponse:
        call_id = uuid.uuid4()
        metadata = {
            "call_id": str(call_id),
            "task_id": str(scope.task_id),
            "agent_run_id": str(scope.agent_run_id),
            "agent_id": scope.agent_id,
            "output_contract": run.output_contract.__name__,
        }
        if run.request.scenario is not None and provider.name == "scripted":
            metadata["scenario"] = run.request.scenario
        max_tokens = self._config.max_output_tokens
        call = ModelCall(
            request=LLMRequest(
                model=model,
                system=run.prompt.text,
                messages=tuple(messages),
                tools=tools,
                max_tokens=max_tokens,
                metadata=metadata,
                timeout_s=attempt_timeout_s(
                    max_tokens, cap_s=self._config.request_timeout_s, remaining_s=remaining_s
                ),
            ),
            call_id=call_id,
            scope=scope,
        )
        deltas = run.deltas
        sent = run.tally.sent

        async def on_text_delta(text: str) -> None:
            await self._delta(run, scope, deltas.feed(text))

        async def on_restart(attempt: int) -> None:
            # M4: the failed attempt's text, held or sent, is void; the client goes back to the calls before.
            await self._delta(run, scope, deltas.restart())

        try:
            response = await self._call_model(
                call,
                provider,
                cancel=run.stop.token,
                on_text_delta=on_text_delta,
                on_restart=on_restart,
                run=run,
            )
        except Exception:
            await self._usage_changed(run, scope, sent)
            raise
        await self._delta(run, scope, deltas.flush())
        await self._usage_changed(run, scope, sent)
        return response

    async def _delta(self, run: _Run, scope: RunScope, delta: Delta | None) -> None:
        if delta is not None:
            await self._event(
                run, "agent.delta", self._ports.events.delta(scope, delta.text, reset=delta.reset)
            )

    async def _usage_changed(self, run: _Run, scope: RunScope, sent_before: int) -> None:
        """``usage.updated`` only when an attempt of this call reached the provider (review L3)."""
        if run.tally.sent > sent_before:
            await self._event(run, "usage.updated", self._ports.events.usage_updated(scope))

    async def _event(self, run: _Run, event_type: str, awaitable: Awaitable[None]) -> None:
        """Events are best effort (review L3): the ledger is the truth; a failed event is logged (ids and
        type) and the run goes on."""
        try:
            await self._port(run, awaitable)
        except Exception as error:
            _log.warning(
                "agent_run_event_failed", **_ctx(run), event_type=event_type, error_type=type(error).__name__
            )

    # --- how it ends ----------------------------------------------------------------------------------------

    def _stopped(self, run: _Run) -> RunEnd:
        if run.stop.reason == "cancelled":
            return RunEnd("cancelled", run.iterations)
        if run.stop.reason == "timeout":
            return self._timed_out(run)
        return self._failed(run, "lease_lost")

    def _timed_out(self, run: _Run) -> RunEnd:
        return self._failed(run, "timeout", detail=f"{run.spec.limits.timeout_seconds} s")

    def _failed(
        self, run: _Run, code: RunErrorCode, error: BaseException | None = None, *, detail: str | None = None
    ) -> RunEnd:
        text = self._redactor.redact(end_text(code, error, detail=detail))[:ERROR_TEXT_MAX]
        return RunEnd("failed", run.iterations, code, text)

    def _end_for(self, error: BaseException, run: _Run) -> RunEnd:
        status, code = classify(error, run.stop.reason)
        if status == "budget_exceeded" and isinstance(error, BudgetRefused):
            breach = BudgetBreach(error.kind, error.limit, error.used, error.next_bound)
            return RunEnd("budget_exceeded", run.iterations, budget=breach)
        if status == "cancelled" or code is None:
            return RunEnd("cancelled", run.iterations)
        if code == "timeout":
            return self._timed_out(run)
        if code == run.stop.reason:  # the run's own stop ended the call: the stop, not the call, is the cause
            return self._failed(run, code)
        return self._failed(run, code, error)  # the error's detail is logged by type only

    async def _close(self, run: _Run, end: RunEnd) -> AgentRunResult:
        """Writes the end and returns the result; ``AgentRunUnfinished`` when it could not be written."""
        if run.tally.sent != run.tally.recorded:
            end = replace(end, usage_complete=False)
        usage = await self._record_end(run, end)
        assert run.agent_run_id is not None
        return AgentRunResult(
            agent_run_id=run.agent_run_id,
            status=end.status,
            output=run.output if end.status == "succeeded" else None,
            usage=usage,
            error_code=end.error_code,
            error=end.error,
            budget=end.budget,
            pending_tool=run.pending if end.status == "approval_required" else None,
        )

    async def _record_end(self, run: _Run, end: RunEnd) -> RunUsage:
        """The end, written shielded and bounded by ``close_timeout_s``, with one retry (review M5): a write
        still running is waited for once more, a write that failed is tried again. Then, if the row exists,
        a minimal ``failed internal_error``, and ``AgentRunUnfinished``. Every failure is logged with the
        run's ids."""
        failure: BaseException | None = None
        write = self._write(run, end)
        for attempt in (1, 2):
            try:
                return await asyncio.wait_for(asyncio.shield(write), self._config.close_timeout_s)
            except RunAlreadyFinished as error:
                # The row is terminal: an earlier write committed and its ack was lost (review N4). Nothing is
                # written over it; the caller still does not know the end.
                _log.warning("agent_run_already_finished", **_ctx(run), attempt=attempt)
                raise AgentRunUnfinished(
                    f"the end of agent run {_id(run.agent_run_id)} was already recorded"
                ) from error
            except Exception as error:  # a timeout or the store's error
                failure = error
                _log.error(
                    "agent_run_unfinished", **_ctx(run), attempt=attempt, error_type=type(error).__name__
                )
                if attempt == 1 and write.done():  # one retry; nothing is started after the last attempt
                    write = self._write(run, end)
        if run.agent_run_id is not None:
            mark = asyncio.ensure_future(self._ports.runs.fail(run.agent_run_id))
            mark.add_done_callback(_retrieve)
            try:
                await asyncio.wait_for(asyncio.shield(mark), self._config.close_timeout_s)
            except Exception as error:
                _log.error("agent_run_unmarked", **_ctx(run), error_type=type(error).__name__)
        raise AgentRunUnfinished(
            f"the end of agent run {_id(run.agent_run_id)} was not recorded"
        ) from failure

    def _write(self, run: _Run, end: RunEnd) -> asyncio.Future[RunUsage]:
        write = asyncio.ensure_future(self._write_end(run, end))
        write.add_done_callback(_retrieve)
        return write

    async def _write_end(self, run: _Run, end: RunEnd) -> RunUsage:
        if run.agent_run_id is None:  # refused before the provider existed: the row names no model
            run.agent_run_id = await self._ports.runs.start(self._run_start(run, None, None))
        return await self._ports.runs.finish(run.agent_run_id, end)

    async def _call_model(
        self,
        call: ModelCall,
        provider: LLMProvider,
        *,
        cancel: CancelToken,
        on_text_delta: TextDeltaHandler | None = None,
        on_restart: RestartHandler | None = None,
        run: _Run | None = None,
    ) -> LLMResponse:
        """The only create_with_retry in app/ (Keputusan Lead #17; tests/unit/test_model_call_guard.py).

        ``before_attempt`` = heartbeat -> provider gate -> budget (Keputusan plan #4): without the lease
        nothing may be touched, the gate is cheap policy, the budget reads the ledger. Any of them raising
        stops the call with nothing sent and nothing recorded. ``on_attempt`` = the ledger; its failure
        becomes ``UsageUnrecorded`` from it, so a paid answer that was not recorded never comes back
        (Keputusan Lead #8, #20). The run loop passes its stop token as ``cancel`` and the delta buffer as
        ``on_text_delta``/``on_restart``."""
        ports = self._ports
        scope = call.scope

        async def before_attempt(attempt: int) -> None:
            if run is None:  # tests of the call path alone: no run, no deadline
                await ports.heartbeat.beat(scope.task_id)
                await ports.gate.check(scope.project_id)
                await ports.budget.before_model_attempt(call)
                return
            await self._beat(run)
            await self._port(run, ports.gate.check(scope.project_id))
            await self._port(run, ports.budget.before_model_attempt(call))
            run.tally.sent += 1  # the hooks passed: the request goes out, unless its restart stops it

        async def restart(attempt: int) -> None:
            # Between before_attempt and the request only the restart runs (retry.py): an attempt it stops (a
            # cancel during the reset event) never went out, so it is not counted as sent (review N3).
            assert on_restart is not None
            try:
                await on_restart(attempt)
            except BaseException:
                if run is not None:
                    run.tally.sent -= 1
                raise

        record = ports.ledger.on_attempt(call)

        async def on_attempt(attempt: AttemptRecord) -> None:
            # Bounded (review N1 of 073e68d), also on retry.py's shielded cancel path, which awaits this.
            limit = (
                None if run is None else max(run.deadline - self._clock(), 0) + self._config.ledger_grace_s
            )
            try:
                async with asyncio.timeout(limit):
                    await record(attempt)
            except Exception as error:  # the bound or the ledger's own error: the attempt is unrecorded
                raise UsageUnrecorded(attempt.attempt, call.call_id) from error
            if run is not None:
                run.tally.recorded += 1

        return await create_with_retry(
            provider,
            call.request,
            policy=self._config.retry_policy,
            on_text_delta=on_text_delta,
            on_restart=restart if on_restart is not None else None,
            cancel=cancel,
            on_attempt=on_attempt,
            before_attempt=before_attempt,
            sleep=self._sleep,
        )
