"""ToolGateway execution (spec WP-3.1d Goal 3, Review Focus 2 and 3; TEST_STRATEGY §3 app/tools): what runs is
recorded, a side effect never runs twice for one tool use, timeouts and cancels say the outcome is unknown,
handler failures never echo their exception, and output is redacted before it is cut."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.cancel import CancelToken
from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.tools import ToolCall
from app.tools.base import ToolError, ToolOutput
from app.tools.gateway import MAX_RESULT_BYTES, TaskMissing
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope, audits, calls, timeline
from tests.tool_fakes import SECRET, Recorder, context, gateway, spec

# Shared fixtures (bound here so pytest finds them in this module).
clean = denials.clean
sessions = denials.sessions
scope = denials.scope


async def test_an_allowed_read_runs_and_is_recorded(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder(result=lambda v: ToolOutput({"path": v.path, "lines": 3}))
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])
    result = await built.execute(ctx, "test.read", {"path": "a.md"}, tool_use_id="toolu_1")
    assert (result.status, result.is_error) == ("ok", False)
    assert result.content == '{"lines": 3, "path": "a.md"}'
    [row] = await calls(sessions, scope.task_id)
    assert (row.id, row.status, row.error_code, row.risk_level) == (result.tool_call_id, "ok", None, 0)
    assert (row.tool_use_id, row.idempotency_key, row.agent_id) == ("toolu_1", None, "B14")
    assert row.output_summary == result.content and row.duration_ms is not None
    assert row.sanitized_input == {"path": "a.md"}
    assert await audits(sessions, row) == []  # R0: the row is the record (SECURITY_MODEL §10)
    events = await timeline(sessions, scope.task_id)
    assert [(e.type, e.payload.get("status")) for e in events] == [
        ("tool.call_started", None),
        ("tool.call_finished", "ok"),
    ]


async def test_a_write_is_audited_and_keyed(sessions: async_sessionmaker[AsyncSession], scope: Scope) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.write", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"])
    result = await built.execute(ctx, "test.write", {"path": "src/a.py"}, tool_use_id="toolu_7")
    assert result.status == "ok"
    [row] = await calls(sessions, scope.task_id)
    assert row.idempotency_key == hashlib.sha256(f"task:{scope.task_id}|toolu_7".encode()).hexdigest()
    audited = await audits(sessions, row)
    assert [(a.action, a.result) for a in audited] == [("tool.executed", "success")]
    assert audited[0].metadata_["status"] == "ok"


async def test_invalid_input_is_an_error_not_a_denial(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])
    result = await built.execute(ctx, "test.read", {"path": 3, "bogus": True, "a sentence key": 1})
    assert (result.status, result.is_error) == ("error", True)
    assert "path: string_type" in result.content and "bogus: extra_forbidden" in result.content
    assert "a sentence key" not in result.content and "<key>" in result.content
    assert handler.calls == []
    [row] = await calls(sessions, scope.task_id)
    assert (row.status, row.error_code) == ("error", "invalid_input")
    assert await audits(sessions, row) == []


async def test_a_side_effect_never_runs_twice_for_one_tool_use(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.write", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"])
    first = await built.execute(ctx, "test.write", {"path": "src/a.py"}, tool_use_id="toolu_1")
    again = await built.execute(ctx, "test.write", {"path": "src/a.py"}, tool_use_id="toolu_1")
    assert len(handler.calls) == 1
    assert again.tool_call_id == first.tool_call_id and again.status == "ok"
    assert "already ran" in again.content
    assert len(await calls(sessions, scope.task_id)) == 1
    other = await built.execute(ctx, "test.write", {"path": "src/a.py"}, tool_use_id="toolu_2")
    assert other.tool_call_id != first.tool_call_id and len(handler.calls) == 2


async def test_a_side_effect_whose_outcome_is_unknown_is_not_run_again(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    # The process died while the handler ran: the claim is still "running".
    key = hashlib.sha256(f"task:{scope.task_id}|toolu_9".encode()).hexdigest()
    async with sessions() as session:
        session.add(
            ToolCall(
                task_id=scope.task_id,
                agent_id="B14",
                tool_name="test.write",
                tool_use_id="toolu_9",
                risk_level=1,
                idempotency_key=key,
                input_hash="0" * 64,
                sanitized_input={},
                status="running",
            )
        )
        await session.commit()
    handler = Recorder()
    built = gateway(sessions, spec("test.write", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"])
    result = await built.execute(ctx, "test.write", {"path": "src/a.py"}, tool_use_id="toolu_9")
    assert (result.status, result.is_error) == ("error", True)
    assert "outcome is unknown" in result.content
    assert handler.calls == []


async def test_a_timeout_is_recorded_and_the_outcome_is_unknown(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder(delay=5)
    built = gateway(sessions, spec("test.write", handler, timeout_s=0.05))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"])
    result = await built.execute(ctx, "test.write", {"path": "src/a.py"}, tool_use_id="toolu_1")
    assert (result.status, result.is_error) == ("timeout", True)
    assert "0.05 s" in result.content and "outcome is unknown" in result.content
    [row] = await calls(sessions, scope.task_id)
    assert row.status == "timeout"
    assert [(a.action, a.result) for a in await audits(sessions, row)] == [("tool.executed", "error")]


async def test_the_cancel_token_stops_the_handler(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    token = CancelToken()
    handler = Recorder(delay=30)
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"], cancel=token)
    running = asyncio.create_task(built.execute(ctx, "test.read", {}))
    await asyncio.wait_for(handler.started.wait(), timeout=5)
    token.cancel()
    result = await asyncio.wait_for(running, timeout=5)
    assert (result.status, result.is_error) == ("cancelled", True)
    [row] = await calls(sessions, scope.task_id)
    assert row.status == "cancelled"


async def test_a_cancelled_caller_still_records_the_call(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder(delay=30)
    built = gateway(sessions, spec("test.write", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"])
    running = asyncio.create_task(built.execute(ctx, "test.write", {"path": "a.py"}, tool_use_id="toolu_1"))
    await asyncio.wait_for(handler.started.wait(), timeout=5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    [row] = await calls(sessions, scope.task_id)
    assert row.status == "cancelled" and "outcome is unknown" in (row.output_summary or "")


@pytest.mark.parametrize(
    ("raised", "code", "shown"),
    [
        (ToolError("not_found", "no such file"), "not_found", "error: no such file"),
        (AppError("PATH_VIOLATION", "path escapes the workspace"), "PATH_VIOLATION", "path escapes"),
        (RuntimeError(f"/srv/workspaces/x/.env leaked {SECRET}"), "internal", "internal error"),
    ],
)
async def test_handler_failures_are_errors_that_never_echo_the_exception(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, raised: Exception, code: str, shown: str
) -> None:
    def fail(_: object) -> ToolOutput:
        raise raised

    built = gateway(sessions, spec("test.read", Recorder(result=fail)))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])
    result = await built.execute(ctx, "test.read", {})
    assert (result.status, result.is_error) == ("error", True)
    assert shown in result.content
    [row] = await calls(sessions, scope.task_id)
    assert row.error_code == code
    for text in (result.content, row.output_summary or ""):
        assert "/srv/workspaces" not in text and SECRET not in text


@pytest.mark.parametrize("offset", range(40, 120, 5))
async def test_output_is_redacted_before_it_is_cut(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, offset: int
) -> None:
    # The secret is placed across the 64 KB cut (the marker takes the last ~60 bytes): cutting first would
    # keep a prefix of it that the redactor no longer recognises.
    filler = "a" * (MAX_RESULT_BYTES - offset)
    handler = Recorder(result=lambda _: ToolOutput(filler + SECRET + "b" * 500))
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])
    result = await built.execute(ctx, "test.read", {})
    assert len(result.content.encode()) <= MAX_RESULT_BYTES
    assert "truncated" in result.content
    assert SECRET[:6] not in result.content


async def test_the_recorded_input_is_redacted_and_bounded(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    built = gateway(sessions, spec("test.read", Recorder()))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])
    raw = {"path": "a.md", "text": f"token={SECRET} " + "x" * 5000}
    await built.execute(ctx, "test.read", raw)
    [row] = await calls(sessions, scope.task_id)
    stored = row.sanitized_input["text"]
    assert SECRET not in stored and len(stored) < 1100 and stored.endswith("chars]")
    assert len(row.input_hash) == 64


async def test_a_missing_task_is_refused_loudly(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    built = gateway(sessions, spec("test.read", Recorder()))
    ctx = context(task_id=uuid.uuid4(), project_id=scope.project_id, tools=["test.read"])
    with pytest.raises(TaskMissing):
        await built.execute(ctx, "test.read", {})


async def test_a_handler_result_that_is_not_json_is_an_error(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    built = gateway(sessions, spec("test.read", Recorder(result=lambda _: ToolOutput({"x": object()}))))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])
    result = await built.execute(ctx, "test.read", {})
    assert (result.status, result.content) == ("error", "error: the tool returned an unreadable result")


async def test_denied_audit_never_carries_the_raw_input(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, engine: AsyncEngine
) -> None:
    built = gateway(sessions, spec("test.read", Recorder()))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=[], offered=["test.read"])
    await built.execute(ctx, "test.read", {"text": SECRET})
    async with sessions() as session:
        metadata = (await session.scalars(select(AuditEvent.metadata_))).all()
    assert all(SECRET not in str(item) for item in metadata)


async def test_nul_characters_never_break_the_record(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    # PostgreSQL refuses NUL in text and jsonb: a model or a file could make the outcome unrecordable.
    handler = Recorder(result=lambda _: ToolOutput("before\x00after"))
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])
    result = await built.execute(ctx, "test.read", {"text": "a\x00b"})
    assert result.status == "ok" and "\x00" not in result.content
    [row] = await calls(sessions, scope.task_id)
    assert row.status == "ok" and "\x00" not in (row.output_summary or "")
    assert "\x00" not in row.sanitized_input["text"]


async def test_a_cancelled_caller_stays_cancelled_when_recording_fails(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = Recorder(delay=30)
    built = gateway(sessions, spec("test.read", handler))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"])

    async def broken(*_: object) -> None:
        raise RuntimeError("database gone")

    monkeypatch.setattr(built, "_record_detached", broken)
    running = asyncio.create_task(built.execute(ctx, "test.read", {}))
    await asyncio.wait_for(handler.started.wait(), timeout=5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
