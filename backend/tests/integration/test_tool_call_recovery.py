"""``close_abandoned_calls`` (WP-3.1f2b1 Keputusan Lead #8): recovery closes the ``running`` tool calls of a
dead run as ``error``/``outcome_unknown`` through the gateway, the only writer of ``tool_calls``."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.tasks import TaskEvent
from app.models.tools import ToolCall
from app.tools.gateway import (
    _EVENT_SUMMARY_MAX,  # pyright: ignore[reportPrivateUsage]
    UNKNOWN_OUTCOME,
    close_abandoned_calls,
)
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope, audits, calls, timeline
from tests.tool_fakes import AUDIT

clean = denials.clean
sessions = denials.sessions
scope = denials.scope


def running(scope: Scope, name: str, risk: int, key: str) -> ToolCall:
    return ToolCall(
        task_id=scope.task_id,
        agent_id="B14",
        tool_name=name,
        risk_level=risk,
        input_hash="a" * 64,
        sanitized_input={"path": "a.md"},
        status="running",
        tool_use_id=key,
    )


async def test_running_calls_close_as_unknown_with_audit_for_r1_only(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    async with sessions() as session:
        r0, r1 = running(scope, "repo.read_file", 0, "t0"), running(scope, "repo.write_file", 1, "t1")
        session.add_all([r0, r1])
        await session.commit()
    async with sessions() as session:
        locked = list(
            (
                await session.scalars(
                    select(ToolCall).where(ToolCall.task_id == scope.task_id).with_for_update()
                )
            ).all()
        )
        closed = await close_abandoned_calls(session, AUDIT, locked, project_id=scope.project_id)
        await session.commit()
    assert closed == 2
    rows = await calls(sessions, scope.task_id)
    assert {(r.status, r.error_code, r.output_summary) for r in rows} == {
        ("error", "outcome_unknown", UNKNOWN_OUTCOME)
    }
    assert await audits(sessions, r0) == []
    [audit] = await audits(sessions, r1)
    assert (audit.action, audit.result, audit.actor_type, audit.actor_id) == (
        "tool.executed",
        "error",
        "system",
        "recovery",
    )
    assert audit.project_id == scope.project_id
    assert audit.metadata_["recovered"] is True and audit.metadata_["status"] == "error"
    assert audit.metadata_["tool"] == "repo.write_file" and audit.metadata_["risk_level"] == 1
    assert audit.metadata_["task_id"] == str(scope.task_id)
    events = [e for e in await timeline(sessions, scope.task_id) if e.type == "tool.call_finished"]
    assert len(events) == 2
    assert {(e.payload["status"], e.payload["summary"]) for e in events} == {
        ("error", UNKNOWN_OUTCOME[:_EVENT_SUMMARY_MAX])
    }
    assert {e.payload["tool_call_id"] for e in events} == {str(r0.id), str(r1.id)}


async def test_a_call_no_longer_running_is_left_alone(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    async with sessions() as session:
        row = running(scope, "repo.write_file", 1, "t1")
        session.add(row)
        await session.commit()
        stale = await session.get(ToolCall, row.id)  # loaded while still running
        assert stale is not None
        async with sessions() as other:  # the owner finished it meanwhile
            done = await other.get(ToolCall, row.id)
            assert done is not None
            done.status = "ok"
            await other.commit()
        closed = await close_abandoned_calls(session, AUDIT, [stale], project_id=scope.project_id)
        await session.commit()
    assert closed == 0
    [after] = await calls(sessions, scope.task_id)
    assert after.status == "ok" and after.error_code is None
    assert await audits(sessions, after) == []
    async with sessions() as session:
        assert not (
            await session.scalars(select(TaskEvent).where(TaskEvent.type == "tool.call_finished"))
        ).all()
