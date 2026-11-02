"""Projects from an archive (FR-015, AC-02 import part; WP-2.1b) with real PostgreSQL, Redis and git.

The worker is not started: tests call ``project_imports.run_import`` where the queue would deliver the job.
Malicious archives through the same path are in ``tests/security/test_archive_import_http.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import io
import os
import tarfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from redis import Redis as SyncRedis
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api.routes import project_imports as import_routes
from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.ratelimit import RateDecision, RateLimiterUnavailable
from app.core.rbac import RoleGrant
from app.core.redaction import Redactor
from app.gitops.adapter import GitAdapter
from app.gitops.errors import GitError
from app.models.audit import AuditEvent
from app.models.projects import Project, Repository
from app.services import import_lock, project_imports, projects
from app.services.import_lock import lock_key
from app.workspace.archive import ArchiveLimits
from tests.conftest import make_test_env
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    OWNER,
    PROJECTS,
    app,
    clean_state,
    create,
    entries,
    git,
    headers,
    owner,
    owner_id,
    workspace_root,
)


async def create_upload(client: httpx.AsyncClient, name: str = "Arsip") -> dict[str, Any]:
    response = await create(client, name, source={"type": "upload"})
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def held(project_id: object) -> str | None:
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    try:
        value = await redis.get(lock_key(uuid.UUID(str(project_id))))
    finally:
        await redis.aclose()
    return value.decode() if isinstance(value, bytes) else value


async def import_events(
    engine: AsyncEngine, project_id: object, action: str = "project.import"
) -> list[tuple[str, str | None]]:
    """``(result, reason)`` of the project's import audit events, oldest first."""
    async with make_sessionmaker(engine)() as session:
        events = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == action, AuditEvent.resource_id == str(project_id))
                .order_by(AuditEvent.id)
            )
        ).all()
    return [(e.result, e.metadata_.get("reason")) for e in events]


async def drop_lock(project_id: object) -> None:
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    try:
        await redis.delete(lock_key(uuid.UUID(str(project_id))))
    finally:
        await redis.aclose()


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
        self.filenames: list[str] = []
        self.fail = fail

    async def enqueue_import(
        self, *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID, filename: str
    ) -> None:
        if self.fail:
            raise ConnectionError("broker unreachable")
        self.calls.append((project_id, job_id, user_id))
        self.filenames.append(filename)


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
    engine: AsyncEngine,
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
    assert await import_events(engine, project_id) == [("error", "import_not_started")]


def test_clean_filename() -> None:
    clean = project_imports.clean_filename
    assert clean("../../x.zip") == "x.zip"
    assert clean("C:\\Users\\a\\x.tar.gz") == "x.tar.gz"
    assert clean("a\u202eb\x00.zip") == "ab.zip"
    assert clean("") == "archive"
    assert clean(None) == "archive"
    assert len(clean("x" * 1000 + ".zip")) == 255


# --- the route (Task 3) -----------------------------------------------------------------------------------


IMPORTS = PROJECTS + "/{id}/imports"


async def upload(
    client: httpx.AsyncClient,
    project_id: object,
    data: bytes,
    *,
    name: str = "proj.zip",
    key: str | None = None,
) -> httpx.Response:
    sent = headers(client)
    if key is not None:
        sent["Idempotency-Key"] = key
    return await client.post(
        IMPORTS.format(id=project_id), files={"file": (name, data, "application/zip")}, headers=sent
    )


def multipart(parts: list[tuple[str, str | None, bytes]], boundary: str = "kkboundary") -> tuple[bytes, str]:
    body = b""
    for field_name, filename, data in parts:
        disposition = f'form-data; name="{field_name}"' + (f'; filename="{filename}"' if filename else "")
        body += f"--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n".encode() + data + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:  # noqa: F811
    recording = RecordingQueue()
    app.state.dependencies.jobs = recording
    return recording


