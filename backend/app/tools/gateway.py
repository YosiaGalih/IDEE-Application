"""ToolGateway: the one door for an agent's runtime actions (CLAUDE.md #2; ARCHITECTURE §6.1; SECURITY_MODEL
§3, §9, §10; spec WP-3.1d Goal 3).

``execute`` checks, in order: name and allowlist (offered to the model **and** granted by the registry,
registered, not the terminal ``submit_result``) → permission preconditions → input schema → scope and write
paths (task of this project, not finished; project active) → per-tool quota → risk and approval (R3 needs an
approval for exactly this input, consumed once) → claim → handler (timeout, cancel) → redaction then
truncation → record. Every evaluated call leaves a ``tool_calls`` row (a replay of the same tool use answers
with the original call's row); every refusal is audited ``tool.denied``; every R1+ call that reaches its
handler is audited ``tool.executed``; the task timeline gets ``tool.call_started`` and
``tool.call_finished``.

The gateway uses its own sessions: the ``running`` claim is committed before the handler starts, so a crash
leaves evidence, and a side-effect tool is never run twice for one ``tool_use_id`` (Keputusan Lead 2, 5).
The policy transaction locks the task row first (``FOR NO KEY UPDATE``), then any approval, the order the
``tool_r3`` handler takes too.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter, json_safe
from app.core.errors import AppError
from app.core.events import EventBus, emit_task_event, publish_pending
from app.core.logging import get_logger
from app.llm.base import ToolDef, is_canonical_tool_name
from app.models.tools import SUMMARY_MAX, ToolCall
from app.tools.base import (
    AgentGrant,
    R3InputNotCanonical,
    RiskLevel,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolPorts,
    ToolResult,
    ToolSpec,
    ToolStatus,
    Untrusted,
)
from app.tools.catalog import TOOL_CATALOG, ToolInfo
from app.tools.policy import is_r4, unmet_preconditions, write_allowed
from app.tools.scope import TaskScope, lock_task_scope
from app.tools.untrusted import CLOSE_TAG, CUT_MARKER, neutralise, open_tag, strip_controls

logger = get_logger(__name__)

TERMINAL_TOOL = "submit_result"
MAX_RESULT_BYTES = 64 * 1024  # one tool_result to the model (TOOL_CATALOG, SECURITY_MODEL §3; Keputusan 6)
# Only this much of a handler's output is redacted (then cut to MAX_RESULT_BYTES): the redaction cost stays
# bounded whatever a handler returns, and a secret that reaches the final window lies wholly inside this one.
REDACT_WINDOW_CHARS = 4 * MAX_RESULT_BYTES
# When the window cut the output, this much of the redacted window's end is dropped too: a secret cut in two
# at the window's edge is not recognised, and heavy redaction can bring that edge inside the final 64 KB.
WINDOW_EDGE_CHARS = 4096
# A model's input deeper than this is not recorded or validated (it is an invalid input): every recursive
# step after it (redaction, hashing, validation) stays bounded.
MAX_INPUT_DEPTH = 32
# How long a cancelled or timed-out handler may take to stop before the gateway records and moves on (a
# handler in a thread, or one that ignores cancellation, keeps running unobserved; its outcome is unknown).
STOP_GRACE_S = 5.0
INVALID_NAME = "<invalid>"
# The finished task statuses (app.workflow.states.FINISHED; tools may not import workflow, a test keeps
# the two equal).
FINISHED_TASK_STATUSES = frozenset({"completed", "failed", "cancelled", "rejected"})
# Rows that reached a handler count against a quota.
_EXECUTED = ("running", "ok", "error", "timeout", "cancelled")
# ...except calls that ended before any handler: an invalid input, or a run cancelled before the tool started.
_NOT_STARTED = ("invalid_input", "not_started")
# Codes only the gateway writes. A handler's error code that collides is prefixed, so a builtin can never
# take its call out of the quota or pass for an unrecorded outcome (AG-13 3.1d1 delta C2).
_GATEWAY_CODES = frozenset({*_NOT_STARTED, "outcome_unknown", "record_failed"})


def _handler_code(code: str) -> str:
    return (f"handler_{code}" if code in _GATEWAY_CODES else code)[:40]


_TOOL_USE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_KEY_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
_INPUT_STRING_MAX = 1024
_INPUT_ITEMS_MAX = 50
_INPUT_DEPTH_MAX = 6
_EVENT_SUMMARY_MAX = 200

# The text the model receives for each refusal: fixed strings, never an echo of its input.
DENIALS: Mapping[str, str] = {
    "invalid_name": "denied: the tool name is not a valid tool name",
    "terminal_tool": "denied: submit_result ends the run and is not executed as a tool",
    "not_offered": "denied: this tool was not offered in this request",
    "not_allowed": "denied: this agent may not use this tool",
    "not_registered": "denied: this tool is not available",
    "permission": "denied: this agent's permission profile does not allow this tool",
    "invalid_tool_use_id": "denied: a tool use id is required and must be a short identifier",
    "path_not_allowed": "denied: this agent may not write to that path",
    "scope_mismatch": "denied: the task does not belong to this project",
    "task_finished": "denied: the task has already finished",
    "project_not_active": "denied: the project is not active",
    "quota_exceeded": "denied: the quota for this tool in this run is used up",
    "input_not_canonical": "denied: an R3 action input must be plain JSON without decimals",
    "tool_use_id_reused": "denied: this tool use id already belongs to another call",
    "r3_needs_agent_run": "denied: an R3 action can only be requested from an agent run",
    "secret_in_input": "denied: an R3 action input must not contain secrets",
    # Refused by the handler (HANDLER_DENIALS).
    "path_denied": "denied: agents may not read or write this path",
    "delete_not_allowed": "denied: deleting a file needs filesystem scoped_write, as code.delete_file does",
    "not_task_branch": "denied: commits go to the task branch only",
    "hidden_paths_affected": (
        "denied: the change would also change paths agents may not read; nothing was committed"
    ),
}
# Policy refusals only a handler can make, since it sees the worktree (AG-13 3.1d2b LOW-2; spec WP-3.1f
# Keputusan Lead #31): they end ``denied`` with an audit ``tool.denied``, like the gateway's own refusals.
HANDLER_DENIALS: frozenset[str] = frozenset(
    {"path_denied", "delete_not_allowed", "not_task_branch", "hidden_paths_affected"}
)
UNKNOWN_OUTCOME = (
    "error: this tool use was started before; it is still running or was interrupted, so its outcome is "
    "unknown. It is not run again."
)
# A replayed call reports the status its original row ended with.
_REPLAYED: Mapping[str, ToolStatus] = {s: s for s in ("ok", "error", "timeout", "cancelled", "denied")}


class TaskMissing(LookupError):
    """The context names a task that does not exist (purged with its project): nothing can be recorded."""


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: ToolStatus
    content: str
    error_code: str | None = None
    untrusted: Untrusted | None = None


def _canonical_name(tool_name: object) -> str | None:
    return tool_name if isinstance(tool_name, str) and is_canonical_tool_name(tool_name) else None


def _input_hash(value: object) -> str:
    """Over the normalised input (plain JSON, clean text): stable, and it never fails to encode."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _clean_text(text: str) -> str:
    """Text PostgreSQL and the provider accept: NUL becomes U+FFFD and a lone surrogate becomes "?". One such
    character from a model or a file must not make a call's input or outcome unrecordable."""
    text = text.replace("\x00", "\ufffd")
    try:
        text.encode()
    except UnicodeEncodeError:
        text = text.encode("utf-8", "replace").decode()
    return text


