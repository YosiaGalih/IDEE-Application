"""Projects from an archive (FR-015, AC-02 import part; WP-2.1b) with real PostgreSQL, Redis and git.

The worker is not started: tests call ``project_imports.run_import`` where the queue would deliver the job.
Malicious archives through the same path are in ``tests/security/test_archive_import_http.py``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from app.services.import_lock import lock_key
from tests.conftest import make_test_env
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    create,
    entries,
    owner,
    owner_id,
    workspace_root,
)


async def create_upload(client: httpx.AsyncClient, name: str = "Arsip") -> dict[str, object]:
    response = await create(client, name, source={"type": "upload"})
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def hold_lock(project_id: object, value: str = "job") -> None:
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    try:
        await redis.set(lock_key(uuid.UUID(str(project_id))), value)
    finally:
        await redis.aclose()


async def test_create_from_upload_has_no_folder_and_waits(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    data = await create_upload(owner)
    assert data["repository"] == {
        "provider": "upload",
        "remote_url": None,
        "branch": "main",
        "sync_state": "importing",
        "last_indexed_at": None,
        "error": None,
    }
    assert data["stack"] == {}
    assert data["stats"]["last_commit"] is None
    assert entries(workspace_root) == []


async def test_an_upload_project_without_a_running_import_reads_as_incomplete(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    del app
    data = await create_upload(owner)
    url = f"{PROJECTS}/{data['id']}"
    read = (await owner.get(url)).json()["data"]
    assert (read["repository"]["sync_state"], read["repository"]["error"]) == ("error", "import_incomplete")
    assert read["stats"]["last_commit"] is None
    [listed] = (await owner.get(PROJECTS)).json()["data"]
    assert (listed["repository"]["sync_state"], listed["repository"]["error"]) == (
        "error",
        "import_incomplete",
    )

    await hold_lock(data["id"])  # an import is running: nothing to report
    read = (await owner.get(url)).json()["data"]
    assert (read["repository"]["sync_state"], read["repository"]["error"]) == ("importing", None)
    [listed] = (await owner.get(PROJECTS)).json()["data"]
    assert (listed["repository"]["sync_state"], listed["repository"]["error"]) == ("importing", None)
