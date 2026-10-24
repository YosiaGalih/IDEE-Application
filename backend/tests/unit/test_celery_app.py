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


def test_eager_mode_follows_settings() -> None:
    app = create_celery(load_settings(make_test_env(CELERY_TASK_ALWAYS_EAGER="true")))
    assert app.conf.task_always_eager is True


def test_recovery_job_is_registered() -> None:
    app = create_celery(load_settings(make_test_env()))
    app.loader.import_default_modules()
    assert "app.worker.jobs.recovery_sweep" in app.tasks
