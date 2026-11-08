"""Provider readiness service (spec 3.1e Goals 1, 6; Lead decisions 3.1e #4, #5, #8): the row the worker
writes, and the predicates 3.1f enforces."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.audit import AuditWriter
from app.core.config import Settings, load_settings
from app.core.redaction import Redactor
from app.llm.readiness import CHECK_KEYS, Check, ReadinessReport
from app.models.audit import AuditEvent
from app.models.projects import Project
from app.models.providers import ProviderConfig
from app.services import provider_readiness as service
from tests.conftest import make_test_env, reset_data

KEY = "sk-ant-" + "service-" + "y" * 24 + "a1b2"  # built, not written: a test value
OTHER_KEY = "sk-ant-" + "rotated-" + "z" * 24 + "c3d4"
AUDIT = AuditWriter(Redactor())  # no literal: a leak would show, the service must not rely on it
RAN_AT = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


def configured(**env: str) -> Settings:
    return load_settings(make_test_env(**env))


def report(mode: str = "scripted", *, failed: str | None = None) -> ReadinessReport:
    checks = tuple(Check(key, "failed" if key == failed else "passed", f"{key} detail") for key in CHECK_KEYS)
    return ReadinessReport(provider="anthropic", mode=mode, checks=checks, ran_at=RAN_AT)


async def record(session: AsyncSession, settings: Settings, rep: ReadinessReport, key: str | None = KEY,
                 **extra: Any) -> ProviderConfig:  # fmt: skip
    models = service.effective_models(settings, {})
    row = await service.record_report(
        session, rep, key=key, base_url=extra.get("base_url"), pricing_version="2026-11-08", models=models,
        audit=AUDIT, actor_id="cli",
    )  # fmt: skip
    await session.commit()
    return row


async def test_ensure_config_creates_one_row_and_is_idempotent(session: AsyncSession) -> None:
    first = await service.ensure_config(session)
    second = await service.ensure_config(session)
    await session.commit()
    assert first.id == second.id and first.provider == "anthropic"
    assert len((await session.scalars(select(ProviderConfig))).all()) == 1


async def test_a_recorded_report_carries_the_worker_view_of_the_key_but_never_the_key(
    session: AsyncSession, settings: Settings
) -> None:
    row = await record(session, settings, report(), base_url="https://api.example.test")
    await session.refresh(row)
    assert row.readiness_status == "passed" and row.llm_provider_mode == "scripted"
    assert row.readiness_checked_at == RAN_AT and row.pricing_version == "2026-11-08"
    assert row.base_url == "https://api.example.test"
    assert row.secret_status == "configured" and row.secret_checked_at is not None
    assert row.secret_hint == "sk-ant-…a1b2"
    assert row.secret_fingerprint == hashlib.sha256(KEY.encode()).hexdigest()[:16]
    assert row.readiness_report["checks"] == report().to_dict()["checks"]
    assert row.readiness_report["models"] == {"default": "claude-test-model", "fast": "claude-test-model",
                                              "complex": "claude-test-model"}  # fmt: skip
    events = await session.scalars(
        select(AuditEvent).where(AuditEvent.action == "provider.readiness_recorded")
    )
    event = events.one()
    assert (event.actor_type, event.actor_id, event.result) == ("system", "cli", "success")
    assert event.metadata_["ready"] is True and event.metadata_["mode"] == "scripted"
    stored = json.dumps([row.readiness_report, event.metadata_, row.secret_hint, row.secret_fingerprint])
    assert KEY not in stored and KEY[7:-4] not in stored


async def test_a_worker_without_a_key_records_it_missing(session: AsyncSession, settings: Settings) -> None:
    row = await record(session, settings, report("anthropic", failed="api_key_valid"), key=None)
    assert (row.secret_status, row.secret_hint, row.secret_fingerprint) == ("missing", None, None)
    assert row.readiness_status == "failed"


async def test_a_short_key_gets_no_hint_that_would_reveal_most_of_it(
    session: AsyncSession, settings: Settings
) -> None:
    row = await record(session, settings, report(), key="sk-ant-short1")
    assert row.secret_hint == "…" and row.secret_status == "configured"


async def test_ready_only_for_a_passed_report_of_the_current_mode_models_and_key(
    session: AsyncSession,
) -> None:
    anthropic = configured(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY)
    assert service.provider_ready(None, anthropic).state == "unknown"
    row = await record(session, anthropic, report("anthropic"))
    verdict = service.provider_ready(row, anthropic, key=KEY)
    assert (verdict.state, verdict.reason, verdict.ready) == ("ready", "passed", True)
    assert service.provider_ready(row, anthropic).ready  # the api process holds no key and does not compare


async def test_a_report_from_another_mode_never_counts(session: AsyncSession) -> None:
    """Mutant E3."""
    scripted = await record(session, configured(), report("scripted"))
    verdict = service.provider_ready(scripted, configured(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY))
    assert (verdict.state, verdict.reason) == ("unknown", "other_mode")


async def test_a_failed_report_is_not_ready(session: AsyncSession, settings: Settings) -> None:
    row = await record(session, settings, report(failed="streaming_ok"))
    assert (service.provider_ready(row, settings).state, service.provider_ready(row, settings).reason) == (
        "not_ready", "report_failed",
    )  # fmt: skip


async def test_other_models_than_the_report_tested_are_not_ready(
    session: AsyncSession, settings: Settings
) -> None:
    """ADR-032: a model change resets readiness, whether it came from the policy or from CLAUDE_MODEL_*."""
    row = await record(session, settings, report())
    moved = configured(CLAUDE_MODEL_FAST="claude-test-fast")
    assert (service.provider_ready(row, moved).state, service.provider_ready(row, moved).reason) == (
        "unknown", "models_changed",
    )  # fmt: skip
    row.model_policy = {"complex": "claude-test-complex"}
    assert service.provider_ready(row, settings).reason == "models_changed"


async def test_changing_the_model_policy_resets_readiness(session: AsyncSession, settings: Settings) -> None:
    """Mutant E4 (the PATCH path uses this)."""
    row = await record(session, settings, report())
    assert service.set_model_policy(row, {"fast": "claude-test-fast"}) is True
    assert row.readiness_status == "unknown" and row.model_policy == {"fast": "claude-test-fast"}
    row2 = await record(session, settings, report())
    assert service.set_model_policy(row2, dict(row2.model_policy)) is False  # unchanged: readiness kept
    assert row2.readiness_status == "passed"


async def test_a_rotated_key_is_not_ready_in_the_worker(session: AsyncSession) -> None:
    """Mutant E5: the fingerprint of the key the worker holds now differs from the one that passed."""
    anthropic = configured(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=OTHER_KEY)
    row = await record(session, anthropic, report("anthropic"), key=KEY)
    verdict = service.provider_ready(row, anthropic, key=OTHER_KEY)
    assert (verdict.state, verdict.reason) == ("unknown", "key_rotated")


async def test_a_disabled_provider_is_disabled_whatever_its_report(
    session: AsyncSession, settings: Settings
) -> None:
    row = await record(session, settings, report())
    row.enabled = False
    assert service.provider_ready(row, settings).state == "disabled"
    relaxed = configured(LLM_REQUIRE_READINESS="false")
    assert service.provider_ready(row, relaxed).state == "disabled"


async def test_readiness_not_required_makes_it_ready_without_a_report(session: AsyncSession) -> None:
    relaxed = configured(LLM_REQUIRE_READINESS="false")
    verdict = service.provider_ready(await service.ensure_config(session), relaxed)
    assert (verdict.state, verdict.reason) == ("ready", "readiness_not_required")
    assert service.provider_ready(None, relaxed).reason == "readiness_not_required"


@pytest.mark.parametrize(
    ("settings_value", "allowed"),
    [({}, True), ({"provider_calls_enabled": True}, True), ({"provider_calls_enabled": False}, False),
     ({"provider_calls_enabled": "false"}, False), ({"provider_calls_enabled": 0}, False)],
)  # fmt: skip
def test_provider_calls_allowed_follows_the_kill_switch_and_fails_closed(
    settings_value: dict[str, Any], allowed: bool
) -> None:
    """FR-158, mutant E9."""
    assert service.provider_calls_allowed(Project(name="p", settings=settings_value)) is allowed


def test_resolve_model_takes_the_policy_then_the_environment(settings: Settings) -> None:
    moved = configured(CLAUDE_MODEL_FAST="claude-env-fast")
    assert service.resolve_model("fast", moved, {}) == "claude-env-fast"
    assert service.resolve_model("fast", moved, {"fast": "claude-policy-fast"}) == "claude-policy-fast"
    assert service.resolve_model("complex", moved, {"fast": "claude-policy-fast"}) == "claude-test-model"
    with pytest.raises(ValueError, match="alias"):
        service.resolve_model("huge", settings, {})


@pytest.mark.parametrize(
    "policy",
    [{"turbo": "claude-x"}, {"fast": "gpt-4o"}, {"fast": "claude-"}, {"fast": "claude-x y"},
     {"fast": "claude-" + "a" * 100}, {"fast": 3}, {"fast": "CLAUDE-X"}],
)  # fmt: skip
def test_model_policy_validation_refuses_what_is_not_a_claude_model_id(policy: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        service.validate_model_policy(policy)


def test_model_policy_validation_keeps_ids_and_drops_empty_aliases() -> None:
    raw = {"default": "claude-sonnet-5-5", "fast": "", "complex": None}
    assert service.validate_model_policy(raw) == {"default": "claude-sonnet-5-5"}
    assert service.validate_model_policy({"fast": "claude-haiku-5-5-20261001"}) == {
        "fast": "claude-haiku-5-5-20261001"
    }
