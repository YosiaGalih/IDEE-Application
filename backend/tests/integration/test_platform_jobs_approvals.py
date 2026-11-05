"""The worker's approval sweep and project purge jobs (spec WP-3.1c Goals 4 and 5): thin shells around the
services, using the worker's own settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.models.approvals import Approval
from app.models.projects import Project
from app.services import approval_store
from app.worker import platform_jobs
from tests.conftest import make_test_env, reset_data
from tests.integration.test_task_schema import make_project

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
async def worker_env(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    await reset_data(engine)
    root = tmp_path / "workspaces"
    root.mkdir()
    for key, value in make_test_env(WORKSPACE_ROOT=str(root)).items():
        monkeypatch.setenv(key, value)
    return root


async def test_sweep_job_expires_and_restores(engine: AsyncEngine) -> None:
    async with make_sessionmaker(engine)() as session:
        project = await make_project(session)
        project.status = "deleting_pending_approval"
        approval = await approval_store.create(
            session,
            AuditWriter(Redactor()),
            project_id=project.id,
            task_id=None,
            action_type="project_delete",
            payload={"project_id": str(project.id), "name": project.name, "previous_status": "archived"},
            summary="Delete project",
            risk_level=3,
            requested_by=f"user:{project.owner_id}",
            now=NOW - timedelta(days=8),
        )
        await session.commit()
    result = await platform_jobs.run_approvals_sweep(now=NOW)
    assert (result["expired"], result["dispatched"], result["failed"]) == (1, 1, 0)
    async with make_sessionmaker(engine)() as session:
        assert (await session.get(Approval, approval.id)).status == "expired"  # type: ignore[union-attr]
        assert (await session.get(Project, project.id)).status == "archived"  # type: ignore[union-attr]


async def test_purge_job_removes_due_projects(engine: AsyncEngine, worker_env: Path) -> None:
    async with make_sessionmaker(engine)() as session:
        project = await make_project(session)
        project.status, project.deleted_at = "deleted", NOW - timedelta(days=8)
        await session.commit()
        project_id = project.id
    (worker_env / str(project_id) / "repo").mkdir(parents=True)
    assert await platform_jobs.run_projects_purge(now=NOW) == [str(project_id)]
    assert not (worker_env / str(project_id)).exists()
    async with make_sessionmaker(engine)() as session:
        assert await session.scalar(select(Project).where(Project.id == project_id)) is None
