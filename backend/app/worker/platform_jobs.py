"""Platform jobs for worker-ide (IMPLEMENTATION_DESIGN_V2 §2: ``platform_jobs.*``).

Thin shells: each one builds its clients, runs one service function, and closes them again.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from celery import shared_task
from redis.asyncio import Redis

from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.redaction import Redactor
from app.gitops.adapter import GitAdapter
from app.services.job_queue import IMPORT_TASK
from app.services.project_imports import ImportEnv, ImportRetry, run_import
from app.services.projects import ProjectEnv

# A job runs well inside its lock (import_lock.LOCK_TTL_S, 45 min): its archive and lock never outlive it.
IMPORT_TIME_LIMIT_S = 40 * 60


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
        return await run_import(env, project_id=project_id, job_id=job_id, user_id=user_id, filename=filename)
    finally:
        await redis.aclose()
        await engine.dispose()
