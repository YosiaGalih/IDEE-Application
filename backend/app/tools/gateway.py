"""ToolGateway: the one door for an agent's runtime actions (CLAUDE.md #2; ARCHITECTURE §6.1; SECURITY_MODEL
§3, §9, §10; spec WP-3.1d Goal 3).

``execute`` checks, in order: name and allowlist (offered to the model **and** granted by the registry,
registered, not the terminal ``submit_result``) → permission preconditions → input schema → scope and write
paths (task of this project, not finished; project active) → per-tool quota → risk and approval (R3 needs an
approval for exactly this input, consumed once) → claim → handler (timeout, cancel) → redaction then
truncation → record. Every call leaves a ``tool_calls`` row; every refusal is audited ``tool.denied``; every
R1+ call that reaches its handler is audited ``tool.executed``; the task timeline gets ``tool.call_started``
and ``tool.call_finished``.

The gateway uses its own sessions: the ``running`` claim is committed before the handler starts, so a crash
leaves evidence, and a side-effect tool is never run twice for one ``tool_use_id`` (Keputusan Lead 2, 5).
The policy transaction locks the task row first (``FOR NO KEY UPDATE``), then any approval, the order the
``tool_r3`` handler takes too.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter, json_safe
from app.core.errors import AppError
from app.core.events import EventBus, emit_task_event, publish_pending
from app.core.logging import get_logger
from app.llm.base import ToolDef, is_canonical_tool_name
from app.models.tools import SUMMARY_MAX, ToolCall
from app.tools.base import (
    AgentGrant,
    RiskLevel,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolPorts,
    ToolResult,
    ToolSpec,
)
from app.tools.catalog import TOOL_CATALOG, ToolInfo
from app.tools.policy import is_r4, unmet_preconditions, write_allowed
from app.tools.scope import TaskScope, lock_task_scope

logger = get_logger(__name__)

TERMINAL_TOOL = "submit_result"
MAX_RESULT_BYTES = 64 * 1024  # one tool_result to the model (TOOL_CATALOG, SECURITY_MODEL §3; Keputusan 6)
# Only this much of a handler's output is redacted (then cut to MAX_RESULT_BYTES): the redaction cost stays
# bounded whatever a handler returns, and a secret that reaches the final window lies wholly inside this one.
REDACT_WINDOW_CHARS = 4 * MAX_RESULT_BYTES
INVALID_NAME = "<invalid>"
# The finished task statuses (app.workflow.states.FINISHED; tools may not import workflow, a test keeps
# the two equal).
FINISHED_TASK_STATUSES = frozenset({"completed", "failed", "cancelled", "rejected"})
# Rows that reached a handler count against a quota.
_EXECUTED = ("running", "ok", "error", "timeout", "cancelled")
_TOOL_USE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_KEY_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
_INPUT_STRING_MAX = 1024
_INPUT_ITEMS_MAX = 50
_INPUT_DEPTH_MAX = 6
_EVENT_SUMMARY_MAX = 200
_R3_REQUESTER = "system:tool_gateway"

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
}


class TaskMissing(LookupError):
    """The context names a task that does not exist (purged with its project): nothing can be recorded."""


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: str
    content: str
    error_code: str | None = None


def _canonical_name(tool_name: object) -> str | None:
    return tool_name if isinstance(tool_name, str) and is_canonical_tool_name(tool_name) else None


def _input_hash(raw_input: object) -> str:
    text = json.dumps(raw_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=repr)
    return hashlib.sha256(text.encode()).hexdigest()


def _no_nul(text: str) -> str:
    """PostgreSQL refuses NUL in text and jsonb: one NUL from a model or a file must not make a call's outcome
    unrecordable. U+FFFD marks where it was."""
    return text.replace("\x00", "\ufffd")


def _bounded(value: Any, depth: int = 0) -> Any:
    """Long strings, long lists and deep nesting cut: ``sanitized_input`` keeps what identifies a call."""
    if depth >= _INPUT_DEPTH_MAX:
        return "<nested>"
    if isinstance(value, str):
        value = _no_nul(value)
        return (
            value if len(value) <= _INPUT_STRING_MAX else f"{value[:_INPUT_STRING_MAX]}…[{len(value)} chars]"
        )
    if isinstance(value, dict):
        items = list(value.items())[:_INPUT_ITEMS_MAX]  # pyright: ignore[reportUnknownArgumentType]
        return {_no_nul(str(k)): _bounded(v, depth + 1) for k, v in items}  # pyright: ignore[reportUnknownVariableType]
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
        row = ToolCall(
            task_id=ctx.task_id,
            agent_run_id=ctx.agent_run_id,
            agent_id=ctx.grant.agent_id,
            tool_name=name or INVALID_NAME,
            tool_use_id=use_id,
            risk_level=int(risk),
            input_hash=_input_hash(raw_input),
            sanitized_input=json_safe(_bounded(self._audit.redactor.redact_obj(raw_input))),
        )

        refusal, detail = self._refusal(ctx, name, spec, info)
        validated: BaseModel | None = None
        invalid: str | None = None
        if refusal is None and spec is not None:
            try:
                validated = spec.input_model.model_validate(raw_input)
            except ValidationError as exc:
                invalid = _invalid_input(exc)
        if refusal is None and validated is not None and spec is not None:
            if spec.side_effect and use_id is None:
                refusal = "invalid_tool_use_id"
            else:
                refusal, invalid = self._path_refusal(ctx, spec, validated)

        async with self._sessions() as session:
            scope = await self._lock_task(session, ctx)
            if refusal is None and invalid is None:
                refusal = await self._scope_refusal(ctx, scope)
            if refusal is None and invalid is None and spec is not None and spec.side_effect:
                assert use_id is not None
                # Before quota and approval: the same tool use again returns what happened, it is not a
                # new action (an R3 replay must not ask for a new approval).
                row.idempotency_key = self._idempotency_key(ctx, use_id)
                previous = await session.scalar(
                    select(ToolCall).where(ToolCall.idempotency_key == row.idempotency_key)
                )
                if previous is not None and (
                    previous.tool_name != row.tool_name or previous.input_hash != row.input_hash
                ):
                    refusal = "tool_use_id_reused"  # never answer one call with another call's result
                elif previous is not None:
                    replay = self._replay(previous)  # read before the rollback expires the row
                    await session.rollback()
                    return replay
            if refusal is None and invalid is None and spec is not None:
                refusal = await self._quota_refusal(session, ctx, spec)
            if refusal is not None:
                return await self._deny(session, ctx, row, refusal, detail, started)
            if invalid is not None or validated is None or spec is None:
                outcome = _Outcome("error", invalid or "invalid input", "invalid_input")
                return await self._finish_now(session, ctx, row, outcome, started)
            if spec.risk is RiskLevel.R3:
                result = await self._authorize_r3(session, ctx, row, spec, validated, started)
                if result is not None:
                    return result
            row.status = "running"
            session.add(row)
            await session.flush()
            await self._event(session, ctx, row, "tool.call_started", None)
            await self._commit(session)

        outcome = await self._run(ctx, row, spec, validated)
        async with self._sessions() as session:
            return await self._record(session, ctx, row, spec, outcome, started)

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
            .where(scope, ToolCall.tool_name == spec.name, ToolCall.status.in_(_EXECUTED))
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
        here as ``approval_required`` (or denied when the input cannot be hashed canonically)."""
        payload = {
            "task_id": str(ctx.task_id),
            "agent_run_id": str(ctx.agent_run_id) if ctx.agent_run_id is not None else None,
            "tool": spec.name,
            "input": validated.model_dump(mode="json"),
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
        except ValueError:  # floats and other values a canonical hash refuses (approval_store.canonical_json)
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

    def _replay(self, previous: ToolCall) -> ToolResult:
        """The same tool use again (a resumed run): the handler is not run a second time."""
        if previous.status == "running":
            content = (
                "error: this tool use was started before and its outcome is unknown (the process stopped "
                "while it ran); it is not run again"
            )
            return ToolResult(previous.id, "error", content, is_error=True)
        content = (
            f"this tool use already ran (status {previous.status}); it is not run again. "
            f"Recorded result: {previous.output_summary or ''}"
        )
        status = previous.status if previous.status in ("ok", "error", "timeout", "cancelled") else "error"
        return ToolResult(previous.id, status, content, is_error=status != "ok")  # type: ignore[arg-type]

    async def _run(
        self, ctx: ToolContext, row: ToolCall, spec: ToolSpec, validated: BaseModel | None
    ) -> _Outcome:
        """The handler under its timeout and the cancel token. The caller being cancelled records the row as
        ``cancelled`` (shielded) and re-raises."""
        handler = asyncio.ensure_future(spec.handler(ctx, validated))
        cancelled = asyncio.ensure_future(ctx.cancel.wait())
        unknown = " The outcome is unknown: the action may have been applied." if spec.side_effect else ""
        try:
            try:
                async with asyncio.timeout(spec.timeout_s):
                    await asyncio.wait({handler, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            except TimeoutError:
                await self._stop(handler)
                return _Outcome("timeout", f"error: the tool timed out after {spec.timeout_s:g} s.{unknown}")
            if not handler.done():
                await self._stop(handler)
                return _Outcome("cancelled", f"cancelled: the run was cancelled.{unknown}")
        except asyncio.CancelledError:
            await self._stop(handler)
            outcome = _Outcome("cancelled", f"cancelled: the run was cancelled.{unknown}")
            try:
                await asyncio.shield(self._record_detached(ctx, row, spec, outcome))
            except Exception:
                # The row stays "running" (a replay reports the outcome as unknown); the cancel still wins.
                logger.error("tool_call_cancel_unrecorded", tool_call_id=str(row.id), exc_info=True)
            raise
        finally:
            cancelled.cancel()
        try:
            output = handler.result()
        except asyncio.CancelledError:  # the handler cancelled itself; this task was not cancelled
            return _Outcome("error", "error: the tool stopped without a result", "internal")
        except ToolError as exc:
            return _Outcome("error", f"error: {exc.message}", exc.code[:40])
        except AppError as exc:
            return _Outcome("error", f"error: {exc.message}", exc.code[:40])
        except Exception as exc:
            logger.warning(
                "tool_handler_failed", tool=spec.name, error_type=type(exc).__name__, exc_info=True
            )
            return _Outcome("error", "error: the tool failed with an internal error", "internal")
        try:
            return _Outcome("ok", _content_text(output))
        except (TypeError, ValueError):
            logger.warning("tool_output_not_json", tool=spec.name)
            return _Outcome("error", "error: the tool returned an unreadable result", "internal")

    @staticmethod
    async def _stop(handler: asyncio.Future[ToolOutput]) -> None:
        handler.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await handler

    async def _record_detached(
        self, ctx: ToolContext, row: ToolCall, spec: ToolSpec, outcome: _Outcome
    ) -> None:
        async with self._sessions() as session:
            await self._record(session, ctx, row, spec, outcome, self._clock())

    # --- recording ----------------------------------------------------------------------------------------

    def _model_text(self, content: str) -> str:
        """Redacted first, then cut: a cut must never split a secret so that its pattern no longer matches."""
        window = content[:REDACT_WINDOW_CHARS]
        total = len(content.encode()) if len(content) > REDACT_WINDOW_CHARS else None
        return _truncate(self._audit.redactor.redact(_no_nul(window)), MAX_RESULT_BYTES, total)

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
        return ToolResult(row.id, outcome.status, content, is_error=True)  # type: ignore[arg-type]

    async def _record(
        self,
        session: AsyncSession,
        ctx: ToolContext,
        row: ToolCall,
        spec: ToolSpec,
        outcome: _Outcome,
        started: float,
    ) -> ToolResult:
        content = self._model_text(outcome.content)
        summary = content[:SUMMARY_MAX]
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
        if spec.risk >= RiskLevel.R1:
            result = "success" if outcome.status == "ok" else "error"
            await self._audit_call(session, ctx, row, "tool.executed", result, {"status": outcome.status})
        await self._event(session, ctx, row, "tool.call_finished", summary)
        await self._commit(session)
        return ToolResult(row.id, outcome.status, content, is_error=outcome.status != "ok")  # type: ignore[arg-type]

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
        if self._bus is not None:
            await publish_pending(session, self._bus)
