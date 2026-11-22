"""Celery worker configuration (ARCHITECTURE §2, §5.3): JSON only, late ack, recovery every 30 s."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import ConfigError, load_settings
from app.worker.celery_app import create_celery
from tests.conftest import make_test_env


def test_worker_configuration_is_safe() -> None:
    app = create_celery(load_settings(make_test_env()))
    conf = app.conf
    assert conf.task_serializer == "json"
    assert conf.accept_content == ["json"]  # never pickle
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_default_queue == "ide"
    assert conf.task_ignore_result is True
    assert conf.task_always_eager is False
    schedule = conf.beat_schedule["recovery-sweep"]
    assert schedule["task"] == "app.worker.jobs.recovery_sweep"
    assert schedule["schedule"] == 30.0
    assert schedule["options"]["expires"] < 30  # a backlog of sweeps is dropped, not replayed in a burst
    assert conf.task_eager_propagates is True
    assert conf.worker_hijack_root_logger is False


def test_eager_mode_follows_settings() -> None:
    app = create_celery(load_settings(make_test_env(CELERY_TASK_ALWAYS_EAGER="true")))
    assert app.conf.task_always_eager is True


def test_recovery_job_is_registered() -> None:
    app = create_celery(load_settings(make_test_env()))
    app.loader.import_default_modules()
    assert "app.worker.jobs.recovery_sweep" in app.tasks


def test_worker_logging_is_redacted(capsys) -> None:  # type: ignore[no-untyped-def]
    """Review WP-1.1f CRITICAL: worker and CLI logs bypassed the redactor."""
    from app.core.logging import get_logger
    from app.worker.celery_app import configure_worker_logging
    from tests.conftest import TEST_SECRET_KEY

    configure_worker_logging(load_settings(make_test_env()))
    get_logger("worker.test").error("probe", leaked=TEST_SECRET_KEY)
    out = capsys.readouterr().out
    assert TEST_SECRET_KEY not in out and '"probe"' in out


def test_import_job_is_registered_with_retries_and_a_time_limit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import uuid

    from app.services.import_lock import LOCK_TTL_S
    from app.services.job_queue import IMPORT_TASK, CeleryJobQueue
    from app.services.project_imports import ImportRetry
    from app.worker import platform_jobs

    app = create_celery(load_settings(make_test_env()))
    app.loader.import_default_modules()
    task = app.tasks[IMPORT_TASK]
    assert tuple(task.autoretry_for) == (ImportRetry,)  # only "outcome unknown" is retried
    assert task.max_retries == 5
    assert task.time_limit is not None and task.time_limit < LOCK_TTL_S  # a job never outlives its lock

    seen: dict[str, object] = {}

    async def fake(**kwargs: object) -> str:
        seen.update(kwargs)
        return "ready"

    monkeypatch.setattr(platform_jobs, "run_import_job", fake)
    ids = {name: uuid.uuid4() for name in ("project_id", "job_id", "user_id")}
    assert task.run(**{name: str(value) for name, value in ids.items()}, filename="x.zip") == "ready"
    assert seen == {**ids, "filename": "x.zip"}

    # The API side: a broker that stops answering fails the send instead of holding a thread forever.
    options = CeleryJobQueue("redis://localhost:6379/0")._celery.conf.broker_transport_options
    assert options["socket_timeout"] > 0 and options["socket_connect_timeout"] > 0


def test_approvals_sweep_and_purge_are_scheduled() -> None:
    from app.worker import platform_jobs

    conf = create_celery(load_settings(make_test_env())).conf
    sweep = conf.beat_schedule["approvals-sweep"]
    assert (sweep["task"], sweep["schedule"]) == (platform_jobs.APPROVALS_SWEEP_TASK, 60.0)
    assert sweep["options"]["expires"] < 60.0  # a late sweep is dropped, not replayed in a burst
    purge = conf.beat_schedule["projects-purge"]
    assert (purge["task"], purge["schedule"]) == (platform_jobs.PROJECTS_PURGE_TASK, 3600.0)
    assert purge["options"]["expires"] < 3600.0


# --- the pricing table is loaded when a worker is built (WP-3.1f1 Task 5; Keputusan Lead #10) ---

BROKEN_PRICING = {
    "missing": None,
    "broken YAML": "version: [unclosed",
    "a zero price": (Path(__file__).resolve().parents[2] / "config" / "pricing.yaml")
    .read_text(encoding="utf-8")
    .replace('input: "4"', 'input: "0"'),
}
REPO_PRICING = Path(__file__).resolve().parents[2] / "config" / "pricing.yaml"


def test_pricing_step_is_part_of_the_worker() -> None:
    from app.worker.pricing import PRICING_PATH_CONF, PricingStep

    settings = load_settings(make_test_env(PRICING_PATH="/srv/pricing.yaml"))
    app = create_celery(settings)
    assert PricingStep in app.steps["worker"]
    assert app.conf[PRICING_PATH_CONF] == settings.pricing_path == "/srv/pricing.yaml"


def test_pricing_step_holds_the_table(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from app.worker import pricing
    from app.worker.pricing import PricingNotLoaded, PricingStep, worker_pricing

    monkeypatch.setattr(pricing, "_held", None)
    with pytest.raises(PricingNotLoaded):
        worker_pricing()
    app = create_celery(load_settings(make_test_env(PRICING_PATH=str(REPO_PRICING))))
    PricingStep(SimpleNamespace(app=app))
    assert worker_pricing().version == "2026-11-05"


@pytest.mark.parametrize("case", list(BROKEN_PRICING))
def test_building_a_worker_fails_on_a_broken_pricing_file(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
    case: str,
) -> None:
    from app.llm.pricing import PricingError
    from app.worker import pricing

    monkeypatch.setattr(pricing, "_held", None)
    path = tmp_path / "pricing.yaml"
    content = BROKEN_PRICING[case]
    if content is not None:
        path.write_text(content, encoding="utf-8")
    app = create_celery(load_settings(make_test_env(PRICING_PATH=str(path))))
    # The steps' __init__ run before any include/create, so this never reaches the broker.
    with pytest.raises(PricingError):
        app.WorkController(app=app, pool_cls="solo")
    assert pricing._held is None  # pyright: ignore[reportPrivateUsage]


def test_signal_receivers_cannot_stop_the_worker() -> None:
    """Why a bootstep and not a signal: Celery's Signal.send catches what a receiver raises, logs it and
    returns it, so a worker_init receiver that fails to load prices would let the worker serve anyway."""
    from celery import signals

    def broken(**_: object) -> None:
        raise RuntimeError("pricing failed")

    signals.worker_init.connect(broken, weak=False)
    try:
        responses = signals.worker_init.send(sender=None)
    finally:
        signals.worker_init.disconnect(broken)
    assert any(isinstance(result, RuntimeError) for _, result in responses)


@pytest.mark.parametrize(("verify", "level"), [(True, "warning"), (False, "info")])
def test_unverified_prices_are_logged_as_a_warning(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    verify: bool,
    level: str,
) -> None:
    """Review L1: a table still marked verify_before_production must stand out in the worker's start log."""
    import json

    from app.worker import pricing
    from app.worker.celery_app import configure_worker_logging

    monkeypatch.setattr(pricing, "_held", None)
    path = tmp_path / "pricing.yaml"
    content = REPO_PRICING.read_text(encoding="utf-8")
    path.write_text(
        content.replace("verify_before_production: true", f"verify_before_production: {str(verify).lower()}"),
        encoding="utf-8",
    )
    configure_worker_logging(load_settings(make_test_env()))
    pricing.hold_pricing(path)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if "pricing_loaded" in line]
    assert [(event["level"], event["verify_before_production"]) for event in events] == [(level, verify)]