def _unknown_outcome(spec: ToolSpec) -> str:
    """Said whenever a side-effect handler was stopped: it may have acted before it stopped."""
    return " The outcome is unknown: the action may have been applied." if spec.side_effect else ""


class _TooDeep(Exception):
    pass


def _normalized(value: Any, depth: int = 0) -> Any:
    """The model's input as plain JSON with clean text, at most ``MAX_INPUT_DEPTH`` levels deep."""
    if depth > MAX_INPUT_DEPTH:
        raise _TooDeep
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict):
        items = value.items()  # pyright: ignore[reportUnknownVariableType]
        return {_clean_text(str(k)): _normalized(v, depth + 1) for k, v in items}  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(value, list | tuple):
        return [_normalized(v, depth + 1) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if value is None or isinstance(value, bool | int | float):
        return value
    return _clean_text(repr(value))


def _bounded(value: Any, depth: int = 0) -> Any:
    """Long strings, long lists and deep nesting cut: ``sanitized_input`` keeps what identifies a call."""
    if depth >= _INPUT_DEPTH_MAX:
        return "<nested>"
    if isinstance(value, str):
        value = _clean_text(value)
        return (
            value if len(value) <= _INPUT_STRING_MAX else f"{value[:_INPUT_STRING_MAX]}…[{len(value)} chars]"
        )
    if isinstance(value, dict):
        items = list(value.items())[:_INPUT_ITEMS_MAX]  # pyright: ignore[reportUnknownArgumentType]
        return {_clean_text(str(k)): _bounded(v, depth + 1) for k, v in items}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [_bounded(v, depth + 1) for v in value[:_INPUT_ITEMS_MAX]]  # pyright: ignore[reportUnknownVariableType]
    return value


def _truncate(text: str, limit: int, total_bytes: int | None = None) -> str:
    """At most ``limit`` UTF-8 bytes, cut on a character boundary, with a marker saying how much was cut.
    ``total_bytes``: the size before an earlier cut, reported in the marker."""
    data = text.encode()
    total = max(len(data), total_bytes or 0)
    if total <= limit:
        return text
    marker = f"\n[truncated: {total} bytes, showing the first {{}}]"
    keep = limit - len(marker.format(limit)) - 8
    head = data[:keep].decode(errors="ignore")
    return head + marker.format(len(head.encode()))


def _where(loc: tuple[int | str, ...]) -> str:
    # Field names the model invented are its own text, but they are recorded too: only identifier-like
    # names are echoed (as in the registry loader).
    return ".".join(str(p) if isinstance(p, int) or _KEY_PART.fullmatch(p) else "<key>" for p in loc) or "$"


def _invalid_input(error: ValidationError) -> str:
    problems = "; ".join(f"{_where(e['loc'])}: {e['type']}" for e in error.errors()[:10])
    return f"invalid input: {problems}"


def _summary(content: str, untrusted: Untrusted | None) -> str:
    """What the tool_calls row keeps (and a replay returns): a wrapped output cut to SUMMARY_MAX keeps its
    closing tag (AG-13 WP-3.1d2a LOW-3)."""
    if untrusted is None or len(content) <= SUMMARY_MAX:
        return content[:SUMMARY_MAX]
    return content[: SUMMARY_MAX - len(CUT_MARKER) - len(CLOSE_TAG)] + CUT_MARKER + CLOSE_TAG


def _content_text(output: ToolOutput) -> str:
    if isinstance(output.content, str):
        return output.content
    # A JSON object for the model; anything not JSON is a handler bug and fails visibly.
    return json.dumps(dict(output.content), ensure_ascii=False, sort_keys=True)


class ToolGateway:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        audit: AuditWriter,
        ports: ToolPorts,
        catalog: Mapping[str, ToolInfo] = TOOL_CATALOG,
        bus: EventBus | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions = sessions
        self._audit = audit
        self._ports = ports
        self._catalog = catalog
        self._bus = bus
        self._clock = clock
        self._specs: dict[str, ToolSpec] = {}

    # --- registration -------------------------------------------------------------------------------------

    def register(self, spec: ToolSpec) -> None:
        """Add a builtin. Refused: an invalid or R4 name, ``submit_result``, a tool the catalog does not list
        as ``mvp`` with this risk, an input model that accepts unknown fields, a write tool that does not
        say which paths it writes, or a second registration of the same name."""
        name, info = spec.name, self._catalog.get(spec.name)
        problems: list[str] = []
        if not is_canonical_tool_name(name):
            problems.append("the name is not a canonical tool name")
        if name == TERMINAL_TOOL:
            problems.append("submit_result belongs to the agent runtime")
        if is_r4(name) or spec.risk >= RiskLevel.R4:
            problems.append("R4 tools are never registered")
        if info is None:
            problems.append("the tool is not in the catalog")
        else:
            if info.risk == "-" or RiskLevel.of(info.risk) != spec.risk:
                problems.append("the risk differs from the catalog")
            if info.status != "mvp":
                problems.append(f"the catalog lists the tool as {info.status}")
            writes = {"fs_write", "fs_scoped_write"} & set(info.preconditions)
            if writes and spec.write_paths is None:
                problems.append("a write tool must declare write_paths")
        if spec.input_model.model_config.get("extra") != "forbid":
            problems.append('the input model must use extra="forbid"')
        if not spec.timeout_s > 0:
            problems.append("timeout_s must be positive")
        if spec.risk is RiskLevel.R3 and any(f.exclude for f in spec.input_model.model_fields.values()):
            # model_dump would leave the field out of the approval's hash: the person would approve less
            # than what runs (AG-13 INFO).
            problems.append("an R3 input model must not exclude fields")
        if spec.risk >= RiskLevel.R1 and not spec.side_effect:
            # Writes, sandbox runs and R3 actions change something: without side_effect they would skip the
            # idempotency key and could run twice for one tool use (silent-failure L3).
            problems.append("an R1+ tool must be a side_effect tool")
        if name in self._specs:
            problems.append("the tool is already registered")
        if problems:
            raise ValueError(f"cannot register {name!r}: " + "; ".join(problems))
        self._specs[name] = spec

    @property
    def registered(self) -> frozenset[str]:
        return frozenset(self._specs)

    def tool_defs_for(self, grant: AgentGrant) -> list[ToolDef]:
        """What to offer the model: the agent's allowlist ∩ registered tools whose preconditions its
        permissions meet, sorted by name (a stable prompt prefix)."""
        defs: list[ToolDef] = []
        for name in sorted(grant.tools):
            spec = self._specs.get(name)
            if spec is None or unmet_preconditions(self._catalog[name], grant.permissions, grant.tools):
                continue
            defs.append(ToolDef(name, spec.description, spec.input_model.model_json_schema()))
        return defs

    # --- execution ----------------------------------------------------------------------------------------

    async def execute(
        self, ctx: ToolContext, tool_name: str, raw_input: object, *, tool_use_id: str | None = None
    ) -> ToolResult:
        started = self._clock()
        name = _canonical_name(tool_name)
        spec = self._specs.get(name) if name is not None else None
        info = self._catalog.get(name) if name is not None else None
        risk = spec.risk if spec else (RiskLevel.of(info.risk) if info and info.risk != "-" else RiskLevel.R4)
        use_id = tool_use_id if tool_use_id is not None and _TOOL_USE_ID.fullmatch(tool_use_id) else None
        try:
            clean, too_deep = _normalized(raw_input), False
        except _TooDeep:
            clean, too_deep = {"_unrecorded": f"input nested deeper than {MAX_INPUT_DEPTH} levels"}, True
        row = ToolCall(
            task_id=ctx.task_id,
            agent_run_id=ctx.agent_run_id,
            agent_id=ctx.grant.agent_id,
            tool_name=name or INVALID_NAME,
            tool_use_id=use_id,
            risk_level=int(risk),
            input_hash=_input_hash(clean),
            sanitized_input=json_safe(_bounded(self._audit.redactor.redact_obj(clean))),
        )

        refusal, detail = self._refusal(ctx, name, spec, info)
        validated: BaseModel | None = None
        invalid: str | None = None
        if refusal is None and spec is not None and too_deep:
            invalid = f"invalid input: nested deeper than {MAX_INPUT_DEPTH} levels"
        elif refusal is None and spec is not None:
            try:
                validated = spec.input_model.model_validate(clean)
            except ValidationError as exc:
                invalid = _invalid_input(exc)
        if refusal is None and validated is not None and spec is not None:
            if spec.side_effect and use_id is None:
                refusal = "invalid_tool_use_id"
            else:
                refusal, invalid = self._path_refusal(ctx, spec, validated)

        claimed = False
        try:
            async with self._sessions() as session:
                result = await self._decide(
                    session, ctx, row, spec, validated, refusal, detail, invalid, started
                )
                if result is not None:
                    return result
                assert spec is not None
                row.status = "running"
                session.add(row)
                await session.flush()
                await self._event(session, ctx, row, "tool.call_started", None)
                await session.commit()
                claimed = True
                await self._publish(session)
            outcome = await self._run(ctx, spec, validated)
        except asyncio.CancelledError:
            if claimed:
                assert spec is not None
                await self._settle_cancelled(ctx, row, spec)
            raise
        return await asyncio.shield(self._finish(ctx, row, spec, outcome, started))

    async def _decide(
        self,
        session: AsyncSession,
        ctx: ToolContext,
        row: ToolCall,
        spec: ToolSpec | None,
        validated: BaseModel | None,
        refusal: str | None,
        detail: list[str],
        invalid: str | None,
        started: float,
    ) -> ToolResult | None:
        """The policy transaction, after the checks that need no database. A result: the call ends here
        (refused, invalid, replayed, cancelled before it started, or waiting for an approval). ``None``: claim
        the row and run the handler."""
        scope = await self._lock_task(session, ctx)
        if refusal is None and invalid is None:
            refusal = await self._scope_refusal(ctx, scope)
        if refusal is None and invalid is None and spec is not None and spec.side_effect:
            assert row.tool_use_id is not None  # checked before: a side effect needs a tool use id
            # Before quota and approval: the same tool use again returns what happened, it is not a new action
            # (an R3 replay must not ask for a new approval).
            row.idempotency_key = self._idempotency_key(ctx, row.tool_use_id)
            previous = await session.scalar(
                select(ToolCall).where(ToolCall.idempotency_key == row.idempotency_key)
            )
            if previous is not None and (
                previous.tool_name != row.tool_name or previous.input_hash != row.input_hash
            ):
                refusal = "tool_use_id_reused"  # never answer one call with another call's result
            elif previous is not None:
                return await self._replay(session, ctx, previous)
        if refusal is None and invalid is None and spec is not None:
            refusal = await self._quota_refusal(session, ctx, spec)
        if refusal is not None:
            return await self._deny(session, ctx, row, refusal, detail, started)
        if invalid is not None or validated is None or spec is None:
            outcome = _Outcome("error", invalid or "invalid input", "invalid_input")
            return await self._finish_now(session, ctx, row, outcome, started)
        if ctx.cancel.cancelled:
            # Before any approval is consumed and before the handler could start (S1/M5).
            outcome = _Outcome(
                "cancelled", "cancelled: the run was cancelled before the tool started", "not_started"
            )
            return await self._finish_now(session, ctx, row, outcome, started)
        if spec.risk is RiskLevel.R3:
            return await self._authorize_r3(session, ctx, row, spec, validated, started)
        return None

    def _refusal(
        self, ctx: ToolContext, name: str | None, spec: ToolSpec | None, info: ToolInfo | None
    ) -> tuple[str | None, list[str]]:
        """Step 1 and 2: the name, the allowlist, the permission profile. A name from the model is matched
        against what was offered and what the registry grants, never trusted (3.1a AG-13 I2/T1)."""
        if name is None:
            return "invalid_name", []
        if name == TERMINAL_TOOL:
            return "terminal_tool", []
        if name not in ctx.offered:
            return "not_offered", []
        if name not in ctx.grant.tools:
            return "not_allowed", []
        if spec is None or info is None:
            return "not_registered", []
        unmet = unmet_preconditions(info, ctx.grant.permissions, ctx.grant.tools)
        return ("permission", unmet) if unmet else (None, [])

    def _path_refusal(
        self, ctx: ToolContext, spec: ToolSpec, validated: BaseModel
    ) -> tuple[str | None, str | None]:
        if spec.write_paths is None:
            return None, None
        try:
            paths = list(spec.write_paths(validated))
        except (ValueError, ToolError) as exc:
            message = exc.message if isinstance(exc, ToolError) else "the paths to write cannot be read"
            return None, f"invalid input: {message}"
        filesystem = ctx.grant.permissions.filesystem
        if not paths or not all(write_allowed(filesystem, path) for path in paths):
            return "path_not_allowed", None
        return None, None

    async def _lock_task(self, session: AsyncSession, ctx: ToolContext) -> TaskScope:
        scope = await lock_task_scope(session, ctx.task_id)
        if scope is None:
            logger.warning("tool_call_without_task", task_id=str(ctx.task_id), agent_id=ctx.grant.agent_id)
            raise TaskMissing(f"task {ctx.task_id} does not exist")
        return scope

    @staticmethod
    async def _scope_refusal(ctx: ToolContext, scope: TaskScope) -> str | None:
        if scope.project_id != ctx.project_id:
            return "scope_mismatch"
        if scope.task_status in FINISHED_TASK_STATUSES:
            return "task_finished"
        return None if scope.project_status == "active" else "project_not_active"

    async def _quota_refusal(self, session: AsyncSession, ctx: ToolContext, spec: ToolSpec) -> str | None:
        quota = ctx.grant.tool_quota.get(spec.name)
        if quota is None:
            return None
        scope = (
            ToolCall.agent_run_id == ctx.agent_run_id
            if ctx.agent_run_id is not None
            else ToolCall.task_id == ctx.task_id
        )
        used = await session.scalar(
            select(func.count())
            .select_from(ToolCall)
            .where(
                scope,
                ToolCall.tool_name == spec.name,
                ToolCall.status.in_(_EXECUTED),
                or_(ToolCall.error_code.is_(None), ToolCall.error_code.not_in(_NOT_STARTED)),
            )
        )
        return "quota_exceeded" if (used or 0) >= quota else None

    async def _authorize_r3(
        self,
        session: AsyncSession,
        ctx: ToolContext,
        row: ToolCall,
        spec: ToolSpec,
        validated: BaseModel,
        started: float,
    ) -> ToolResult | None:
        """``None``: an approval for exactly this input was consumed, run the tool. Otherwise the call ends
        here as ``approval_required``, or denied: an approval binds one agent run (the payload names the run,
        so without one another agent of the task could use it), and its payload is stored and shown as is (it
        cannot be redacted without breaking the hash), so an input carrying a secret is refused (AG-13)."""
        if ctx.agent_run_id is None:
            return await self._deny(session, ctx, row, "r3_needs_agent_run", [], started)
        action_input = validated.model_dump(mode="json")
        if self._audit.redactor.redact_obj(action_input) != action_input:
            return await self._deny(session, ctx, row, "secret_in_input", [], started)
        payload = {
            "task_id": str(ctx.task_id),
            "agent_run_id": str(ctx.agent_run_id),
            "tool": spec.name,
            "input": action_input,
        }
        try:
            ticket = await self._ports.approvals.authorize(
                session,
                project_id=ctx.project_id,
                task_id=ctx.task_id,
                agent_id=ctx.grant.agent_id,
                payload=payload,
                summary=f"Agent {ctx.grant.agent_id} asks to run {spec.name} (R3)",
            )
        except R3InputNotCanonical:  # floats and other values a canonical hash refuses
            return await self._deny(session, ctx, row, "input_not_canonical", [], started)
        row.approval_id = ticket.approval_id
        if ticket.consumed:
            return None
        message = (
            f"approval required: approval {ticket.approval_id} waits for a person's decision; "
            "the task stops here until it is decided"
        )
        return await self._finish_now(session, ctx, row, _Outcome("approval_required", message), started)

    def _idempotency_key(self, ctx: ToolContext, use_id: str) -> str:
        run = str(ctx.agent_run_id) if ctx.agent_run_id is not None else f"task:{ctx.task_id}"
        return hashlib.sha256(f"{run}|{use_id}".encode()).hexdigest()

    async def _replay(self, session: AsyncSession, ctx: ToolContext, previous: ToolCall) -> ToolResult:
        """The same tool use again (a resumed run): the handler is not run a second time. A row still
        ``running`` is closed as ``error``/``outcome_unknown`` (the run that owned it is gone: one worker
        holds a task's lease)."""
        call_id, status, summary = previous.id, previous.status, previous.output_summary or ""
        if status == "running":
            closed = await session.scalar(
                update(ToolCall)
                .where(ToolCall.id == call_id, ToolCall.status == "running")
                .values(status="error", error_code="outcome_unknown", output_summary=UNKNOWN_OUTCOME)
                .returning(ToolCall.id)
                .execution_options(synchronize_session=False)
            )
            if closed is not None:
                await session.refresh(previous)
                if previous.risk_level >= RiskLevel.R1:
                    await self._audit_call(
                        session, ctx, previous, "tool.executed", "error", {"status": "error", "replay": True}
                    )
                await self._event(session, ctx, previous, "tool.call_finished", UNKNOWN_OUTCOME)
                await self._commit(session)
            else:
                await session.rollback()
            return ToolResult(call_id, "error", UNKNOWN_OUTCOME, is_error=True)
        await session.rollback()
        cut = " (cut to its first 2048 characters)" if len(summary) >= SUMMARY_MAX else ""
        content = (
            f"this tool use already ran (status {status}); it is not run again. "
            f"Recorded result{cut}: {summary}"
        )
        return ToolResult(call_id, _REPLAYED.get(status, "error"), content, is_error=status != "ok")

    async def _run(self, ctx: ToolContext, spec: ToolSpec, validated: BaseModel | None) -> _Outcome:
        """The handler under its timeout and the cancel token. If the caller is cancelled, the handler is
        stopped and the cancellation propagates (``execute`` records it)."""
        handler = asyncio.ensure_future(spec.handler(ctx, validated))
        cancelled = asyncio.ensure_future(ctx.cancel.wait())
        unknown = _unknown_outcome(spec)
        try:
            try:
                async with asyncio.timeout(spec.timeout_s):
                    await asyncio.wait({handler, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            except TimeoutError:
                await self._stop(ctx, spec, handler)
                return _Outcome("timeout", f"error: the tool timed out after {spec.timeout_s:g} s.{unknown}")
            except asyncio.CancelledError:
                await self._stop(ctx, spec, handler)
                raise
        finally:
            cancelled.cancel()
        if not handler.done():
            await self._stop(ctx, spec, handler)
            if ctx.cancel.cancelled:
                return _Outcome("cancelled", f"cancelled: the run was cancelled.{unknown}")
            # The token's waiter failed (e.g. used from another event loop): not a cancellation (L7).
            logger.error("tool_cancel_token_failed", tool=spec.name, correlation_id=ctx.correlation_id)
            return _Outcome("error", f"error: the run's cancel signal failed.{unknown}", "internal")
        try:
            output = handler.result()
        except asyncio.CancelledError:  # the handler cancelled itself; this task was not cancelled
            return _Outcome("error", "error: the tool stopped without a result", "internal")
        except ToolError as exc:
            if exc.code in HANDLER_DENIALS:
                return _Outcome("denied", DENIALS[exc.code], exc.code)
            return _Outcome("error", f"error: {exc.message}", _handler_code(exc.code))
        except AppError as exc:
            return _Outcome("error", f"error: {exc.message}", _handler_code(exc.code))
        except Exception as exc:
            logger.warning(
                "tool_handler_failed",
                tool=spec.name,
                error_type=type(exc).__name__,
                correlation_id=ctx.correlation_id,
                exc_info=True,
            )
            return _Outcome("error", "error: the tool failed with an internal error", "internal")
        try:
            return _Outcome("ok", _content_text(output), untrusted=output.untrusted)
        except (TypeError, ValueError):
            logger.warning("tool_output_not_json", tool=spec.name)
            return _Outcome("error", "error: the tool returned an unreadable result", "internal")

    @staticmethod
    async def _stop(ctx: ToolContext, spec: ToolSpec, handler: asyncio.Future[ToolOutput]) -> None:
        """Cancel the handler and give it ``STOP_GRACE_S`` to stop. ``asyncio.wait`` neither raises the
        handler's error nor swallows a cancellation of the caller (S2/M4)."""
        handler.cancel()
        await asyncio.wait({handler}, timeout=STOP_GRACE_S)
        if handler.done():
            if not handler.cancelled():
                handler.exception()  # retrieved: a stopped handler's error is not reported again
            return
        logger.warning("tool_handler_did_not_stop", tool=spec.name, correlation_id=ctx.correlation_id)
        handler.add_done_callback(lambda done: None if done.cancelled() else done.exception())

    async def _settle_cancelled(self, ctx: ToolContext, row: ToolCall, spec: ToolSpec) -> None:
        """The caller was cancelled after the claim: record ``cancelled`` (shielded). If that fails the row
        stays ``running`` (a replay closes it as unknown) and the cancellation still propagates."""
        unknown = _unknown_outcome(spec)
        outcome = _Outcome("cancelled", f"cancelled: the run was cancelled.{unknown}")
        try:
            await asyncio.shield(self._record_detached(ctx, row, spec, outcome))
        except Exception:
            logger.error(
                "tool_call_cancel_unrecorded",
                tool_call_id=str(row.id),
                task_id=str(ctx.task_id),
                correlation_id=ctx.correlation_id,
                exc_info=True,
            )

    async def _record_detached(
        self, ctx: ToolContext, row: ToolCall, spec: ToolSpec, outcome: _Outcome
    ) -> None:
        async with self._sessions() as session:
            await self._record(session, ctx, row, spec, outcome, self._clock())

    async def _finish(
        self, ctx: ToolContext, row: ToolCall, spec: ToolSpec, outcome: _Outcome, started: float
    ) -> ToolResult:
        """Record the outcome of a handler that ran. If that fails, the row is marked ``record_failed`` (not
        left ``running``) and the failure propagates: no result without its record (M1)."""
        try:
            async with self._sessions() as session:
                return await self._record(session, ctx, row, spec, outcome, started)
        except Exception:
            logger.error(
                "tool_call_record_failed",
                tool_call_id=str(row.id),
                task_id=str(ctx.task_id),
                correlation_id=ctx.correlation_id,
                exc_info=True,
            )
            await self._mark_record_failed(ctx, row)
            raise

    async def _mark_record_failed(self, ctx: ToolContext, row: ToolCall) -> None:
        try:
            async with self._sessions() as session:
                await session.execute(
                    update(ToolCall)
                    .where(ToolCall.id == row.id, ToolCall.status == "running")
                    .values(
                        status="error",
                        error_code="record_failed",
                        output_summary="error: the tool ran but its outcome could not be recorded",
                        updated_at=func.now(),
                    )
                )
                await session.commit()
        except Exception:
            logger.error(
                "tool_call_unrecordable", tool_call_id=str(row.id), task_id=str(ctx.task_id), exc_info=True
            )

    # --- recording ----------------------------------------------------------------------------------------

    def _model_text(self, content: str, untrusted: Untrusted | None = None) -> str:
        """Redacted first, then cut: a cut must never split a secret so that its pattern no longer matches.
        Untrusted content is neutralised and cut to what fits beside its wrapper, then wrapped (3.1d2a)."""
        if untrusted is None:
            return self._redacted_cut(content, MAX_RESULT_BYTES, lambda text: text)
        redact = self._audit.redactor.redact
        path = None if untrusted.path is None else redact(strip_controls(_clean_text(untrusted.path)))
        opening = open_tag(untrusted.source, path)
        budget = MAX_RESULT_BYTES - len(opening.encode()) - len(CLOSE_TAG.encode())
        return opening + self._redacted_cut(content, budget, neutralise) + CLOSE_TAG

    def _redacted_cut(self, content: str, limit: int, after_redaction: Callable[[str], str]) -> str:
        redact = self._audit.redactor.redact
        if len(content) <= REDACT_WINDOW_CHARS:
            return _truncate(after_redaction(redact(_clean_text(content))), limit)
        total = len(_clean_text(content).encode())
        redacted = after_redaction(redact(_clean_text(content[:REDACT_WINDOW_CHARS])))
        return _truncate(redacted[:-WINDOW_EDGE_CHARS], limit, total)

    def _duration(self, started: float) -> int:
        return max(0, int((self._clock() - started) * 1000))

    async def _deny(
        self,
        session: AsyncSession,
        ctx: ToolContext,
        row: ToolCall,
        reason: str,
        detail: list[str],
        started: float,
    ) -> ToolResult:
        row.status, row.error_code = "denied", reason
        row.output_summary = DENIALS[reason]
        row.duration_ms = self._duration(started)
        row.idempotency_key = None
        session.add(row)
        await session.flush()
        await self._audit_call(
            session, ctx, row, "tool.denied", "denied", {"reason": reason, "unmet": detail}
        )
        await self._event(session, ctx, row, "tool.call_finished", DENIALS[reason])
        await self._commit(session)
        return ToolResult(row.id, "denied", DENIALS[reason], is_error=True)

    async def _finish_now(
        self, session: AsyncSession, ctx: ToolContext, row: ToolCall, outcome: _Outcome, started: float
    ) -> ToolResult:
        """A call that ends before any handler: invalid input or approval required."""
        content = self._model_text(outcome.content)
        row.status, row.error_code = outcome.status, outcome.error_code
        row.output_summary = content[:SUMMARY_MAX]
        row.duration_ms = self._duration(started)
        row.idempotency_key = None
        session.add(row)
        await session.flush()
        await self._event(session, ctx, row, "tool.call_finished", row.output_summary)
        await self._commit(session)
        return ToolResult(row.id, outcome.status, content, is_error=True)

    async def _record(
        self,
        session: AsyncSession,
        ctx: ToolContext,
        row: ToolCall,
        spec: ToolSpec,
        outcome: _Outcome,
        started: float,
    ) -> ToolResult:
        content = self._model_text(outcome.content, outcome.untrusted)
        summary = _summary(content, outcome.untrusted)
        updated = await session.scalar(
            update(ToolCall)
            .where(ToolCall.id == row.id, ToolCall.status == "running")
            .values(
                status=outcome.status,
                error_code=outcome.error_code,
                output_summary=summary,
                duration_ms=self._duration(started),
                updated_at=func.now(),
            )
            .returning(ToolCall.id)
        )
        if updated is None:
            # Recorded already (the caller was cancelled and the shielded write won): one outcome per row.
            raise RuntimeError(f"tool call {row.id} was no longer running when its outcome was recorded")
        row.status = outcome.status
        if outcome.status == "denied":  # a policy refusal from the handler: audited as every denial
            await self._audit_call(
                session, ctx, row, "tool.denied", "denied", {"reason": outcome.error_code, "unmet": []}
            )
        elif spec.risk >= RiskLevel.R1:
            result = "success" if outcome.status == "ok" else "error"
            await self._audit_call(session, ctx, row, "tool.executed", result, {"status": outcome.status})
        await self._event(session, ctx, row, "tool.call_finished", summary)
        await self._commit(session)
        return ToolResult(row.id, outcome.status, content, is_error=outcome.status != "ok")

    async def _audit_call(
        self,
        session: AsyncSession,
        ctx: ToolContext,
        row: ToolCall,
        action: str,
        result: str,
        extra: dict[str, Any],
    ) -> None:
        await self._audit.write(
            session,
            actor_type="agent",
            actor_id=ctx.grant.agent_id,
            action=action,
            result=result,
            resource_type="tool_call",
            resource_id=str(row.id),
            project_id=ctx.project_id,
            metadata={
                "tool": row.tool_name,
                "risk_level": row.risk_level,
                "task_id": str(ctx.task_id),
                "agent_run_id": str(ctx.agent_run_id) if ctx.agent_run_id else None,
                "approval_id": str(row.approval_id) if row.approval_id else None,
                **extra,
            },
            correlation_id=ctx.correlation_id[:80] or None,
        )

    async def _event(
        self, session: AsyncSession, ctx: ToolContext, row: ToolCall, kind: str, summary: str | None
    ) -> None:
        payload: dict[str, Any] = {
            "tool_call_id": str(row.id),
            "tool_name": row.tool_name,
            "risk_level": row.risk_level,
        }
        if kind == "tool.call_finished":
            payload["status"] = row.status
            payload["summary"] = (summary or "")[:_EVENT_SUMMARY_MAX]
        await emit_task_event(session, None, ctx.task_id, kind, payload, redactor=self._audit.redactor)

    async def _commit(self, session: AsyncSession) -> None:
        await session.commit()
        await self._publish(session)

    async def _publish(self, session: AsyncSession) -> None:
        if self._bus is not None:
            await publish_pending(session, self._bus)
