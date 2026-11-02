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
from app.services.project_imports import ImportEnv, run_import
from app.services.projects import ProjectEnv


@shared_task(name=IMPORT_TASK)
def import_archive(project_id: str, job_id: str, user_id: str, filename: str) -> str:
    """``ready`` / ``error`` / ``skipped``; the outcome is in the project row, so the result is not stored."""
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
