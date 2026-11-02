"""Projects from an archive (FR-015, AC-02 import part; WP-2.1b) with real PostgreSQL, Redis and git.

The worker is not started: tests call ``project_imports.run_import`` where the queue would deliver the job.
Malicious archives through the same path are in ``tests/security/test_archive_import_http.py``.
"""

from __future__ import annotations

import hashlib
import uuid
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.rbac import RoleGrant
from app.core.redaction import Redactor
from app.models.audit import AuditEvent
from app.services import project_imports, projects
from app.services.import_lock import lock_key
from tests.conftest import make_test_env
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    OWNER,
    PROJECTS,
    app,
    clean_state,
    create,
    entries,
    headers,
    owner,
    owner_id,
    workspace_root,
)


async def create_upload(client: httpx.AsyncClient, name: str = "Arsip") -> dict[str, object]:
    response = await create(client, name, source={"type": "upload"})
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def held(project_id: object) -> str | None:
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    try:
        value = await redis.get(lock_key(uuid.UUID(str(project_id))))
    finally:
        await redis.aclose()
    return value.decode() if value is not None else None


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


# --- acceptance (Task 2): the service the upload route drives -----------------------------------------------


class RecordingQueue:
    """Stands in for Celery: records what would be sent to the worker."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []
        self.fail = fail

    async def enqueue_import(self, *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID) -> None:
        if self.fail:
            raise ConnectionError("broker unreachable")
        self.calls.append((project_id, job_id, user_id))


def import_env(api: FastAPI, root: Path, queue: RecordingQueue) -> project_imports.ImportEnv:
    deps = api.state.dependencies
    projects_env = projects.ProjectEnv(
        deps.sessions, AuditWriter(Redactor()), deps.git, root, redis=deps.redis
    )
    return project_imports.ImportEnv(projects=projects_env, redis=deps.redis, jobs=queue)


def owner_actor(user_id: uuid.UUID) -> projects.Actor:
    return projects.Actor(user_id, OWNER[0], "Tira", (RoleGrant("owner", "global"),), ip="203.0.113.10")


def small_zip(tmp: Path, name: str = "proj.zip") -> bytes:
    path = tmp / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("proj-main/README.md", "# Proj\n")
        archive.writestr("proj-main/src/app.py", "print('ok')\n")
    return path.read_bytes()


async def accept(env: project_imports.ImportEnv, actor: projects.Actor, project_id: uuid.UUID, data: bytes):  # type: ignore[no-untyped-def]
    slot = await project_imports.begin_import(env, actor, project_id)
    sink = project_imports.ArchiveSink(slot)
    try:
        for start in range(0, len(data), 64 * 1024):
            await sink.write(data[start : start + 64 * 1024])
        upload = await sink.close("proj.zip")
        return await project_imports.finish_import(env, actor, slot, upload)
    except BaseException:
        await project_imports.abort_import(env, slot)
        raise


async def test_begin_refuses_projects_that_cannot_take_an_archive(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    env = import_env(app, workspace_root, RecordingQueue())
    actor = owner_actor(owner_id)

    async def reason(project_id: object) -> str:
        with pytest.raises(AppError) as refused:
            await project_imports.begin_import(env, actor, uuid.UUID(str(project_id)))
        assert (refused.value.code, refused.value.http_status) == ("CONFLICT", 409)
        return refused.value.details[0]["reason"]  # type: ignore[index]

    blank = (await create(owner, "Blank")).json()["data"]
    assert await reason(blank["id"]) == "not_an_upload_project"
    running = await create_upload(owner, "Running")
    await hold_lock(running["id"])
    assert await reason(running["id"]) == "import_running"
    archived = await create_upload(owner, "Archived")
    await owner.post(f"{PROJECTS}/{archived['id']}/archive", headers=headers(owner))
    assert await reason(archived["id"]) == "project_archived"


async def test_sink_caps_the_size_and_checks_the_type(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = import_env(app, workspace_root, RecordingQueue())
    actor = owner_actor(owner_id)
    project_id = uuid.UUID(str((await create_upload(owner))["id"]))
    monkeypatch.setattr(project_imports, "IMPORT_MAX_BYTES", 1000)
    with pytest.raises(AppError) as too_large:
        await accept(env, actor, project_id, small_zip(tmp_path) + b"\0" * 2000)
    assert (too_large.value.code, too_large.value.http_status) == ("PAYLOAD_TOO_LARGE", 413)
    monkeypatch.setattr(project_imports, "IMPORT_MAX_BYTES", 200 * 1024 * 1024)
    with pytest.raises(AppError) as wrong_type:
        await accept(env, actor, project_id, b"just some text, not an archive\n" * 40)
    assert (wrong_type.value.code, wrong_type.value.http_status) == ("UNSUPPORTED_FILE_TYPE", 400)
    assert wrong_type.value.details == [{"detected_mime": "text/plain"}]
    assert list((workspace_root / str(project_id) / "uploads").iterdir()) == []
    assert await held(project_id) is None  # each refusal released the lock


async def test_finish_enqueues_once_and_audits(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    queue = RecordingQueue()
    env = import_env(app, workspace_root, queue)
    project_id = uuid.UUID(str((await create_upload(owner))["id"]))
    data = small_zip(tmp_path)
    accepted = await accept(env, owner_actor(owner_id), project_id, data)
    assert queue.calls == [(project_id, accepted.job_id, owner_id)]
    assert accepted.upload == project_imports.UploadInfo(
        "proj.zip", len(data), hashlib.sha256(data).hexdigest()
    )
    assert (accepted.repository.sync_state, accepted.repository.error) == ("importing", None)
    uploads = workspace_root / str(project_id) / "uploads"
    assert [p.name for p in uploads.iterdir()] == [f"{accepted.job_id}.archive"]
    assert await held(project_id) == str(accepted.job_id)
    async with make_sessionmaker(engine)() as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "project.import_requested", AuditEvent.resource_id == str(project_id)
                )
            )
        ).all()
    assert [(e.result, e.metadata_["sha256"], e.metadata_["job_id"], e.ip) for e in events] == [
        ("success", hashlib.sha256(data).hexdigest(), str(accepted.job_id), "203.0.113.10")
    ]


async def test_an_enqueue_failure_leaves_an_error_and_no_files(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    env = import_env(app, workspace_root, RecordingQueue(fail=True))
    project_id = uuid.UUID(str((await create_upload(owner))["id"]))
    with pytest.raises(AppError) as unavailable:
        await accept(env, owner_actor(owner_id), project_id, small_zip(tmp_path))
    assert (unavailable.value.code, unavailable.value.http_status) == ("DEPENDENCY_UNAVAILABLE", 503)
    read = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
    assert (read["repository"]["sync_state"], read["repository"]["error"]) == ("error", "import_not_started")
    assert list((workspace_root / str(project_id) / "uploads").iterdir()) == []
    assert await held(project_id) is None


def test_clean_filename() -> None:
    clean = project_imports.clean_filename
    assert clean("../../x.zip") == "x.zip"
    assert clean("C:\\Users\\a\\x.tar.gz") == "x.tar.gz"
    assert clean("a\u202eb\x00.zip") == "ab.zip"
    assert clean("") == "archive"
    assert clean(None) == "archive"
    assert len(clean("x" * 1000 + ".zip")) == 255
