"""Projects from an archive (FR-015, AC-02; WP-2.1b). Framework-free.

**Accepting an upload** (the route drives these, in order):
1. ``begin_import``: authorise, rate limit, check that the project can take an archive, take the per-project
   lock (``import_lock``) and make ``<project>/uploads``. Refusals happen here, before any body is read.
2. ``ArchiveSink``: the route feeds it the file part as it arrives; it writes ``<job_id>.part`` in batches,
   stops past ``IMPORT_MAX_BYTES``, and on ``close`` checks the magic bytes and renames it ``.archive``.
3. ``finish_import``: under the project row lock, check again, mark ``importing``, audit, commit, enqueue.
4. ``abort_import`` on any failure in between: the files go and the lock is released.

**Running it** is ``run_import`` (worker, Task 4 of the plan).

The name the client sent is only ever data (audit, commit message) after ``clean_filename``; the path is
always ``<job_id>.archive``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.projects import Project, Repository
from app.services import import_lock
from app.services.job_queue import JobQueue
from app.services.projects import Actor, ProjectEnv, authorize, enforce_rate, repo_path

logger = get_logger(__name__)

IMPORT_MAX_BYTES = 200 * 1024 * 1024  # ADR-005
IMPORT_RATE_LIMIT = 10
IMPORT_RATE_WINDOW_S = 3600
FILENAME_MAX = 255
_WRITE_BATCH = 1024 * 1024
_SNIFF_BYTES = 512
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_OPEN_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


@dataclass(frozen=True)
class ImportEnv:
    projects: ProjectEnv
    redis: Redis
    jobs: JobQueue


@dataclass(frozen=True)
class ImportSlot:
    """One accepted upload in flight: its job id and where its bytes go."""

    project_id: uuid.UUID
    job_id: uuid.UUID
    uploads: Path

    @property
    def part(self) -> Path:
        return self.uploads / f"{self.job_id}.part"

    @property
    def archive(self) -> Path:
        return self.uploads / f"{self.job_id}.archive"


@dataclass(frozen=True)
class UploadInfo:
    filename: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ImportAccepted:
    repository: Repository
    job_id: uuid.UUID
    upload: UploadInfo


# --- helpers ------------------------------------------------------------------------------------------------


def clean_filename(raw: str | None) -> str:
    """The base name the client sent, as data: no folders, nothing hidden, at most 255 characters."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c) not in _HIDDEN_CATEGORIES).strip()
    return name[:FILENAME_MAX] or "archive"


def _conflict(reason: str) -> AppError:
    return AppError(
        "CONFLICT", "The project cannot take an archive now", http_status=409, details=[{"reason": reason}]
    )


def _unavailable() -> AppError:
    return AppError("DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable", http_status=503)


def _refusal(project: Project, repository: Repository | None, repo_exists: bool) -> str | None:
    if project.status == "archived":
        return "project_archived"
    if repository is None or repository.provider != "upload":
        return "not_an_upload_project"
    if repository.sync_state not in ("importing", "error") or repo_exists:
        return "repository_not_empty"
    return None


def _sniff(head: bytes) -> str | None:
    """The archive kind from its first bytes (the same signatures ``workspace.archive`` accepts)."""
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "application/zip"
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if head[257:262] == b"ustar":
        return "application/x-tar"
    return None


def _detected_mime(head: bytes) -> str:
    if not head:
        return "application/x-empty"
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain" if all(c.isprintable() or c in "\r\n\t" for c in text) else "application/octet-stream"


# --- accepting an upload ------------------------------------------------------------------------------------


async def _load(
    env: ImportEnv, actor: Actor, project_id: uuid.UUID, *, lock: bool
) -> tuple[Project, Repository | None]:
    async with env.projects.sessions() as session:
        project, _ = await authorize(session, actor, project_id, "project:write", lock=lock)
        repository = await session.scalar(select(Repository).where(Repository.project_id == project.id))
    return project, repository


async def _repo_exists(env: ImportEnv, project_id: uuid.UUID) -> bool:
    return await asyncio.to_thread(os.path.lexists, repo_path(env.projects.workspace_root, project_id))


async def begin_import(env: ImportEnv, actor: Actor, project_id: uuid.UUID) -> ImportSlot:
    """Everything that can refuse the upload, before a byte of it is read."""
    await enforce_rate(
        env.projects,
        actor,
        scope="project-import",
        limit=IMPORT_RATE_LIMIT,
        window_s=IMPORT_RATE_WINDOW_S,
        message="Too many archives uploaded; try again later",
    )
    project, repository = await _load(env, actor, project_id, lock=False)
    reason = _refusal(project, repository, await _repo_exists(env, project_id))
    if reason:
        raise _conflict(reason)
    job_id = uuid.uuid4()
    try:
        acquired = await import_lock.acquire(env.redis, project_id, job_id)
    except RedisError as exc:
        logger.error(
            "project_import_lock_unavailable", project_id=str(project_id), error_type=type(exc).__name__
        )
        raise _unavailable() from None
    if not acquired:
        raise _conflict("import_running")
    slot = ImportSlot(project_id, job_id, env.projects.workspace_root / str(project_id) / "uploads")
    try:
        await asyncio.to_thread(_make_uploads, slot.uploads)
    except BaseException:
        await abort_import(env, slot)
        raise
    return slot


