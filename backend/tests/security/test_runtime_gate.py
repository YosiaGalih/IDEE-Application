"""The provider gate and the kill switch before every model attempt (spec WP-3.1f Keputusan Lead #16; AG-13
3.1e2 (3); SECURITY_MODEL §7; plan WP-3.1f2a Task 4).

``DbProviderGate`` reads the provider row and the project afresh on every check, in the order of the decision:
``provider_ready(row, settings, held=WorkerCredentials(key, base_url))`` first (``disabled`` ->
``PROVIDER_DISABLED``; ``not_ready``/``unknown`` -> ``provider_not_ready``), then the project's kill switch
(anything but ``true`` -> ``PROVIDER_DISABLED``; a missing project fails closed). Through the single
model-call path a refused gate sends nothing and records nothing, and a switch flipped mid-run stops the next
attempt without a restart.

The run-level assertions (the run ends ``failed`` with the code) are in the Task 5 part of this file.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.runtime import AgentRunResult, ProviderBlocked, RunScope
from app.core.audit import AuditWriter
from app.core.cancel import CancelToken
from app.core.config import Settings, load_settings
from app.core.redaction import Redactor
from app.llm.pricing import PricingTable
from app.llm.readiness import CHECK_KEYS, Check, ReadinessReport
from app.llm.scenarios.model import Fail
from app.models.agents import AgentRun
from app.models.projects import Project
from app.models.providers import ProviderConfig
from app.models.usage import UsageRecord
from app.services import provider_readiness
from app.services.agent_runtime import DbProviderGate, GuardBudget, LedgerRecorder
from app.services.projects import KILL_SWITCH_KEY
from app.services.usage import UsageLedger
from app.workflow.budget import BudgetDefaults, BudgetGuard
from tests.conftest import make_test_env, reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.runtime_fakes import (
    OUTPUTS,
    Composition,
    FakeHeartbeat,
    Fakes,
    FakeTools,
    a_model_call,
    compose,
    hold_lease,
    in_order,
    make_runtime,
    ok_result,
    run_request,
    scripted,
    submit_turn,
    tool_turn,
)
from tests.tool_fakes import make_run

KEY = "sk-ant-" + "gate-" + "w" * 28 + "k9z8"  # built, not written: a test value
OTHER_KEY = "sk-ant-" + "rotated-" + "v" * 24 + "c3d4"
AUDIT = AuditWriter(Redactor())


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


def worker(**env: str) -> Settings:
    """The worker's settings: scripted mode with its own key (the gate's WorkerCredentials)."""
    return load_settings(make_test_env(**{"ANTHROPIC_API_KEY": KEY, **env}))


def report(mode: str = "scripted", *, failed: str | None = None) -> ReadinessReport:
    checks = tuple(Check(key, "failed" if key == failed else "passed", "detail") for key in CHECK_KEYS)
    return ReadinessReport(provider="anthropic", mode=mode, checks=checks, ran_at=datetime.now(UTC))


async def record(
    session: AsyncSession, settings: Settings, rep: ReadinessReport | None = None, *, key: str | None = KEY
) -> None:
    await provider_readiness.record_report(
        session,
        rep or report(),
        key=key,
        base_url=settings.anthropic_base_url,
        pricing_version="2026-11-05",
        models=provider_readiness.effective_models(settings, {}),
        settings=settings,
        audit=AUDIT,
        actor_id="cli",
    )
    await session.commit()


async def set_enabled(session: AsyncSession, enabled: bool) -> None:
    await session.execute(
        update(ProviderConfig).where(ProviderConfig.provider == "anthropic").values(enabled=enabled)
    )
    await session.commit()


async def set_kill_switch(session: AsyncSession, project_id: uuid.UUID, value: object) -> None:
    project = await session.get(Project, project_id)
    assert project is not None
    project.settings = {**(project.settings or {}), KILL_SWITCH_KEY: value}
    await session.commit()


async def a_scope(session: AsyncSession) -> RunScope:
    project = await make_project(session)
    task = await make_task(session, project)
    run_id = await make_run(session, task.id, "B14")
    await session.commit()
    return RunScope(project_id=project.id, task_id=task.id, agent_run_id=run_id, agent_id="B14")


async def ledger_rows(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(UsageRecord)) or 0


async def blocked(gate: DbProviderGate, project_id: uuid.UUID) -> ProviderBlocked:
    with pytest.raises(ProviderBlocked) as exc:
        await gate.check(project_id)
    return exc.value


# --- the gate itself ----------------------------------------------------------------------------------------


async def test_a_ready_provider_with_the_switch_on_returns_the_models(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    settings = worker(CLAUDE_MODEL_FAST="claude-haiku-5-5")
    await record(session, settings)
    scope = await a_scope(session)
    models = await DbProviderGate(sessions, settings).check(scope.project_id)
    assert dict(models) == {
        "default": "claude-test-model",
        "fast": "claude-haiku-5-5",
        "complex": "claude-test-model",
    }


async def test_a_disabled_provider_is_provider_disabled(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    settings = worker()
    await record(session, settings)
    await set_enabled(session, False)
    scope = await a_scope(session)
    refused = await blocked(DbProviderGate(sessions, settings), scope.project_id)
    assert (refused.code, refused.reason) == ("PROVIDER_DISABLED", "disabled")


@pytest.mark.parametrize("value", [False, "false", 0, None, "true", 1], ids=repr)
async def test_the_kill_switch_is_provider_disabled_unless_true(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], value: object
) -> None:
    settings = worker()
    await record(session, settings)
    scope = await a_scope(session)
    await set_kill_switch(session, scope.project_id, value)
    refused = await blocked(DbProviderGate(sessions, settings), scope.project_id)
    assert (refused.code, refused.reason) == ("PROVIDER_DISABLED", "kill_switch")


async def test_an_absent_kill_switch_means_on(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    settings = worker()
    await record(session, settings)
    scope = await a_scope(session)
    await set_kill_switch(session, scope.project_id, True)
    assert await DbProviderGate(sessions, settings).check(scope.project_id)


NOT_READY: dict[str, tuple[dict[str, Any], str]] = {
    "no_report": ({"record": False}, "no_report"),
    "report_failed": ({"failed": "streaming_ok"}, "report_failed"),
    "models_changed": ({"gate_env": {"CLAUDE_MODEL_DEFAULT": "claude-other-model"}}, "models_changed"),
    "key_rotated": ({"gate_env": {"ANTHROPIC_API_KEY": OTHER_KEY}}, "key_rotated"),
    "endpoint_changed": (
        {"gate_env": {"ANTHROPIC_BASE_URL": "https://gateway.example.test"}},
        "endpoint_changed",
    ),
    "key_missing": (
        {"mode": "anthropic", "gate_env": {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ""}},
        "key_missing",
    ),
}


async def arrange_not_ready(session: AsyncSession, case: Mapping[str, Any]) -> Settings:
    recorded_with = worker(LLM_PROVIDER=case.get("mode", "scripted"))
    if case.get("record", True):
        await record(session, recorded_with, report(case.get("mode", "scripted"), failed=case.get("failed")))
    return worker(**case.get("gate_env", {}))


@pytest.mark.parametrize("case", list(NOT_READY))
async def test_a_provider_not_ready_is_provider_not_ready(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], case: str
) -> None:
    arrangement, reason = NOT_READY[case]
    settings = await arrange_not_ready(session, arrangement)
    scope = await a_scope(session)
    refused = await blocked(DbProviderGate(sessions, settings), scope.project_id)
    assert (refused.code, refused.reason) == ("provider_not_ready", reason)


async def test_readiness_order_is_provider_then_kill_switch(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    settings = worker()  # no report: unknown
    scope = await a_scope(session)
    await set_kill_switch(session, scope.project_id, False)
    refused = await blocked(DbProviderGate(sessions, settings), scope.project_id)
    assert (refused.code, refused.reason) == ("provider_not_ready", "no_report")
    await record(session, settings)
    refused = await blocked(DbProviderGate(sessions, settings), scope.project_id)
    assert (refused.code, refused.reason) == ("PROVIDER_DISABLED", "kill_switch")


async def test_the_gate_reads_the_worker_key_not_the_report_alone(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await record(session, worker())
    rotated = worker(ANTHROPIC_API_KEY=OTHER_KEY)
    row = (await session.scalars(select(ProviderConfig))).one()
    assert provider_readiness.provider_ready(row, rotated).ready  # without the worker's key: no rotation seen
    scope = await a_scope(session)
    refused = await blocked(DbProviderGate(sessions, rotated), scope.project_id)
    assert refused.reason == "key_rotated"


async def test_a_missing_project_fails_closed(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    settings = worker()
    await record(session, settings)
    refused = await blocked(DbProviderGate(sessions, settings), uuid.uuid4())
    assert (refused.code, refused.reason) == ("PROVIDER_DISABLED", "project_missing")


async def test_the_gate_reads_afresh_on_every_check(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    settings = worker()
    await record(session, settings)
    scope = await a_scope(session)
    gate = DbProviderGate(sessions, settings)
    await gate.check(scope.project_id)
    await set_kill_switch(session, scope.project_id, False)
    assert (await blocked(gate, scope.project_id)).reason == "kill_switch"
    await set_kill_switch(session, scope.project_id, True)
    await gate.check(scope.project_id)
    await set_enabled(session, False)
    assert (await blocked(gate, scope.project_id)).code == "PROVIDER_DISABLED"


# --- through the single model-call path ---------------------------------------------------------------------


def real_ports(
    fakes: Fakes, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable, settings: Settings
) -> Fakes:
    fakes.gate = DbProviderGate(sessions, settings)  # type: ignore[assignment]
    fakes.budget = GuardBudget(BudgetGuard(sessions, pricing, BudgetDefaults.from_settings(settings)))  # type: ignore[assignment]
    fakes.ledger = LedgerRecorder(UsageLedger(sessions, pricing))  # type: ignore[assignment]
    return fakes


@pytest.mark.parametrize("switch", ["disabled", "kill_switch", "not_ready"])
async def test_a_refused_gate_sends_nothing_and_records_nothing(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    pricing: PricingTable,
    switch: str,
) -> None:
    settings = worker()
    scope = await a_scope(session)
    if switch != "not_ready":
        await record(session, settings)
    if switch == "disabled":
        await set_enabled(session, False)
    if switch == "kill_switch":
        await set_kill_switch(session, scope.project_id, False)
    fakes = real_ports(Fakes(scripted(submit_turn({"summary": "s"}))), sessions, pricing, settings)
    with pytest.raises(ProviderBlocked):
        await make_runtime(fakes)._call_model(
            a_model_call(scope), fakes.providers.provider, cancel=CancelToken()
        )
    assert fakes.provider.requests == []
    assert await ledger_rows(session) == 0


@pytest.mark.parametrize("switch", ["kill_switch", "enabled"])
async def test_a_switch_flipped_between_calls_stops_the_next_one(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    pricing: PricingTable,
    switch: str,
) -> None:
    settings = worker()
    await record(session, settings)
    scope = await a_scope(session)
    fakes = real_ports(Fakes(scripted(submit_turn({"summary": "s"}))), sessions, pricing, settings)
    runtime = make_runtime(fakes)
    await runtime._call_model(a_model_call(scope), fakes.providers.provider, cancel=CancelToken())
    if switch == "kill_switch":
        await set_kill_switch(session, scope.project_id, False)
    else:
        await set_enabled(session, False)
    with pytest.raises(ProviderBlocked) as exc:
        await runtime._call_model(a_model_call(scope), fakes.providers.provider, cancel=CancelToken())
    assert exc.value.code == "PROVIDER_DISABLED"
    assert len(fakes.provider.requests) == 1
    assert await ledger_rows(session) == 1


class FlipOnBeat(FakeHeartbeat):
    """Flips the kill switch from another session when the given beat comes (before that attempt's gate)."""

    def __init__(self, fakes: Fakes, engine: AsyncEngine, project_id: uuid.UUID, on_beat: int) -> None:
        super().__init__(fakes.log)
        self.engine = engine
        self.project_id = project_id
        self.on_beat = on_beat

    async def beat(self, task_id: uuid.UUID) -> None:
        await super().beat(task_id)
        if self.calls == self.on_beat:
            async with async_sessionmaker(self.engine)() as other:
                await set_kill_switch(other, self.project_id, False)


async def test_a_switch_flipped_between_retries_stops_the_retry(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    pricing: PricingTable,
) -> None:
    settings = worker()
    await record(session, settings)
    scope = await a_scope(session)
    fakes = real_ports(
        Fakes(scripted(in_order(Fail("overloaded"), submit_turn({"summary": "s"})))),
        sessions,
        pricing,
        settings,
    )
    fakes.heartbeat = FlipOnBeat(fakes, engine, scope.project_id, on_beat=2)
    with pytest.raises(ProviderBlocked) as exc:
        await make_runtime(fakes)._call_model(
            a_model_call(scope), fakes.providers.provider, cancel=CancelToken()
        )
    assert (exc.value.code, exc.value.reason) == ("PROVIDER_DISABLED", "kill_switch")
    assert len(fakes.provider.requests) == 1
    assert await ledger_rows(session) == 1


# --- the run ends with the code (plan WP-3.1f2a Task 5) -----------------------------------------------------


async def run_task(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    project = await make_project(session)
    task = await make_task(session, project)
    await session.commit()
    await hold_lease(session, task.id)
    return project.id, task.id


async def a_run(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    pricing: PricingTable,
    settings: Settings,
    *turns: Any,
    tools: FakeTools | None = None,
) -> tuple[Composition, AgentRunResult]:
    project_id, task_id = await run_task(session)
    composed = compose(sessions, settings, pricing, scripted(*turns), tools=tools or FakeTools([]))
    result = await composed.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    return composed, result


async def test_a_disabled_provider_stops_the_run_before_any_request(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable
) -> None:
    settings = worker()
    await record(session, settings)
    await set_enabled(session, False)
    composed, result = await a_run(
        session, sessions, pricing, settings, submit_turn(OUTPUTS["ImplementOutput"])
    )
    assert (result.status, result.error_code) == ("failed", "PROVIDER_DISABLED")
    assert composed.provider.requests == []
    assert composed.providers.opened == 0
    assert await ledger_rows(session) == 0
    run_row = await session.get(AgentRun, result.agent_run_id)
    assert run_row is not None
    assert (run_row.status, run_row.error_code, run_row.model_name) == ("failed", "PROVIDER_DISABLED", None)


@pytest.mark.parametrize("value", [False, "false", 0, None], ids=repr)
async def test_the_kill_switch_stops_the_run_before_any_request(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable, value: object
) -> None:
    settings = worker()
    await record(session, settings)
    project_id, task_id = await run_task(session)
    await set_kill_switch(session, project_id, value)
    composed = compose(
        sessions, settings, pricing, scripted(submit_turn(OUTPUTS["ImplementOutput"])), tools=FakeTools([])
    )
    result = await composed.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert (result.status, result.error_code) == ("failed", "PROVIDER_DISABLED")
    assert composed.provider.requests == []
    assert await ledger_rows(session) == 0


@pytest.mark.parametrize("case", list(NOT_READY))
async def test_a_provider_not_ready_stops_the_run(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], pricing: PricingTable, case: str
) -> None:
    arrangement, _ = NOT_READY[case]
    settings = await arrange_not_ready(session, arrangement)
    composed, result = await a_run(
        session, sessions, pricing, settings, submit_turn(OUTPUTS["ImplementOutput"])
    )
    assert (result.status, result.error_code) == ("failed", "provider_not_ready")
    assert composed.provider.requests == []
    assert await ledger_rows(session) == 0


@pytest.mark.parametrize("switch", ["kill_switch", "enabled"])
async def test_a_switch_flipped_mid_run_stops_the_next_attempt(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    pricing: PricingTable,
    switch: str,
) -> None:
    settings = worker()
    await record(session, settings)
    project_id, task_id = await run_task(session)
    tools = FakeTools([])

    async def flip(ctx: Any, raw: object) -> Any:
        async with async_sessionmaker(engine)() as other:
            if switch == "kill_switch":
                await set_kill_switch(other, project_id, False)
            else:
                await set_enabled(other, False)
        return ok_result()

    tools.handlers["repo.get_tree"] = flip
    composed = compose(
        sessions,
        settings,
        pricing,
        scripted(tool_turn("repo.get_tree"), submit_turn(OUTPUTS["ImplementOutput"])),
        tools=tools,
    )
    result = await composed.runtime.run(run_request("B14", task_id=task_id, project_id=project_id))
    assert (result.status, result.error_code) == ("failed", "PROVIDER_DISABLED")
    assert len(composed.provider.requests) == 1  # no restart of the worker needed
    assert await ledger_rows(session) == 1