def test_visibility_timeout_and_dispatch_sweep_beat() -> None:
    conf = create_celery(load_settings(make_test_env())).conf
    assert conf.broker_transport_options == {"visibility_timeout": 7200}  # merged, not replacing
    schedule = conf.beat_schedule["dispatch-sweep"]
    assert schedule["task"] == "app.worker.jobs.dispatch_sweep"
    assert schedule["schedule"] == 30.0
    assert schedule["options"]["expires"] == 25.0
    assert "app.worker.jobs.run_task" in _registered()


def _registered() -> set[str]:
    app = create_celery(load_settings(make_test_env()))
    app.loader.import_default_modules()
    return set(app.tasks)


# --- the agent registry is loaded when a worker is built (WP-3.2c2a Task 3; spec §16.3 #68) ---

REPO_REGISTRY = Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml"


def test_registry_step_is_part_of_the_worker() -> None:
    from app.worker.registry import REGISTRY_PATH_CONF, RegistryStep

    settings = load_settings(make_test_env(AGENT_REGISTRY_PATH="/srv/registry.yaml"))
    app = create_celery(settings)
    assert RegistryStep in app.steps["worker"]
    assert app.conf[REGISTRY_PATH_CONF] == settings.agent_registry_path == "/srv/registry.yaml"


