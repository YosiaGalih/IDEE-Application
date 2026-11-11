"""``claude-smoke`` and the worker's smoke job (spec 3.1e Goals 3, 4; RUNBOOK §4): run the gate, record the
report, print it redacted, one smoke per provider at a time, exit 0 only when ready."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from redis import Redis as SyncRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.cli import SMOKE_BUSY
from app.cli import main as cli_main
from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.redaction import Redactor
from app.models.audit import AuditEvent
from app.models.providers import ProviderConfig
from app.services import provider_smoke, smoke_lock
from app.worker.platform_jobs import run_provider_smoke_job
from tests.conftest import make_test_env, reset_data
from tests.unit.test_llm_readiness import fake_api

KEY = "sk-ant-" + "cli-smoke-" + "q" * 24 + "e5f6"  # built, not written: a test value
LOCK = smoke_lock.lock_key("anthropic")
PRICING = """
version: "2026-11-08-test"
currency: USD
source: https://example.test/pricing
verify_before_production: true
models:
  claude-test-model: {input: "3", output: "15", cache_write: "3.75", cache_read: "0.30"}
"""


@pytest.fixture(autouse=True)
def pricing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The shipped table has no price for the test model (pricing_verified would rightly fail)."""
    path = tmp_path / "pricing.yaml"
    path.write_text(PRICING, encoding="utf-8")
    monkeypatch.setenv("KK_TEST_PRICING_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def redis() -> Iterator[SyncRedis]:
    client = SyncRedis.from_url(make_test_env()["REDIS_URL"])
    client.delete(LOCK)
    yield client
    client.delete(LOCK)
    client.close()


def smoke_env(**overrides: str) -> dict[str, str]:
    import os

    return make_test_env(PRICING_PATH=os.environ["KK_TEST_PRICING_PATH"], **overrides)


def use_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for key, value in smoke_env(**overrides).items():
        monkeypatch.setenv(key, value)


def stored() -> tuple[ProviderConfig | None, list[AuditEvent]]:
    async def read() -> tuple[ProviderConfig | None, list[AuditEvent]]:
        engine = create_async_engine(make_test_env()["DATABASE_URL"])
        try:
            async with async_sessionmaker(engine)() as session:
                row = (await session.scalars(select(ProviderConfig))).one_or_none()
                if row is None:
                    return None, []
                # audit_events is append-only (kept across tests): this row's events only
                mine = select(AuditEvent).where(AuditEvent.resource_id == str(row.id)).order_by(AuditEvent.id)
                return row, list((await session.scalars(mine)).all())
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_scripted_smoke_passes_records_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, redis: SyncRedis, capsys: pytest.CaptureFixture[str]
) -> None:
    use_env(monkeypatch)
    assert cli_main(["claude-smoke"]) == 0
    out = capsys.readouterr().out
    assert "mode scripted" in out and "ready: yes" in out and "streaming_ok" in out
    row, events = stored()
    assert row is not None and row.readiness_status == "passed" and row.llm_provider_mode == "scripted"
    assert row.readiness_report["job_id"] is not None
    assert [e.actor_id for e in events if e.action == "provider.readiness_recorded"] == ["cli"]
    assert redis.get(LOCK) is None  # released


def test_json_output_is_the_contract_shape(
    monkeypatch: pytest.MonkeyPatch, redis: SyncRedis, capsys: pytest.CaptureFixture[str]
) -> None:
    use_env(monkeypatch)
    assert cli_main(["claude-smoke", "--json"]) == 0
    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert set(data) == {"provider", "mode", "ready", "checks", "ran_at"} and data["ready"] is True