def _make_uploads(uploads: Path) -> None:
    uploads.parent.mkdir(exist_ok=True)
    uploads.mkdir(mode=0o700, exist_ok=True)


class ArchiveSink:
    """The file part of an upload, written to ``<job_id>.part`` as it arrives."""

    def __init__(self, slot: ImportSlot) -> None:
        self._slot = slot
        self._limit = IMPORT_MAX_BYTES  # read here, so a test can lower it
        self._buffer = bytearray()
        self._head = bytearray()
        self._size = 0
        self._digest = hashlib.sha256()

    async def write(self, chunk: bytes) -> None:
        self._size += len(chunk)
        if self._size > self._limit:
            raise AppError(
                "PAYLOAD_TOO_LARGE",
                "The archive is larger than the limit",
                http_status=413,
                details=[{"max_bytes": self._limit}],
            )
        self._digest.update(chunk)
        if len(self._head) < _SNIFF_BYTES:
            self._head += chunk[: _SNIFF_BYTES - len(self._head)]
        self._buffer += chunk
        if len(self._buffer) >= _WRITE_BATCH:
            await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        data = bytes(self._buffer)
        self._buffer.clear()
        await asyncio.to_thread(_append, self._slot.part, data)

    async def close(self, filename: str | None) -> UploadInfo:
        await self._flush()
        head = bytes(self._head)
        if _sniff(head) is None:
            raise AppError(
                "UNSUPPORTED_FILE_TYPE",
                "Only zip, tar and tar.gz archives are accepted",
                http_status=400,
                details=[{"detected_mime": _detected_mime(head)}],
            )
        await asyncio.to_thread(os.replace, self._slot.part, self._slot.archive)
        return UploadInfo(clean_filename(filename), self._size, self._digest.hexdigest())


def _append(path: Path, data: bytes) -> None:
    fd = os.open(path, _OPEN_FLAGS, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)


async def finish_import(env: ImportEnv, actor: Actor, slot: ImportSlot, upload: UploadInfo) -> ImportAccepted:
    async with env.projects.sessions() as session:
        project, _ = await authorize(session, actor, slot.project_id, "project:write", lock=True)
        repository = await session.scalar(select(Repository).where(Repository.project_id == project.id))
        reason = _refusal(project, repository, await _repo_exists(env, slot.project_id))
        if reason:
            raise _conflict(reason)
        assert repository is not None  # _refusal said "not an upload project" otherwise
        repository.sync_state = "importing"
        repository.error = None
        await env.projects.audit.write(
            session,
            actor_type="user",
            actor_id=str(actor.user_id),
            action="project.import_requested",
            result="success",
            resource_type="project",
            resource_id=str(project.id),
            project_id=project.id,
            metadata={
                "job_id": str(slot.job_id),
                "filename": upload.filename,
                "size": upload.size,
                "sha256": upload.sha256,
            },
            ip=actor.ip,
        )
        await session.commit()
    try:
        await env.jobs.enqueue_import(project_id=slot.project_id, job_id=slot.job_id, user_id=actor.user_id)
    except Exception as exc:
        logger.error(
            "project_import_enqueue_failed",
            project_id=str(slot.project_id),
            job_id=str(slot.job_id),
            error_type=type(exc).__name__,
        )
        await mark_failed(env, slot.project_id, "import_not_started")
        await abort_import(env, slot)
        raise _unavailable() from None
    return ImportAccepted(repository, slot.job_id, upload)


async def mark_failed(env: ImportEnv, project_id: uuid.UUID, code: str) -> None:
    """Record why an import did not finish; logged, never raised (the caller is already failing)."""
    try:
        async with env.projects.sessions() as session:
            repository = await session.scalar(
                select(Repository).where(Repository.project_id == project_id).with_for_update()
            )
            if (
                repository is not None
                and repository.provider == "upload"
                and repository.sync_state != "ready"
            ):
                repository.sync_state = "error"
                repository.error = code
                await session.commit()
    except Exception:
        logger.exception("project_import_state_write_failed", project_id=str(project_id), code=code)


async def abort_import(env: ImportEnv, slot: ImportSlot) -> None:
    """Undo an accepted upload: its files go, its lock is released. Runs to its end even if cancelled."""
    await asyncio.shield(asyncio.ensure_future(_abort(env, slot)))


async def _abort(env: ImportEnv, slot: ImportSlot) -> None:
    def remove() -> None:
        for path in (slot.part, slot.archive):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

    try:
        await asyncio.to_thread(remove)
    except OSError as exc:
        logger.error(
            "project_import_cleanup_failed",
            project_id=str(slot.project_id),
            job_id=str(slot.job_id),
            error_type=type(exc).__name__,
        )
    try:
        await import_lock.release(env.redis, slot.project_id, slot.job_id)
    except RedisError as exc:
        # It expires on its own (LOCK_TTL_S); until then a new upload is refused as "import_running".
        logger.error(
            "project_import_lock_release_failed",
            project_id=str(slot.project_id),
            error_type=type(exc).__name__,
        )
