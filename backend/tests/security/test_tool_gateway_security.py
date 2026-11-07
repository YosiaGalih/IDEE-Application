"""ToolGateway security regressions (SECURITY_MODEL T1, T5, T7, T10; spec WP-3.1d Review Focus 1, 3, 4).

The model's output is untrusted: a tool name, an input or a handler's output can carry an injection or a
secret. Names are matched against what was offered and granted, never trusted; nothing the gateway records
or returns carries a configured secret; no tool can create a decision on an approval.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.approvals import Approval
from app.tools.base import RiskLevel, ToolError, ToolOutput
from app.tools.catalog import ToolInfo
from app.tools.gateway import ToolGateway
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope, calls
from tests.tool_fakes import AUDIT, CATALOG, SECRET, ReadInput, Recorder, context, gateway, spec

sessions = denials.sessions
scope = denials.scope

AUDIT_DUMP = "SELECT coalesce(json_agg(a)::text, '') FROM audit_events a WHERE resource_type = 'tool_call'"
INJECTED_NAMES = [
    "approval.decide",
    "approvals.approve",
    "shell.exec",
    "docker.run",
    "env.read",
    "db.query",
    "git.force_push",
    "repo__read_file",  # the provider encoding, never a canonical name
    "REPO.READ_FILE",
    "repo.read_file​",
    "repo.read_file; rm -rf /",
    "test.read\n",
    "a" * 200,
    "",
]


@pytest.mark.parametrize("name", INJECTED_NAMES)
async def test_injected_tool_names_never_run(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, name: str
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    # Even offered and granted by a buggy runtime, these names do not reach a handler.
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read", name])
    result = await built.execute(ctx, name, {})
    assert result.status == "denied" and handler.calls == []
    [row] = await calls(sessions, scope.task_id)
    assert row.tool_name in ("<invalid>", name) and len(row.tool_name) <= 80


async def test_granted_but_not_offered_and_offered_but_not_granted_are_both_refused(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    handler = Recorder()
    built = gateway(sessions, spec("test.read", handler))
    hidden = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read"], offered=[])
    smuggled = context(task_id=scope.task_id, project_id=scope.project_id, tools=[], offered=["test.read"])
    assert (await built.execute(hidden, "test.read", {})).status == "denied"
    assert (await built.execute(smuggled, "test.read", {})).status == "denied"
    assert handler.calls == []


@pytest.mark.parametrize(
    "name", ["approval.decide", "approvals.create", "policy.disable_audit", "audit.delete"]
)
def test_no_tool_can_touch_approvals_or_audit_even_if_catalogued(name: str) -> None:
    # CLAUDE.md #5: a decision is a person's HTTP request; a tool that decides cannot even be registered.
    catalog = MappingProxyType({**CATALOG, name: ToolInfo(name, "R1", "backend", (), "mvp")})
    built = ToolGateway(sessions=async_sessionmaker(), audit=AUDIT, ports=None, catalog=catalog)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="R4"):
        built.register(spec(name, Recorder(), input_model=ReadInput, risk=RiskLevel.R1))


async def test_a_secret_never_leaves_the_gateway(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, engine: AsyncEngine
) -> None:
    """T5: a secret in the input, in a handler's output and in a handler's error never reaches the model,
    the row, the audit or the timeline."""
    leaky_output = Recorder(result=lambda _: ToolOutput(f"config: api_key={SECRET}\n{SECRET}"))

    def fail(_: object) -> ToolOutput:
        raise ToolError("leak", f"cannot open {SECRET}")

    built = gateway(
        sessions,
        spec("test.read", leaky_output),
        spec("test.write", Recorder(result=fail)),
    )
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.read", "test.write"])
    denied = context(task_id=scope.task_id, project_id=scope.project_id, tools=[], offered=["test.read"])
    results = [
        await built.execute(ctx, "test.read", {"text": SECRET}),
        await built.execute(ctx, "test.write", {"path": "a.py", "content": SECRET}, tool_use_id="toolu_1"),
        await built.execute(denied, "test.read", {"text": SECRET}),
        await built.execute(ctx, "test.read", {"text": SECRET, "extra": SECRET}),
    ]
    assert all(SECRET not in result.content for result in results)
    assert "[REDACTED]" in results[0].content
    async with engine.connect() as conn:
        dumps = [
            (
                await conn.execute(text("SELECT coalesce(json_agg(t)::text, '') FROM tool_calls t"))
            ).scalar_one(),
            (await conn.execute(text(AUDIT_DUMP))).scalar_one(),
            (
                await conn.execute(text("SELECT coalesce(json_agg(e)::text, '') FROM task_events e"))
            ).scalar_one(),
        ]
    assert dumps[0] and dumps[1] and dumps[2]
    assert all(SECRET not in dump for dump in dumps)


async def test_an_r3_call_cannot_approve_itself(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    """T7 / AC-13: whatever the model sends, an R3 call only ever creates a pending request."""
    built = gateway(sessions, spec("test.r3", Recorder()))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.r3"])
    for raw in ({"target": "approved"}, {"target": "x", "count": 1}):
        result = await built.execute(ctx, "test.r3", raw, tool_use_id="toolu_1")
        assert result.status == "approval_required"
    async with sessions() as session:
        statuses = (await session.scalars(select(Approval.status).order_by(Approval.created_at))).all()
    assert statuses == ["superseded", "pending"]