def test_a_worker_without_the_key_records_it_missing_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, redis: SyncRedis, capsys: pytest.CaptureFixture[str]
) -> None:
    use_env(monkeypatch, LLM_PROVIDER="anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli_main(["claude-smoke"]) == 1
    assert "api_key_valid" in capsys.readouterr().out
    row, _ = stored()
    assert row is not None and (row.readiness_status, row.secret_status) == ("failed", "missing")
    assert row.llm_provider_mode == "anthropic"


def test_a_running_smoke_blocks_another(
    monkeypatch: pytest.MonkeyPatch, redis: SyncRedis, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutant E11: two smokes of one provider never run together (CLI and API share the lock)."""
    use_env(monkeypatch)
    redis.set(LOCK, str(uuid.uuid4()), ex=60)
    assert cli_main(["claude-smoke"]) == SMOKE_BUSY
    assert "already running" in capsys.readouterr().err
    assert stored()[0] is None  # nothing ran


async def test_the_anthropic_adapter_with_a_refused_key_leaks_it_nowhere(
    monkeypatch: pytest.MonkeyPatch, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = load_settings(smoke_env(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = provider_smoke.SmokeEnv(
        settings=settings, sessions=sessions, audit=AuditWriter(Redactor()),
        transport=fake_api(refuse=True).transport(),
    )  # fmt: skip
    report = await provider_smoke.run_smoke(env, actor_id="cli", job_id=uuid.uuid4())
    assert report.ready is False
    async with sessions() as session:
        row = (await session.scalars(select(ProviderConfig))).one()
        events = (
            await session.scalars(select(AuditEvent).where(AuditEvent.resource_id == str(row.id)))
        ).all()
    assert row.secret_status == "configured" and row.secret_hint == "sk-ant-…e5f6"
    captured = capsys.readouterr()
    everything = json.dumps(
        [row.readiness_report, [e.metadata_ for e in events], provider_smoke.render(report)]
    )
    assert KEY not in everything + captured.out + captured.err


async def test_the_worker_job_runs_only_for_the_lock_it_was_given(
    engine: AsyncEngine, monkeypatch: Any
) -> None:
    from redis.asyncio import Redis

    use_env(monkeypatch)
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    try:
        job_id = uuid.uuid4()
        await redis.set(LOCK, str(uuid.uuid4()), ex=60)  # another run holds it
        assert await run_provider_smoke_job(job_id=job_id, provider="anthropic") == "skipped"
        async with async_sessionmaker(engine)() as session:
            assert (await session.scalars(select(ProviderConfig))).one_or_none() is None
        await redis.set(LOCK, str(job_id), ex=60)
        assert await run_provider_smoke_job(job_id=job_id, provider="anthropic") == "ready"
        assert await redis.get(LOCK) is None
        async with async_sessionmaker(engine)() as session:
            row = (await session.scalars(select(ProviderConfig))).one()
        assert row.readiness_report["job_id"] == str(job_id)
        with pytest.raises(ValueError, match="unknown provider"):
            await run_provider_smoke_job(job_id=job_id, provider="openai")
    finally:
        await redis.delete(LOCK)
        await redis.aclose()


# --- review fixes (silent-failure 3.1e1 M2-M5) -------------------------------------------------------


async def test_a_skipped_job_leaves_an_audit_event(engine: AsyncEngine, monkeypatch: Any) -> None:
    from redis.asyncio import Redis

    use_env(monkeypatch)
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    job_id = uuid.uuid4()
    try:
        assert (
            await run_provider_smoke_job(job_id=job_id, provider="anthropic") == "skipped"
        )  # no lock at all
    finally:
        await redis.delete(LOCK)
        await redis.aclose()
    async with async_sessionmaker(engine)() as session:
        query = (
            select(AuditEvent)
            .where(AuditEvent.action == "provider.smoke_skipped")
            .order_by(AuditEvent.id.desc())
        )
        event = (await session.scalars(query)).first()
    assert event is not None and event.metadata_["job_id"] == str(job_id) and event.actor_id == "worker"


async def test_the_job_extends_the_lock_it_was_given(monkeypatch: Any) -> None:
    from redis.asyncio import Redis

    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    job_id = uuid.uuid4()
    try:
        await redis.set(LOCK, str(job_id), ex=5)
        assert await smoke_lock.claim(redis, "anthropic", job_id) is True
        assert await redis.ttl(LOCK) > 5
        assert await smoke_lock.claim(redis, "anthropic", uuid.uuid4()) is False
    finally:
        await redis.delete(LOCK)
        await redis.aclose()


async def _smoke(engine: AsyncEngine) -> tuple[Any, ProviderConfig]:
    settings = load_settings(smoke_env())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = provider_smoke.SmokeEnv(settings=settings, sessions=sessions, audit=AuditWriter(Redactor()))
    report = await provider_smoke.run_smoke(env, actor_id="worker", job_id=uuid.uuid4())
    async with sessions() as session:
        return report, (await session.scalars(select(ProviderConfig))).one()


async def test_a_provider_that_cannot_be_built_records_a_failed_report(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError(f"bad base url with {KEY}")

    monkeypatch.setattr(provider_smoke, "build_provider", broken)
    report, row = await _smoke(engine)
    assert report.ready is False and row.readiness_status == "failed"
    api_key_valid = next(c for c in row.readiness_report["checks"] if c["key"] == "api_key_valid")
    assert "RuntimeError" in api_key_valid["detail"] and KEY not in json.dumps(row.readiness_report)


async def test_a_smoke_that_runs_too_long_records_a_failed_report(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hang(*_: Any, **__: Any) -> Any:
        await asyncio.sleep(30)

    monkeypatch.setattr(provider_smoke, "SMOKE_RUN_LIMIT_S", 0.05)
    monkeypatch.setattr(provider_smoke, "run_readiness", hang)
    report, row = await _smoke(engine)
    assert report.ready is False and row.readiness_status == "failed"
    assert any("did not finish" in c["detail"] for c in row.readiness_report["checks"])


def test_the_cli_says_when_it_could_not_run(
    monkeypatch: pytest.MonkeyPatch, redis: SyncRedis, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silent-failure 3.1e1 M4: exit 1 is "not ready", never "the tool broke"."""

    async def down(*_: Any, **__: Any) -> Any:
        raise ConnectionError("database unreachable")

    use_env(monkeypatch)
    monkeypatch.setattr(provider_smoke, "run_smoke", down)
    assert cli_main(["claude-smoke"]) == 2
    assert "could not run" in capsys.readouterr().err and redis.get(LOCK) is None
