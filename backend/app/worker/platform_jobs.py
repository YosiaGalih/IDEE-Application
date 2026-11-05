"""Platform jobs for worker-ide (IMPLEMENTATION_DESIGN_V2 §2: ``platform_jobs.*``).

Thin shells: each one builds its clients, runs one service function, and closes them again.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import datetime
from pathlib import Path

from celery import shared_task
from redis.asyncio import Redis

from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.redaction import Redactor
from app.gitops.adapter import GitAdapter
from app.services import approval_handlers, project_delete
from app.services.approval_registry import default_handlers
from app.services.job_queue import IMPORT_TASK, INDEX_TASK, CeleryJobQueue
from app.services.project_imports import ImportEnv, ImportRetry, run_import
from app.services.projects import ProjectEnv
from app.services.repo_index import IndexEnv, Trigger, index_soon, run_index

# A job runs well inside its lock (import_lock.LOCK_TTL_S, 45 min): its archive and lock never outlive it.
IMPORT_TIME_LIMIT_S = 40 * 60
# An index run ends well inside its lock (index_lock.LOCK_TTL_S, 30 min).
INDEX_TIME_LIMIT_S = 20 * 60


@shared_task(
    name=IMPORT_TASK,
    autoretry_for=(ImportRetry,),
    max_retries=5,
    retry_backoff=5,  # waits of up to 5, 10, 20, 40, 80 s (random, below each): Redis or the DB comes back
    retry_backoff_max=120,
    time_limit=IMPORT_TIME_LIMIT_S,
)
def import_archive(project_id: str, job_id: str, user_id: str, filename: str) -> str:
    """``ready`` / ``error`` / ``skipped``; the outcome is in the project row, so the result is not stored.
    ``ImportRetry`` (the outcome could not be decided) runs it again; after the last retry, or when the retry
    cannot be queued because Redis (also the broker) is down, the archive and lock stay until the lock
    expires, the project reads ``import_incomplete``, and the next upload clears and audits them."""
    return asyncio.run(
        run_import_job(
            project_id=uuid.UUID(project_id),
            job_id=uuid.UUID(job_id),
            user_id=uuid.UUID(user_id),
            filename=filename,
        )
    )


async def run_import_job(
    *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID, filename: str
) -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, socket_timeout=5.0, socket_connect_timeout=5.0)
    try:
        projects_env = ProjectEnv(
            sessions=make_sessionmaker(engine),
            audit=AuditWriter(Redactor(settings.secret_values())),
            git=GitAdapter(),
            workspace_root=Path(settings.workspace_root),
            redis=redis,
        )
        env = ImportEnv(projects=projects_env, redis=redis)
        outcome = await run_import(
            env, project_id=project_id, job_id=job_id, user_id=user_id, filename=filename
        )
        if outcome == "ready":
            # UC-02: import, then index. Best effort: the import's outcome is already decided.
            jobs = CeleryJobQueue(settings.redis_url)
            await index_soon(IndexEnv(projects_env, redis, jobs), project_id, user_id, trigger="import")
        return outcome
    finally:
        await redis.aclose()
        await engine.dispose()


@shared_task(name=INDEX_TASK, time_limit=INDEX_TIME_LIMIT_S)
def index_repository(project_id: str, job_id: str, user_id: str, trigger: Trigger) -> str:
    """``indexed`` / ``unchanged`` / ``skipped`` / ``error``. No retry: a failed run is audited, the old index
    stays, and the user can ask again."""
    return asyncio.run(
        run_index_job(
            project_id=uuid.UUID(project_id),
            job_id=uuid.UUID(job_id),
            user_id=uuid.UUID(user_id),
            trigger=trigger,
        )
    )


async def run_index_job(
    *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID, trigger: Trigger
) -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, socket_timeout=5.0, socket_connect_timeout=5.0)
    try:
        projects_env = ProjectEnv(
            sessions=make_sessionmaker(engine),
            audit=AuditWriter(Redactor(settings.secret_values())),
            git=GitAdapter(),
            workspace_root=Path(settings.workspace_root),
            redis=redis,
        )
        env = IndexEnv(projects=projects_env, redis=redis, jobs=CeleryJobQueue(settings.redis_url))
        return await run_index(env, project_id=project_id, job_id=job_id, user_id=user_id, trigger=trigger)
    finally:
        await redis.aclose()
        await engine.dispose()


# --- approvals (WP-3.1c) -----------------------------------------------------------------------------------

APPROVALS_SWEEP_TASK = "app.worker.platform_jobs.approvals_sweep"
PROJECTS_PURGE_TASK = "app.worker.platform_jobs.projects_purge"
APPROVALS_SWEEP_SECONDS = 60.0
PROJECTS_PURGE_SECONDS = 3600.0


@shared_task(name=APPROVALS_SWEEP_TASK, time_limit=int(APPROVALS_SWEEP_SECONDS) - 10)
def approvals_sweep() -> dict[str, int]:
    """Expire what is past its time and rerun due handlers (ADR-024). A handler failure is recorded on the
    approval, not raised here: one stuck approval must not stop the others."""
    return asyncio.run(run_approvals_sweep())


async def run_approvals_sweep(*, now: datetime | None = None) -> dict[str, int]:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    try:
        sessions = make_sessionmaker(engine)
        audit = AuditWriter(Redactor(settings.secret_values()))
        result = await approval_handlers.sweep(
            sessions=sessions, audit=audit, handlers=default_handlers(sessions=sessions, audit=audit), now=now
        )
    finally:
        await engine.dispose()
    return dataclasses.asdict(result)


@shared_task(name=PROJECTS_PURGE_TASK, time_limit=int(PROJECTS_PURGE_SECONDS) - 60)
def projects_purge() -> list[str]:
    """Projects deleted 7 days ago or more: workspace, grants, rows (ADR-005). Runs where the workspace volume
    is mounted (the worker, not the scheduler)."""
    return asyncio.run(run_projects_purge())


async def run_projects_purge(*, now: datetime | None = None) -> list[str]:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    try:
        purged = await project_delete.purge_deleted(
            sessions=make_sessionmaker(engine),
            audit=AuditWriter(Redactor(settings.secret_values())),
            workspace_root=Path(settings.workspace_root),
            now=now,
        )
    finally:
        await engine.dispose()
    return [str(project_id) for project_id in purged]
