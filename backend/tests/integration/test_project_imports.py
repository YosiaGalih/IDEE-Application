"""Projects from an archive (FR-015, AC-02 import part; WP-2.1b) with real PostgreSQL, Redis and git.

The worker is not started: tests call ``project_imports.run_import`` where the queue would deliver the job.
Malicious archives through the same path are in ``tests/security/test_archive_import_http.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
import zipfile
from pathlib import Path
from typing import Any

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
from app.gitops.adapter import GitAdapter
from app.gitops.errors import GitError
from app.models.audit import AuditEvent
from app.services import project_imports, projects
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
    tmp_path: Path,
) -> None:
    project_id, job_id = await accepted_upload(owner, small_zip(tmp_path))
    await hold_lock(project_id, "a-newer-upload")  # this job's lock expired and someone else took it
    assert await run_job(app, workspace_root, owner_id, project_id, job_id) == "skipped"
    assert not (workspace_root / str(project_id) / "repo").exists()
    assert await held(project_id) == "a-newer-upload"  # never freed by a job that does not own it
    data = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
    assert data["repository"]["sync_state"] == "importing"


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