def test_registry_is_not_loaded_until_held(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.worker import registry

    monkeypatch.setattr(registry, "_held", None)
    with pytest.raises(registry.RegistryNotLoaded):
        registry.worker_registry()


def test_hold_registry_keeps_and_logs_the_count(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    import json

    from app.worker import registry
    from app.worker.celery_app import configure_worker_logging

    monkeypatch.setattr(registry, "_held", None)
    configure_worker_logging(load_settings(make_test_env()))
    held = registry.hold_registry(REPO_REGISTRY)
    assert registry.worker_registry() is held
    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if "agent_registry_loaded" in line
    ]
    assert [event["agents"] for event in events] == [len(held.agents)]


def _write_broken(path: Path, case: str) -> None:
    if case == "bad YAML":
        path.write_text("agents: [unclosed", encoding="utf-8")
    elif case == "rule violation":
        path.write_text("schema_version: 99\nagents: []\n", encoding="utf-8")
    else:
        path.write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\x28")


@pytest.mark.parametrize(
    ("case", "error_type", "has_count"),
    [
        ("bad YAML", "RegistryError", True),
        ("rule violation", "RegistryError", True),
        ("non-UTF-8", "UnicodeDecodeError", False),
    ],
)
def test_a_broken_registry_is_logged_by_type_and_re_raised(
    tmp_path: Path,
    monkeypatch,
    capsys,
    case: str,
    error_type: str,
    has_count: bool,  # type: ignore[no-untyped-def]
) -> None:
    import json

    from app.worker import registry
    from app.worker.celery_app import configure_worker_logging

    monkeypatch.setattr(registry, "_held", None)
    path = tmp_path / "registry.yaml"
    _write_broken(path, case)
    configure_worker_logging(load_settings(make_test_env()))
    with pytest.raises(Exception) as raised:
        registry.hold_registry(path)
    assert type(raised.value).__name__ == error_type
    assert registry._held is None  # pyright: ignore[reportPrivateUsage]
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if "agent_registry_invalid" in line]
    assert len(events) == 1
    event = events[0]
    assert event["error_type"] == error_type
    assert ("problems_count" in event) is has_count
    assert "exc_info" not in event and "exception" not in event
    assert "unclosed" not in out and "problem(s)" not in out


def test_startup_recovery_failure_logs_the_error_type_only(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from structlog.testing import capture_logs

    from app.worker import jobs
    from app.worker.celery_app import _recover_on_start

    async def boom() -> dict[str, int]:
        raise RuntimeError("secret-host")

    monkeypatch.setattr(jobs, "run_recovery_sweep", boom)
    with capture_logs() as logs:
        _recover_on_start()
    entries = [e for e in logs if e["event"] == "startup_recovery_failed"]
    assert len(entries) == 1
    assert entries[0]["error_type"] == "RuntimeError"
    assert "exc_info" not in entries[0]
    assert "secret-host" not in str(entries[0])


def test_delivery_limit_of_110_minutes_is_accepted() -> None:
    """110 min + the 600 s margin = the 7200 s visibility timeout."""
    create_celery(load_settings(make_test_env(TASK_DELIVERY_MAX_MINUTES="110")))


def test_delivery_limit_past_the_visibility_margin_is_refused_without_its_value() -> None:
    with pytest.raises(ConfigError) as refused:
        create_celery(load_settings(make_test_env(TASK_DELIVERY_MAX_MINUTES="111")))
    text = str(refused.value)
    assert "TASK_DELIVERY_MAX_MINUTES" in text
    assert "111" not in text and "6660" not in text
