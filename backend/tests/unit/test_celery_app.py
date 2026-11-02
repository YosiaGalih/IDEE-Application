"""Celery worker configuration (ARCHITECTURE §2, §5.3): JSON only, late ack, recovery every 30 s."""

from __future__ import annotations

from app.core.config import load_settings
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