async def test_an_upload_through_http_is_accepted(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project = await create_upload(owner)
    data = small_zip(tmp_path)
    response = await upload(owner, project["id"], data)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["data"]["upload"] == {
        "filename": "proj.zip",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    assert body["data"]["repository"]["sync_state"] == "importing"
    assert [str(call[1]) for call in queue.calls] == [body["data"]["job_id"]]


async def test_a_project_that_cannot_take_an_archive_is_refused_before_the_body(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    blank = (await create(owner, "Blank")).json()["data"]
    response = await upload(owner, blank["id"], small_zip(tmp_path))
    assert response.status_code == 409
    assert response.json()["error"]["details"] == [{"reason": "not_an_upload_project"}]
    assert queue.calls == []


@pytest.mark.parametrize(
    "parts",
    [
        [("file", "a.zip", b"PK\x03\x04x"), ("file", "b.zip", b"PK\x03\x04y")],
        [("note", None, b"hello"), ("file", "a.zip", b"PK\x03\x04x")],
        [("archive", "a.zip", b"PK\x03\x04x")],
        [("file", None, b"PK\x03\x04x")],
    ],
    ids=["two_files", "extra_field", "wrong_name", "no_filename"],
)
async def test_anything_but_one_file_field_is_422_and_leaves_nothing(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    parts: list[tuple[str, str | None, bytes]],
) -> None:
    project = await create_upload(owner)
    body, content_type = multipart(parts)
    sent = headers(owner) | {"Content-Type": content_type}
    response = await owner.post(IMPORTS.format(id=project["id"]), content=body, headers=sent)
    assert response.status_code == 422, response.text
    assert list((workspace_root / str(project["id"]) / "uploads").iterdir()) == []
    assert await held(project["id"]) is None
    assert queue.calls == []


async def test_an_upload_without_content_length_is_400(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
) -> None:
    project = await create_upload(owner)
    body, content_type = multipart([("file", "a.zip", b"PK\x03\x04x")])

    async def chunks():  # type: ignore[no-untyped-def]
        yield body

    sent = headers(owner) | {"Content-Type": content_type}
    response = await owner.post(IMPORTS.format(id=project["id"]), content=chunks(), headers=sent)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    assert queue.calls == []


async def test_a_retry_with_the_same_key_replays_without_a_second_job(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project = await create_upload(owner)
    data = small_zip(tmp_path)
    key = str(uuid.uuid4())
    first = await upload(owner, project["id"], data, key=key)
    again = await upload(owner, project["id"], data, key=key)
    assert (first.status_code, again.status_code) == (202, 202)
    assert again.json()["data"]["job_id"] == first.json()["data"]["job_id"]
    assert len(queue.calls) == 1


async def test_a_client_that_goes_away_mid_upload_leaves_nothing(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    project = await create_upload(owner)
    body, content_type = multipart([("file", "a.zip", small_zip(tmp_path) + b"\0" * 300_000)])

    async def broken():  # type: ignore[no-untyped-def]
        yield body[: len(body) // 2]
        raise ConnectionResetError("client went away")

    sent = headers(owner) | {"Content-Type": content_type, "Content-Length": str(len(body))}
    with pytest.raises(ConnectionResetError):
        await owner.post(IMPORTS.format(id=project["id"]), content=broken(), headers=sent)
    assert list((workspace_root / str(project["id"]) / "uploads").iterdir()) == []
    assert await held(project["id"]) is None
    assert queue.calls == []
    assert (await upload(owner, project["id"], small_zip(tmp_path))).status_code == 202


async def test_two_uploads_at_once_one_wins(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project = await create_upload(owner)
    data = small_zip(tmp_path)
    responses = await asyncio.gather(upload(owner, project["id"], data), upload(owner, project["id"], data))
    assert sorted(r.status_code for r in responses) == [202, 409]
    refused = next(r for r in responses if r.status_code == 409)
    assert refused.json()["error"]["details"] == [{"reason": "import_running"}]
    assert len(queue.calls) == 1


# --- the worker job (Task 4) --------------------------------------------------------------------------------


async def accepted_upload(
    client: httpx.AsyncClient, data: bytes, name: str = "proj.zip"
) -> tuple[uuid.UUID, uuid.UUID]:
    project = await create_upload(client)
    response = await upload(client, project["id"], data, name=name)
    assert response.status_code == 202, response.text
    return uuid.UUID(str(project["id"])), uuid.UUID(response.json()["data"]["job_id"])


async def run_job(
    api: FastAPI,
    root: Path,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    filename: str = "proj.zip",
    limits: ArchiveLimits | None = None,
) -> str:
    env = import_env(api, root, RecordingQueue())
    extra = {"limits": limits} if limits is not None else {}
    return await project_imports.run_import(
        env, project_id=project_id, job_id=job_id, user_id=user_id, filename=filename, **extra
    )


async def test_run_import_makes_a_repository_with_one_commit(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"
    repo = workspace_root / str(project_id) / "repo"
    assert (repo / "README.md").read_text() == "# Proj\n"  # the proj-main/ wrapper is stripped
    assert git(repo, "log", "--format=%s|%an|%ae") == "Import from archive proj.zip|Tira|owner@example.test"
    data = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
    assert (data["repository"]["sync_state"], data["repository"]["error"]) == ("ready", None)
    assert data["stats"]["last_commit"] == git(repo, "rev-parse", "HEAD")
    assert "python" in data["stack"]["languages"]
    assert list((workspace_root / str(project_id) / "uploads").iterdir()) == []
    assert await held(project_id) is None
    async with make_sessionmaker(engine)() as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "project.import", AuditEvent.resource_id == str(project_id)
                )
            )
        ).all()
    assert [(e.result, e.metadata_["files"], e.metadata_["stripped_prefix"]) for e in events] == [
        ("success", 2, "proj-main")
    ]


async def test_a_job_delivered_again_changes_nothing(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"
    repo = workspace_root / str(project_id) / "repo"
    head = git(repo, "rev-parse", "HEAD")
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "skipped"
    assert git(repo, "rev-parse", "HEAD") == head
    data = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
    assert data["repository"]["sync_state"] == "ready"


async def test_a_git_failure_removes_the_repository_and_allows_a_new_upload(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))

    async def broken(*_: object, **__: object) -> str | None:
        raise GitError("commit_failed")

    monkeypatch.setattr(GitAdapter, "commit_all", broken)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "error"
    data = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
    assert (data["repository"]["sync_state"], data["repository"]["error"]) == ("error", "import_git_failed")
    assert not (workspace_root / str(project_id) / "repo").exists()
    assert list((workspace_root / str(project_id) / "uploads").iterdir()) == []
    assert await held(project_id) is None
    monkeypatch.undo()
    assert (await upload(owner, project_id, small_zip(tmp_path))).status_code == 202


async def test_a_stale_job_changes_nothing(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    await hold_lock(project_id, "a-newer-upload")  # this job's lock expired and someone else took it
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "skipped"
    assert not (workspace_root / str(project_id) / "repo").exists()
    assert await held(project_id) == "a-newer-upload"  # never freed by a job that does not own it
    data = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
    assert data["repository"]["sync_state"] == "importing"
    assert await import_events(engine, project_id) == [("error", "import_superseded")]
    assert list((workspace_root / str(project_id) / "uploads").iterdir()) == []  # its own archive only


async def test_a_job_whose_lock_expired_with_nobody_after_it_fails_visibly(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    await drop_lock(project_id)  # the queue waited past LOCK_TTL_S
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "error"
    data = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
    assert (data["repository"]["sync_state"], data["repository"]["error"]) == ("error", "import_expired")
    assert await import_events(engine, project_id) == [("error", "import_expired")]
    assert not (workspace_root / str(project_id) / "repo").exists()
    assert list((workspace_root / str(project_id) / "uploads").iterdir()) == []


async def test_an_archive_with_nothing_in_it_is_rejected_as_empty(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("only-a-folder/", "")
    project_id, job_id = await accepted_upload(owner, path.read_bytes(), name="empty.zip")
    assert await run_job(app, workspace_root, owner_id, project_id, job_id, "empty.zip") == "error"
    data = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
    assert data["repository"]["error"] == "archive_rejected:empty"
    assert not (workspace_root / str(project_id) / "repo").exists()


# --- review round: ownership, leftovers, retries, limits --------------------------------------------------


def project_folder(root: Path, project_id: object) -> Path:
    return root / str(project_id)


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


async def repository_state(client: httpx.AsyncClient, project_id: object) -> tuple[str, str | None]:
    repository = (await client.get(f"{PROJECTS}/{project_id}")).json()["data"]["repository"]
    return repository["sync_state"], repository["error"]


async def test_two_runs_of_one_job_at_once_finish_it_once(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    outcomes = await asyncio.gather(
        run_job(app, workspace_root, owner_id, project_id, job_id),
        run_job(app, workspace_root, owner_id, project_id, job_id),
    )
    assert sorted(outcomes) == ["ready", "skipped"]
    folder = project_folder(workspace_root, project_id)
    assert git(folder / "repo", "log", "--format=%s") == "Import from archive proj.zip"
    assert names(folder) == ["repo", "uploads"]
    assert await repository_state(owner, project_id) == ("ready", None)
    assert await import_events(engine, project_id) == [("success", None)]


async def test_a_killed_runs_leftovers_go_and_the_job_runs_again(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    folder = project_folder(workspace_root, project_id)
    (folder / f".import-{job_id}").mkdir()
    (folder / f".import-{job_id}" / "half.txt").write_text("x")
    (folder / f"..import-{job_id}.importing-0a1b2c").mkdir()
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"
    assert names(folder) == ["repo", "uploads"]
    assert not (folder / "repo" / "half.txt").exists()


async def test_the_job_never_removes_a_repo_folder_it_did_not_publish(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    folder = project_folder(workspace_root, project_id)
    (folder / "repo").mkdir()
    (folder / "repo" / "precious.txt").write_text("keep")
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "error"
    assert (folder / "repo" / "precious.txt").read_text() == "keep"
    assert names(folder) == ["repo", "uploads"]  # only its own work folder went
    assert await repository_state(owner, project_id) == ("error", "import_failed")
    # Nor does the next upload: no import's publish marker says this repo/ is an import's to remove.
    response = await upload(owner, project_id, small_zip(tmp_path))
    assert response.status_code == 409
    assert response.json()["error"]["details"] == [{"reason": "repository_not_empty"}]
    assert (folder / "repo" / "precious.txt").read_text() == "keep"
    assert await held(project_id) is None


async def test_a_new_upload_clears_what_an_interrupted_import_left(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    """AG-13 (T9): a job killed after it published ``repo/``, a dead upload and a dead extraction."""
    project_id, old_job = await accepted_upload(owner, small_zip(tmp_path))
    await drop_lock(project_id)  # the dead job's lock expired
    folder = project_folder(workspace_root, project_id)
    (folder / "repo" / ".git").mkdir(parents=True)  # published, never committed
    (folder / f".import-{old_job}.published").touch()  # its publish marker
    (folder / "uploads" / f"{uuid.uuid4()}.part").write_bytes(b"half an upload")
    (folder / f".import-{old_job}").mkdir()
    (folder / f"..import-{old_job}.importing-0a1b2c").mkdir()
    (folder / ".repo.importing-0a1b2c").mkdir()
    assert await repository_state(owner, project_id) == ("error", "import_incomplete")

    response = await upload(owner, project_id, small_zip(tmp_path))
    assert response.status_code == 202, response.text
    new_job = uuid.UUID(response.json()["data"]["job_id"])
    assert names(folder) == ["uploads"]
    assert names(folder / "uploads") == [f"{new_job}.archive"]
    assert await import_events(engine, project_id) == [
        ("error", "import_incomplete")
    ]  # the dead job's outcome
    assert await run_job(app, workspace_root, owner_id, project_id, new_job) == "ready"
    head = git(folder / "repo", "rev-parse", "HEAD")
    # The dead job delivered late finds nothing of its own and changes nothing.
    assert await run_job(app, workspace_root, owner_id, project_id, old_job) == "skipped"
    assert git(folder / "repo", "rev-parse", "HEAD") == head
    assert await repository_state(owner, project_id) == ("ready", None)


async def test_a_sweep_that_cannot_clear_refuses_the_upload(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await create_upload(owner)
    monkeypatch.setattr(project_imports, "_remove_paths", lambda *_: False)
    response = await upload(owner, project["id"], small_zip(tmp_path))
    assert response.status_code == 409
    assert response.json()["error"]["details"] == [{"reason": "cleanup_failed"}]
    assert await held(project["id"]) is None
    assert queue.calls == []


async def test_an_upload_whose_lock_expired_is_refused_when_it_finishes(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    queue = RecordingQueue()
    env = import_env(app, workspace_root, queue)
    actor = owner_actor(owner_id)
    project_id = uuid.UUID(str((await create_upload(owner))["id"]))
    slot = await project_imports.begin_import(env, actor, project_id)
    sink = project_imports.ArchiveSink(slot)
    await sink.write(small_zip(tmp_path))
    info = await sink.close("proj.zip")
    await drop_lock(project_id)  # the upload took longer than LOCK_TTL_S
    with pytest.raises(AppError) as expired:
        await project_imports.finish_import(env, actor, slot, info)
    await project_imports.abort_import(env, slot)
    assert (expired.value.http_status, expired.value.details) == (409, [{"reason": "import_expired"}])
    assert queue.calls == []
    assert list(slot.uploads.iterdir()) == []
    assert await repository_state(owner, project_id) == ("error", "import_incomplete")


async def test_when_redis_cannot_answer_the_job_is_retried_with_its_archive(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))

    async def down(*_: object, **__: object) -> bool:
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(import_lock, "refresh", down)
    with pytest.raises(project_imports.ImportRetry):
        await run_job(app, workspace_root, owner_id, project_id, job_id)
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    assert names(folder / "uploads") == [f"{job_id}.archive"]
    assert await held(project_id) == str(job_id)
    assert await repository_state(owner, project_id) == ("importing", None)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"  # the retry


async def test_when_a_failure_cannot_be_recorded_the_job_is_retried(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))

    async def broken(*_: object, **__: object) -> str | None:
        raise GitError("commit_failed")

    async def database_down(*_: object, **__: object) -> str:
        raise OperationalError("UPDATE repositories", {}, ConnectionError("database is down"))

    monkeypatch.setattr(GitAdapter, "commit_all", broken)
    monkeypatch.setattr(project_imports, "_settle_failure", database_down)
    with pytest.raises(project_imports.ImportRetry):
        await run_job(app, workspace_root, owner_id, project_id, job_id)
    assert names(project_folder(workspace_root, project_id) / "uploads") == [f"{job_id}.archive"]
    assert await held(project_id) == str(job_id)
    monkeypatch.undo()
    monkeypatch.setattr(GitAdapter, "commit_all", broken)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "error"  # the retry
    assert await repository_state(owner, project_id) == ("error", "import_git_failed")


async def test_a_commit_whose_reply_is_lost_keeps_the_repository(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    commit = AsyncSession.commit

    async def committed_then_lost(self: AsyncSession) -> None:
        await commit(self)
        raise OperationalError("COMMIT", {}, ConnectionError("reply lost"))

    monkeypatch.setattr(AsyncSession, "commit", committed_then_lost)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    assert git(folder / "repo", "log", "--format=%s") == "Import from archive proj.zip"
    assert names(folder) == ["repo", "uploads"]
    assert await repository_state(owner, project_id) == ("ready", None)


async def test_a_commit_that_fails_takes_the_repository_back_and_retries(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))

    async def fails(self: AsyncSession) -> None:
        raise OperationalError("COMMIT", {}, ConnectionError("database is down"))

    monkeypatch.setattr(AsyncSession, "commit", fails)
    with pytest.raises(project_imports.ImportRetry):
        await run_job(app, workspace_root, owner_id, project_id, job_id)
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    assert names(folder) == ["uploads"]  # repo/ taken back, work folder gone
    assert names(folder / "uploads") == [f"{job_id}.archive"]
    assert await repository_state(owner, project_id) == ("importing", None)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"  # the retry


async def test_a_missing_archive_with_the_lock_held_fails_visibly(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    (
        project_folder(workspace_root, project_id) / "uploads" / f"{job_id}.archive"
    ).unlink()  # no shared volume
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "error"
    assert await repository_state(owner, project_id) == ("error", "import_failed")
    assert await held(project_id) is None


class StubLimiter:
    def __init__(self, *, allowed: bool = True, down: bool = False) -> None:
        self.hits: list[tuple[str, int, int]] = []
        self.allowed = allowed
        self.down = down

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateDecision:
        self.hits.append((key, limit, window_seconds))
        if self.down:
            raise RateLimiterUnavailable("down")
        return RateDecision(allowed=self.allowed, retry_after=60)


async def test_the_upload_rate_limit_counts_only_uploads_that_could_be_taken(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    base = import_env(app, workspace_root, RecordingQueue())
    actor = owner_actor(owner_id)

    def limited_by(limiter: StubLimiter) -> project_imports.ImportEnv:
        return dataclasses.replace(base, projects=dataclasses.replace(base.projects, limiter=limiter))  # type: ignore[arg-type]

    limiter = StubLimiter(allowed=False)
    blank = uuid.UUID(str((await create(owner, "Blank")).json()["data"]["id"]))
    with pytest.raises(AppError) as refused:
        await project_imports.begin_import(limited_by(limiter), actor, blank)
    assert refused.value.details == [{"reason": "not_an_upload_project"}]
    assert limiter.hits == []  # a refusal by state spends nothing

    project_id = uuid.UUID(str((await create_upload(owner))["id"]))
    with pytest.raises(AppError) as limited:
        await project_imports.begin_import(limited_by(limiter), actor, project_id)
    assert (limited.value.code, limited.value.http_status) == ("RATE_LIMITED", 429)
    assert limited.value.headers == {"Retry-After": "60"}
    assert limiter.hits == [(f"kk:rl:project-import:{owner_id}", 10, 3600)]
    with pytest.raises(AppError) as unavailable:
        await project_imports.begin_import(limited_by(StubLimiter(down=True)), actor, project_id)
    assert (unavailable.value.code, unavailable.value.http_status) == ("DEPENDENCY_UNAVAILABLE", 503)
    assert await held(project_id) is None


def tar_gz(tmp: Path, files: dict[str, str]) -> bytes:
    path = tmp / "proj.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return path.read_bytes()


async def test_a_tar_gz_with_a_wrapper_folder_is_imported_over_http(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    data = tar_gz(tmp_path, {"proj-1.0/README.md": "# Proj\n", "proj-1.0/main.py": "print(1)\n"})
    project_id, job_id = await accepted_upload(owner, data, name="proj.tar.gz")
    assert await run_job(app, workspace_root, owner_id, project_id, job_id, "proj.tar.gz") == "ready"
    repo = project_folder(workspace_root, project_id) / "repo"
    assert names(repo) == [".git", "README.md", "main.py"]
    assert git(repo, "log", "--format=%s") == "Import from archive proj.tar.gz"


async def test_a_body_in_small_chunks_is_reassembled_exactly(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project = await create_upload(owner)
    data = small_zip(tmp_path)
    body, content_type = multipart([("file", "proj.zip", data)])

    async def chunks():  # type: ignore[no-untyped-def]
        for start in range(0, len(body), 7):
            yield body[start : start + 7]

    sent = headers(owner) | {"Content-Type": content_type, "Content-Length": str(len(body))}
    response = await owner.post(IMPORTS.format(id=project["id"]), content=chunks(), headers=sent)
    assert response.status_code == 202, response.text
    assert response.json()["data"]["upload"]["sha256"] == hashlib.sha256(data).hexdigest()


async def test_a_boundary_longer_than_70_is_422_before_the_lock(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await create_upload(owner)
    body, content_type = multipart([("file", "a.zip", b"PK\x03\x04x")], boundary="b" * 71)
    sent = headers(owner) | {"Content-Type": content_type}
    response = await owner.post(IMPORTS.format(id=project["id"]), content=body, headers=sent)
    assert response.status_code == 422, response.text
    assert not project_folder(workspace_root, project["id"]).exists()  # begin_import never ran
    assert await held(project["id"]) is None


async def test_an_archive_over_the_limit_is_413_over_http(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(project_imports, "IMPORT_MAX_BYTES", 1000)
    project = await create_upload(owner)
    response = await upload(owner, project["id"], small_zip(tmp_path) + b"\0" * 2000)
    assert response.status_code == 413
    assert response.json()["error"]["details"] == [{"max_bytes": 1000}]
    assert list((project_folder(workspace_root, project["id"]) / "uploads").iterdir()) == []
    assert await held(project["id"]) is None


async def test_a_body_past_the_multipart_allowance_is_413(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(project_imports, "IMPORT_MAX_BYTES", 1000)
    monkeypatch.setattr(import_routes, "MULTIPART_OVERHEAD", 1000)
    project = await create_upload(owner)
    body, content_type = multipart([("file", "a.zip", b"PK\x03\x04" + b"x" * 100)])
    body += b"\r\n" + b"e" * 5000  # an epilogue: ignored by the parser, still counted
    sent = headers(owner) | {"Content-Type": content_type}
    response = await owner.post(IMPORTS.format(id=project["id"]), content=body, headers=sent)
    assert response.status_code == 413
    assert list((project_folder(workspace_root, project["id"]) / "uploads").iterdir()) == []
    assert await held(project["id"]) is None


async def test_an_upload_that_stalls_is_408_and_the_key_stays_free(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_routes, "UPLOAD_IDLE_S", 0.2)
    project = await create_upload(owner)
    body, content_type = multipart([("file", "a.zip", small_zip(tmp_path))])

    async def stalls():  # type: ignore[no-untyped-def]
        yield body[:100]
        await asyncio.sleep(5)
        yield body[100:]

    sent = headers(owner) | {"Content-Type": content_type, "Content-Length": str(len(body))}
    response = await owner.post(IMPORTS.format(id=project["id"]), content=stalls(), headers=sent)
    assert response.status_code == 408
    assert response.json()["error"]["details"] == [{"reason": "upload_stalled"}]
    assert list((project_folder(workspace_root, project["id"]) / "uploads").iterdir()) == []
    assert await held(project["id"]) is None
    again = await owner.post(IMPORTS.format(id=project["id"]), content=body, headers=sent)
    assert again.status_code == 202, again.text  # 408 is not stored: the same key runs
    assert len(queue.calls) == 1


async def test_an_upload_that_loses_its_lock_midway_is_refused(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_routes, "KEEP_ALIVE_S", 0.0)  # extend on every chunk
    project = await create_upload(owner)
    body, content_type = multipart([("file", "a.zip", small_zip(tmp_path))])

    async def loses():  # type: ignore[no-untyped-def]
        yield body[:100]
        await drop_lock(project["id"])
        yield body[100:]

    sent = headers(owner) | {"Content-Type": content_type, "Content-Length": str(len(body))}
    response = await owner.post(IMPORTS.format(id=project["id"]), content=loses(), headers=sent)
    assert response.status_code == 409
    assert response.json()["error"]["details"] == [{"reason": "import_expired"}]
    assert list((project_folder(workspace_root, project["id"]) / "uploads").iterdir()) == []
    assert queue.calls == []


async def test_a_real_disconnect_mid_upload_leaves_nothing_and_frees_the_key(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    project = await create_upload(owner)
    path = IMPORTS.format(id=project["id"])
    body, content_type = multipart([("file", "a.zip", small_zip(tmp_path) + b"\0" * 300_000)])
    sent = headers(owner) | {"Content-Type": content_type, "Content-Length": str(len(body))}
    cookie = "; ".join(f"{name}={value}" for name, value in owner.cookies.items())
    messages = [{"type": "http.request", "body": body[: len(body) // 2], "more_body": True}]

    async def receive() -> dict[str, object]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    replies: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        replies.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"cookie", cookie.encode())]
        + [(name.lower().encode(), value.encode()) for name, value in sent.items()],
        "client": ("203.0.113.10", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)  # type: ignore[arg-type]
    start = next(m for m in replies if m["type"] == "http.response.start")
    assert start["status"] == 408
    assert list((project_folder(workspace_root, project["id"]) / "uploads").iterdir()) == []
    assert await held(project["id"]) is None
    assert queue.calls == []
    again = await owner.post(path, content=body, headers=sent)
    assert again.status_code == 202, again.text  # the key was freed, not stored


async def test_the_same_key_with_another_length_is_409_and_runs_nothing(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project = await create_upload(owner)
    key = str(uuid.uuid4())
    first = await upload(owner, project["id"], small_zip(tmp_path), key=key)
    assert first.status_code == 202, first.text
    other = await upload(owner, project["id"], small_zip(tmp_path) + b"\0" * 10, key=key)
    assert other.status_code == 409
    assert len(queue.calls) == 1


async def test_a_declared_length_past_the_limit_is_413_before_any_read(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
) -> None:
    project = await create_upload(owner)
    read = False

    async def body():  # type: ignore[no-untyped-def]
        nonlocal read
        read = True
        yield b"x"

    sent = headers(owner) | {
        "Content-Type": "multipart/form-data; boundary=kk",
        "Content-Length": str(300 * 1024 * 1024),
    }
    response = await owner.post(IMPORTS.format(id=project["id"]), content=body(), headers=sent)
    assert response.status_code == 413
    assert not read
    assert await held(project["id"]) is None


async def test_a_slow_upload_keeps_its_lock_while_it_streams(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_routes, "KEEP_ALIVE_S", 0.0)  # extend on every chunk
    project = await create_upload(owner)
    body, content_type = multipart([("file", "a.zip", small_zip(tmp_path))])
    key = lock_key(uuid.UUID(str(project["id"])))

    async def slow():  # type: ignore[no-untyped-def]
        yield body[:100]
        redis = Redis.from_url(make_test_env()["REDIS_URL"])
        try:
            await redis.expire(key, 1)  # about to run out
        finally:
            await redis.aclose()
        yield body[100:200]  # extended here
        await asyncio.sleep(1.5)
        yield body[200:]

    sent = headers(owner) | {"Content-Type": content_type, "Content-Length": str(len(body))}
    response = await owner.post(IMPORTS.format(id=project["id"]), content=slow(), headers=sent)
    assert response.status_code == 202, response.text
    assert await held(project["id"]) == response.json()["data"]["job_id"]


# --- delta review round: publish marker, row lock, guards ------------------------------------------------


async def test_a_ready_project_refuses_an_upload_and_nothing_is_swept(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"
    folder = project_folder(workspace_root, project_id)
    (folder / f".import-{uuid.uuid4()}").mkdir()
    (folder / "uploads" / "junk.part").write_bytes(b"x")
    response = await upload(owner, project_id, small_zip(tmp_path))
    assert response.status_code == 409
    assert response.json()["error"]["details"] == [{"reason": "repository_not_empty"}]
    assert len(names(folder)) == 3 and (folder / "uploads" / "junk.part").exists()
    assert git(folder / "repo", "log", "--format=%s") == "Import from archive proj.zip"
    assert await held(project_id) is None


async def test_the_sweep_never_follows_a_link(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    project = await create_upload(owner)
    folder = project_folder(workspace_root, project["id"])
    outside = tmp_path / "outside"
    for name in ("a", "b", "c"):
        (outside / name).mkdir(parents=True)
        (outside / name / "keep.txt").write_text("keep")
    (folder / "uploads").mkdir(parents=True)
    (folder / "uploads" / "link.part").symlink_to(outside / "a")
    (folder / f".import-{uuid.uuid4()}").symlink_to(outside / "b")
    (folder / "repo").symlink_to(outside / "c")
    (folder / f".import-{uuid.uuid4()}.published").touch()
    response = await upload(owner, project["id"], small_zip(tmp_path))
    assert response.status_code == 202, response.text
    assert names(folder) == ["uploads"]
    assert all((outside / name / "keep.txt").read_text() == "keep" for name in ("a", "b", "c"))


async def test_an_upload_racing_a_publish_waits_for_its_commit(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AG-13 delta: the Redis key vanishes between the job's last lock check and its commit (Redis restart,
    an operator). The new upload's re-check and sweep wait for the job's row lock, then see ``ready``."""
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    write = AuditWriter.write
    racing: list[asyncio.Task[httpx.Response]] = []

    async def write_then_race(self: AuditWriter, session: AsyncSession, **event: Any) -> None:
        if event.get("action") == "project.import" and event.get("result") == "success" and not racing:
            await drop_lock(project_id)
            racing.append(asyncio.create_task(upload(owner, project_id, small_zip(tmp_path))))
            await asyncio.sleep(0.5)  # the upload reaches the row lock while repo/ is published, uncommitted
        await write(self, session, **event)

    monkeypatch.setattr(AuditWriter, "write", write_then_race)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"
    response = await racing[0]
    assert response.status_code == 409
    assert response.json()["error"]["details"] == [{"reason": "repository_not_empty"}]
    folder = project_folder(workspace_root, project_id)
    assert git(folder / "repo", "log", "--format=%s") == "Import from archive proj.zip"
    assert names(folder) == ["repo", "uploads"]
    assert await repository_state(owner, project_id) == ("ready", None)


async def test_a_commit_with_an_unknown_outcome_is_taken_back_by_the_retry(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))

    async def fails(self: AsyncSession) -> None:
        raise OperationalError("COMMIT", {}, ConnectionError("database is down"))

    async def unknown(*_: object) -> bool | None:
        return None

    monkeypatch.setattr(AsyncSession, "commit", fails)
    monkeypatch.setattr(project_imports, "_is_ready", unknown)
    with pytest.raises(project_imports.ImportRetry):
        await run_job(app, workspace_root, owner_id, project_id, job_id)
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    assert names(folder) == [f".import-{job_id}.published", "repo", "uploads"]  # kept: the outcome is unknown
    assert await repository_state(owner, project_id) == ("importing", None)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"  # the retry
    assert names(folder) == ["repo", "uploads"]
    assert git(folder / "repo", "log", "--format=%s") == "Import from archive proj.zip"


async def test_a_commit_with_an_unknown_outcome_that_landed_is_left_alone(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    commit = AsyncSession.commit

    async def committed_then_lost(self: AsyncSession) -> None:
        await commit(self)
        raise OperationalError("COMMIT", {}, ConnectionError("reply lost"))

    async def unknown(*_: object) -> bool | None:
        return None

    monkeypatch.setattr(AsyncSession, "commit", committed_then_lost)
    monkeypatch.setattr(project_imports, "_is_ready", unknown)
    with pytest.raises(project_imports.ImportRetry):
        await run_job(app, workspace_root, owner_id, project_id, job_id)
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    head = git(folder / "repo", "rev-parse", "HEAD")
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "skipped"  # the retry
    assert names(folder) == ["repo", "uploads"]  # marker and archive gone, repo kept
    assert git(folder / "repo", "rev-parse", "HEAD") == head
    assert await repository_state(owner, project_id) == ("ready", None)
    assert await held(project_id) is None


async def test_a_lock_lost_during_extraction_stops_before_git(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    extract = project_imports.import_archive
    inits: list[Path] = []

    def extract_then_expire(*args: Any, **kwargs: Any) -> Any:
        report = extract(*args, **kwargs)
        client = SyncRedis.from_url(make_test_env()["REDIS_URL"])
        try:
            client.delete(lock_key(project_id))
        finally:
            client.close()
        return report

    async def init(self: GitAdapter, repo: Path, branch: str) -> None:
        inits.append(repo)

    monkeypatch.setattr(project_imports, "import_archive", extract_then_expire)
    monkeypatch.setattr(GitAdapter, "init", init)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "error"
    assert inits == []
    assert await repository_state(owner, project_id) == ("error", "import_expired")
    assert names(project_folder(workspace_root, project_id)) == ["uploads"]


async def test_a_job_overtaken_before_publishing_never_publishes(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    commit_all = GitAdapter.commit_all

    async def commit_then_overtaken(self: GitAdapter, *args: Any, **kwargs: Any) -> str | None:
        head = await commit_all(self, *args, **kwargs)
        await hold_lock(project_id, "a-newer-upload")
        return head

    monkeypatch.setattr(GitAdapter, "commit_all", commit_then_overtaken)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "skipped"
    assert names(project_folder(workspace_root, project_id)) == ["uploads"]
    assert await held(project_id) == "a-newer-upload"
    assert await import_events(engine, project_id) == [("error", "import_superseded")]


async def test_publishing_waits_for_the_project_row_lock(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    async with make_sessionmaker(engine)() as holder:
        await holder.execute(select(Project).where(Project.id == project_id).with_for_update())
        job = asyncio.create_task(run_job(app, workspace_root, owner_id, project_id, job_id))
        await asyncio.sleep(2.0)
        assert not job.done()
        assert not (project_folder(workspace_root, project_id) / "repo").exists()
        await holder.rollback()
    assert await job == "ready"


async def test_recording_a_failure_waits_for_the_project_row_lock(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    env = import_env(app, workspace_root, RecordingQueue())
    async with make_sessionmaker(engine)() as holder:
        await holder.execute(select(Project).where(Project.id == project_id).with_for_update())
        settle = asyncio.create_task(
            project_imports._settle_failure(
                env, project_id, job_id, "import_failed", actor=("system", "test")
            )  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0.5)
        assert not settle.done()
        await holder.rollback()
    assert await settle == "error"


async def test_a_cancelled_abort_still_cleans_up(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    env = import_env(app, workspace_root, RecordingQueue())
    project_id = uuid.UUID(str((await create_upload(owner))["id"]))
    slot = await project_imports.begin_import(env, owner_actor(owner_id), project_id)
    slot.part.write_bytes(b"half")
    aborting = asyncio.create_task(project_imports.abort_import(env, slot))
    await asyncio.sleep(0)
    aborting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await aborting
    for _ in range(50):
        if await held(project_id) is None:
            break
        await asyncio.sleep(0.05)
    assert await held(project_id) is None
    assert not slot.part.exists()


async def test_a_lock_error_after_the_set_frees_the_lock(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = import_env(app, workspace_root, RecordingQueue())
    project_id = uuid.UUID(str((await create_upload(owner))["id"]))
    acquire = import_lock.acquire

    async def set_then_fail(*args: Any) -> bool:
        await acquire(*args)
        raise RedisConnectionError("the reply was lost")

    monkeypatch.setattr(import_lock, "acquire", set_then_fail)
    with pytest.raises(AppError) as unavailable:
        await project_imports.begin_import(env, owner_actor(owner_id), project_id)
    assert unavailable.value.http_status == 503
    assert await held(project_id) is None


async def test_a_refused_database_connection_is_retried(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))

    async def refused(*_: object, **__: object) -> None:
        raise ConnectionRefusedError("connection refused")  # what asyncpg raises, unwrapped, on connect

    monkeypatch.setattr(AsyncSession, "get", refused)
    with pytest.raises(project_imports.ImportRetry):
        await run_job(app, workspace_root, owner_id, project_id, job_id)
    monkeypatch.undo()
    assert names(project_folder(workspace_root, project_id) / "uploads") == [f"{job_id}.archive"]
    assert await held(project_id) == str(job_id)


async def test_an_upload_that_never_ends_is_408(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_routes, "UPLOAD_MAX_S", 0.3)
    project = await create_upload(owner)
    body, content_type = multipart([("file", "a.zip", small_zip(tmp_path))])

    async def trickle():  # type: ignore[no-untyped-def]
        for start in range(0, len(body), 20):
            yield body[start : start + 20]
            await asyncio.sleep(0.05)  # never idle long enough to stall, never done

    sent = headers(owner) | {"Content-Type": content_type, "Content-Length": str(len(body))}
    response = await owner.post(IMPORTS.format(id=project["id"]), content=trickle(), headers=sent)
    assert response.status_code == 408
    assert response.json()["error"]["details"] == [{"reason": "upload_too_slow"}]
    assert list((project_folder(workspace_root, project["id"]) / "uploads").iterdir()) == []
    assert await held(project["id"]) is None


# --- AG-13 delta at 9cb4223: stale markers, settling commits, outcome dedupe -------------------------------


async def unknown_commit_that_landed(
    api: FastAPI,
    root: Path,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the job so its ``ready`` commit lands but its outcome is unknown (marker and lock stay)."""
    commit = AsyncSession.commit

    async def committed_then_lost(self: AsyncSession) -> None:
        await commit(self)
        raise OperationalError("COMMIT", {}, ConnectionError("reply lost"))

    async def unknown(*_: object) -> bool | None:
        return None

    monkeypatch.setattr(AsyncSession, "commit", committed_then_lost)
    monkeypatch.setattr(project_imports, "_is_ready", unknown)
    with pytest.raises(project_imports.ImportRetry):
        await run_job(api, root, user_id, project_id, job_id)
    monkeypatch.undo()


async def test_a_committed_jobs_stale_marker_never_sweeps_its_repository(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AG-13 MEDIUM: the retry never ran (Redis down, lock expired), so the marker stays next to a committed
    repo/. Once something stores ``error`` on that project (WP-2.1c), an upload must still refuse."""
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    await unknown_commit_that_landed(app, workspace_root, owner_id, project_id, job_id, monkeypatch)
    folder = project_folder(workspace_root, project_id)
    head = git(folder / "repo", "rev-parse", "HEAD")
    assert (folder / f".import-{job_id}.published").exists()
    await drop_lock(project_id)
    async with make_sessionmaker(engine)() as session:
        repository = await session.scalar(select(Repository).where(Repository.project_id == project_id))
        assert repository is not None and repository.sync_state == "ready"
        repository.sync_state, repository.error = "error", "repository_unreadable"
        await session.commit()
    response = await upload(owner, project_id, small_zip(tmp_path))
    assert response.status_code == 409
    assert response.json()["error"]["details"] == [{"reason": "repository_not_empty"}]
    assert git(folder / "repo", "rev-parse", "HEAD") == head
    assert await held(project_id) is None


async def test_taking_back_waits_for_a_commit_still_settling(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AG-13 LOW: run 1's COMMIT is still in the database when run 2 starts; run 2 must wait for it."""
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))

    async def fails(self: AsyncSession) -> None:
        raise OperationalError("COMMIT", {}, ConnectionError("connection reset"))

    async def unknown(*_: object) -> bool | None:
        return None

    monkeypatch.setattr(AsyncSession, "commit", fails)
    monkeypatch.setattr(project_imports, "_is_ready", unknown)
    with pytest.raises(project_imports.ImportRetry):
        await run_job(app, workspace_root, owner_id, project_id, job_id)
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    async with make_sessionmaker(engine)() as settling:  # run 1's commit, still in flight
        await settling.execute(
            select(Project).where(Project.id == project_id).with_for_update(key_share=True)
        )
        repository = await settling.scalar(select(Repository).where(Repository.project_id == project_id))
        assert repository is not None
        repository.sync_state = "ready"
        await settling.flush()
        retry = asyncio.create_task(run_job(app, workspace_root, owner_id, project_id, job_id))
        await asyncio.sleep(1.0)
        assert not retry.done()
        assert (folder / "repo").is_dir()  # not taken back while the commit settles
        await settling.commit()
    assert await retry == "skipped"
    assert git(folder / "repo", "log", "--format=%s") == "Import from archive proj.zip"
    assert names(folder) == ["repo", "uploads"]  # its marker went once the commit was seen to land


async def test_a_job_whose_retries_ran_out_gets_its_outcome_from_the_next_upload(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))

    async def down(*_: object, **__: object) -> bool:
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(import_lock, "refresh", down)
    with pytest.raises(project_imports.ImportRetry):
        await run_job(app, workspace_root, owner_id, project_id, job_id)
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    assert names(folder) == ["uploads"] and names(folder / "uploads") == [f"{job_id}.archive"]
    await drop_lock(project_id)  # no retry came; the lock expired
    assert (await upload(owner, project_id, small_zip(tmp_path))).status_code == 202
    async with make_sessionmaker(engine)() as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "project.import", AuditEvent.project_id == project_id
                )
            )
        ).all()
    assert [(e.result, e.metadata_["reason"], e.metadata_["job_id"]) for e in events] == [
        ("error", "import_incomplete", str(job_id))
    ]


async def test_a_job_with_an_outcome_is_not_given_a_second_one(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    rename = os.rename

    def no_publish(src: Any, dst: Any) -> None:
        if Path(dst).name == "repo":
            raise PermissionError("cannot publish")
        rename(src, dst)

    monkeypatch.setattr(os, "rename", no_publish)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "error"
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    assert names(folder) == ["uploads"]  # no marker left: nothing was published
    (folder / "uploads" / f"{job_id}.archive").write_bytes(b"left by a failed delete")
    assert (await upload(owner, project_id, small_zip(tmp_path))).status_code == 202
    assert await import_events(engine, project_id) == [("error", "import_failed")]


async def test_an_uploads_folder_that_is_a_link_is_replaced_not_followed(
    owner: httpx.AsyncClient,  # noqa: F811
    queue: RecordingQueue,
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    project = await create_upload(owner)
    folder = project_folder(workspace_root, project["id"])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    folder.mkdir()
    (folder / "uploads").symlink_to(outside)
    response = await upload(owner, project["id"], small_zip(tmp_path))
    assert response.status_code == 202, response.text
    assert not (folder / "uploads").is_symlink()
    assert names(folder / "uploads") == [f"{response.json()['data']['job_id']}.archive"]
    assert names(outside) == ["keep.txt"]


# --- AG-13 delta at b3b1906 ---------------------------------------------------------------------------------


async def test_a_cancel_after_the_publish_keeps_the_marker_for_the_sweep(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    rename = os.rename

    def renamed_then_cancelled(src: Any, dst: Any) -> None:
        rename(src, dst)
        if Path(dst).name == "repo":
            raise asyncio.CancelledError()  # the cancel lands after the thread already renamed

    monkeypatch.setattr(os, "rename", renamed_then_cancelled)
    with pytest.raises(asyncio.CancelledError):
        await run_job(app, workspace_root, owner_id, project_id, job_id)
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    assert names(folder) == [f".import-{job_id}.published", "repo", "uploads"]
    assert (await upload(owner, project_id, small_zip(tmp_path))).status_code == 202  # not stuck at 409
    assert names(folder) == ["uploads"]
    assert await import_events(engine, project_id) == [("error", "import_incomplete")]


async def test_a_landed_commit_is_kept_even_if_the_state_changed_before_the_read_back(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    commit = AsyncSession.commit
    done: list[bool] = []

    async def committed_then_changed(self: AsyncSession) -> None:
        await commit(self)
        if done:
            return
        done.append(True)
        async with make_sessionmaker(engine)() as other:  # someone else (WP-2.1c) records a problem
            repository = await other.scalar(select(Repository).where(Repository.project_id == project_id))
            assert repository is not None
            repository.sync_state, repository.error = "error", "repository_unreadable"
            await commit(other)
        raise OperationalError("COMMIT", {}, ConnectionError("reply lost"))

    monkeypatch.setattr(AsyncSession, "commit", committed_then_changed)
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "ready"
    monkeypatch.undo()
    folder = project_folder(workspace_root, project_id)
    assert git(folder / "repo", "log", "--format=%s") == "Import from archive proj.zip"
    assert names(folder) == ["repo", "uploads"]


async def test_a_job_overtaken_after_the_sweep_gave_it_an_outcome_gets_no_second_one(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    project_id, old_job = await accepted_upload(owner, small_zip(tmp_path))
    await drop_lock(project_id)  # the key vanished (an AOF tail lost, an operator)
    response = await upload(owner, project_id, small_zip(tmp_path))
    assert response.status_code == 202, response.text
    assert await run_job(app, workspace_root, owner_id, project_id, old_job) == "skipped"
    async with make_sessionmaker(engine)() as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "project.import", AuditEvent.project_id == project_id
                )
            )
        ).all()
    assert [(e.metadata_["job_id"], e.metadata_["reason"]) for e in events] == [
        (str(old_job), "import_incomplete")
    ]


# --- AG-13 delta at c7222ee: the read-back waits for a commit still settling --------------------------------


async def settle_like_complete(engine: AsyncEngine, project_id: uuid.UUID, job_id: uuid.UUID) -> AsyncSession:
    """A transaction doing what ``_complete`` does, flushed to the server but not committed."""
    settling = make_sessionmaker(engine)()
    await settling.execute(select(Project).where(Project.id == project_id).with_for_update(key_share=True))
    repository = await settling.scalar(select(Repository).where(Repository.project_id == project_id))
    assert repository is not None and repository.sync_state == "importing"
    repository.sync_state, repository.error = "ready", None
    await AuditWriter(Redactor()).write(  # flushes the UPDATE and the INSERT: both on the server, uncommitted
        settling,
        actor_type="system",
        actor_id="project-import",
        action="project.import",
        result="success",
        resource_type="project",
        resource_id=str(project_id),
        project_id=project_id,
        metadata={"job_id": str(job_id)},
    )
    return settling


@pytest.mark.parametrize("lands", [True, False], ids=["commit_lands", "commit_rolls_back"])
async def test_the_read_back_waits_for_a_commit_still_settling(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
    lands: bool,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    env = import_env(app, workspace_root, RecordingQueue())
    job = project_imports._Job(project_id, job_id, workspace_root / str(project_id))  # pyright: ignore[reportPrivateUsage]
    settling = await settle_like_complete(engine, project_id, job_id)
    try:
        reading = asyncio.create_task(project_imports._is_ready(env, job))  # pyright: ignore[reportPrivateUsage]
        await asyncio.sleep(1.0)
        waited = not reading.done()  # FOR SHARE waits on the row the settling commit holds
        await (settling.commit() if lands else settling.rollback())
    finally:
        await settling.close()  # a failing run must not leave the row locked for the next test
    assert waited
    assert await reading is lands


async def test_a_reply_lost_while_the_commit_settles_keeps_the_repository(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_complete``'s COMMIT reply is lost while the same writes still settle in the database (modelled by a
    transaction that commits a second later): the landed commit keeps ``repo/``."""
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    commit = AsyncSession.commit
    is_ready = project_imports._is_ready  # pyright: ignore[reportPrivateUsage]
    lost: list[bool] = []
    landing: list[asyncio.Task[None]] = []

    async def reply_lost(self: AsyncSession) -> None:
        if not lost:
            lost.append(True)
            raise OperationalError("COMMIT", {}, ConnectionError("reply lost"))
        await commit(self)

    async def read_back_while_settling(env: project_imports.ImportEnv, job: Any) -> bool | None:
        settling = await settle_like_complete(engine, job.project_id, job.job_id)

        async def land() -> None:
            await asyncio.sleep(1.0)
            await commit(settling)
            await settling.close()

        landing.append(asyncio.create_task(land()))
        return await is_ready(env, job)

    monkeypatch.setattr(AsyncSession, "commit", reply_lost)
    monkeypatch.setattr(project_imports, "_is_ready", read_back_while_settling)
    try:
        outcome = await run_job(app, workspace_root, owner_id, project_id, job_id)
    finally:
        await asyncio.gather(*landing)  # the settling transaction always ends
        monkeypatch.undo()
    assert outcome == "ready"
    folder = project_folder(workspace_root, project_id)
    assert names(folder) == ["repo", "uploads"]
    assert git(folder / "repo", "log", "--format=%s") == "Import from archive proj.zip"
    assert await repository_state(owner, project_id) == ("ready", None)
