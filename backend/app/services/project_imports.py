"""Projects from an archive (FR-015, AC-02; WP-2.1b). Framework-free.

**Accepting an upload** (the route drives these, in order):
1. ``begin_import``: authorise, check that the project can take an archive, rate limit, take the
   per-project lock (``import_lock``), clear what an earlier import left behind, make ``<project>/uploads``.
   Refusals happen here, before any body is read.
2. ``ArchiveSink``: the route feeds it the file part as it arrives; it writes ``<job_id>.part`` in batches,
   stops past ``IMPORT_MAX_BYTES``, and on ``close`` checks the magic bytes and renames it ``.archive``.
   The route calls ``keep_alive`` now and then so a slow upload keeps its lock.
3. ``finish_import``: under the project row lock, check again (the lock too), mark ``importing``, audit,
   commit, enqueue.
4. ``abort_import`` on any failure in between: the files go and the lock is released.

**Running it** is ``run_import`` (worker). Everything it writes on disk is named after its job:
``.import-<job_id>`` (extraction, git, stack detection) and that folder's staging. ``repo/`` appears only
when the job publishes it, by a rename inside the transaction that marks the project ``ready``. A job never
removes a ``repo/`` it did not publish; an interrupted import's ``repo/`` is cleared by the next upload,
which holds the lock while the project still waits for an archive.

**Outcomes.** ``ready`` and ``error`` are written to the project row with a ``project.import`` audit event in
the same transaction. ``skipped`` changes nothing: another run of the same job is in progress, the project
is no longer waiting (finished, deleted), or a newer upload took over. When the job cannot tell which
(Redis or the database did not answer), it raises ``ImportRetry`` and keeps its archive and lock for the
retry.

The name the client sent is only ever data (audit, commit message) after ``clean_filename``; the paths are
always derived from the job id.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import os
import shutil
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.logging import get_logger
from app.gitops.adapter import GitTarget
from app.gitops.errors import GitError
from app.models.audit import AuditEvent
from app.models.identity import User
from app.models.projects import Project, Repository
from app.services import import_lock
from app.services.job_queue import JobQueue
from app.services.projects import Actor, ProjectEnv, authorize, commit_author, enforce_rate
from app.workspace.archive import ArchiveLimits, ImportReport, import_archive
from app.workspace.errors import ArchiveRejected
from app.workspace.fs import Workspace
from app.workspace.stack import detect_stack

logger = get_logger(__name__)

IMPORT_MAX_BYTES = 200 * 1024 * 1024  # ADR-005
IMPORT_RATE_LIMIT = 10
IMPORT_RATE_WINDOW_S = 3600
FILENAME_MAX = 255
_WRITE_BATCH = 1024 * 1024
_SNIFF_BYTES = 512
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
DEFAULT_LIMITS = ArchiveLimits()  # ADR-005
_OPEN_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
# What an import writes next to ``repo/`` in the project folder: a job's work folder, its publish marker,
# its staging, a ``repo/`` set aside, and the staging of an extraction straight into ``repo/``.
_LEFTOVER_PREFIXES = (".import-", "..import-", ".repo.importing-")
_PUBLISHED = ".published"  # .import-<job_id>.published: written just before the rename to repo/
_WORKER = ("system", "project-import")
# Failures that say nothing about the archive: the job is retried, never failed, on these.
_TRANSIENT = (RedisError, SQLAlchemyError, ConnectionError, TimeoutError)
_READ_BACK_S = 30.0  # how long a commit whose reply was lost may still be settling in the database


@dataclass(frozen=True)
class ImportEnv:
    projects: ProjectEnv
    redis: Redis
    jobs: JobQueue | None = None  # None in the worker, which runs imports and never enqueues them


class ImportRetry(Exception):
    """The job could not tell what to do (Redis or the database failed): run it again later, files kept."""


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


def _refusal(project: Project, repository: Repository | None) -> str | None:
    if project.status != "active":
        return "project_archived"
    if repository is None or repository.provider != "upload":
        return "not_an_upload_project"
    if repository.sync_state not in ("importing", "error"):
        return "repository_not_empty"
    return None


def _waiting(project: Project | None, repository: Repository | None) -> bool:
    """The project still waits for an import: only then may a job write its outcome."""
    return (
        project is not None
        and project.status != "deleted"
        and repository is not None
        and repository.provider == "upload"
        and repository.sync_state == "importing"
    )


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


def _remove_paths(paths: list[Path], report: Callable[[str, BaseException], None]) -> bool:
    """Remove each path (file, link or folder); True when all of them are gone."""
    clean = True

    def failed(_function: object, path: str, exc: BaseException) -> None:
        nonlocal clean
        clean = False
        report(path, exc)

    for path in paths:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, onexc=failed)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            failed(None, str(path), exc)
    return clean


async def _write_audit_detached(env: ImportEnv, project_id: uuid.UUID, metadata: dict[str, Any]) -> None:
    try:
        await env.projects.audit.write_detached(
            env.projects.sessions,
            actor_type=_WORKER[0],
            actor_id=_WORKER[1],
            action="project.import",
            result="error",
            resource_type="project",
            resource_id=str(project_id),
            project_id=project_id,
            metadata=metadata,
        )
    except Exception:
        logger.exception("project_import_audit_failed", project_id=str(project_id))


# --- accepting an upload ------------------------------------------------------------------------------------


async def _load(env: ImportEnv, actor: Actor, project_id: uuid.UUID) -> tuple[Project, Repository | None]:
    async with env.projects.sessions() as session:
        project, _ = await authorize(session, actor, project_id, "project:write")
        repository = await session.scalar(select(Repository).where(Repository.project_id == project.id))
    return project, repository


async def _check(env: ImportEnv, actor: Actor, project_id: uuid.UUID) -> None:
    project, repository = await _load(env, actor, project_id)
    reason = _refusal(project, repository)
    if reason:
        logger.info("project_import_refused", project_id=str(project_id), reason=reason)
        raise _conflict(reason)


async def begin_import(env: ImportEnv, actor: Actor, project_id: uuid.UUID) -> ImportSlot:
    """Everything that can refuse the upload, before a byte of it is read."""
    await _check(env, actor, project_id)
    await enforce_rate(
        env.projects,
        actor,
        scope="project-import",
        limit=IMPORT_RATE_LIMIT,
        window_s=IMPORT_RATE_WINDOW_S,
        message="Too many archives uploaded; try again later",
    )
    slot = ImportSlot(project_id, uuid.uuid4(), env.projects.workspace_root / str(project_id) / "uploads")
    try:
        acquired = await import_lock.acquire(env.redis, project_id, slot.job_id)
    except RedisError as exc:
        logger.error(
            "project_import_lock_unavailable", project_id=str(project_id), error_type=type(exc).__name__
        )
        await _abort(env, slot)  # the SET may have landed before the error: never leave it to expire
        raise _unavailable() from None
    if not acquired:
        logger.info("project_import_refused", project_id=str(project_id), reason="import_running")
        raise _conflict("import_running")
    try:
        await _claim_folder(env, actor, slot)
        await _sweep(env, slot)
        await asyncio.to_thread(_make_uploads, slot.uploads)
    except BaseException:
        await abort_import(env, slot)
        raise
    return slot


async def _claim_folder(env: ImportEnv, actor: Actor, slot: ImportSlot) -> None:
    """Check the project again and set an unfinished import's ``repo/`` aside, under the project row lock.

    A job holds that row lock from its last lock check, through the rename to ``repo/``, to its ``ready``
    commit. Taking it here means a job either finished (``ready``: refused) or will find its lock gone, even
    if the Redis key vanished without expiring. Only a ``repo/`` with the publish marker of a job whose
    ``ready`` commit never landed is set aside: a job with a ``project.import`` success event committed its
    ``repo/`` (the event is written in the same transaction), so its marker, if one was left, proves nothing.
    Any other ``repo/`` is not an import's to remove (409 ``repository_not_empty``).
    """
    async with env.projects.sessions() as session:
        project, _ = await authorize(session, actor, slot.project_id, "project:write", lock=True)
        repository = await session.scalar(select(Repository).where(Repository.project_id == project.id))
        reason = _refusal(project, repository)
        if reason is None:
            committed = await _jobs_with_outcome(session, slot.project_id, result="success")
            if not await asyncio.to_thread(_set_aside_repo, slot.uploads.parent, committed):
                reason = "repository_not_empty"
        if reason:
            logger.info("project_import_refused", project_id=str(slot.project_id), reason=reason)
            raise _conflict(reason)


async def _jobs_with_outcome(
    session: AsyncSession, project_id: uuid.UUID, *, result: str | None = None
) -> set[uuid.UUID]:
    """The jobs of this project that have a ``project.import`` event (of ``result``, when given)."""
    statement = select(AuditEvent.metadata_).where(
        AuditEvent.project_id == project_id, AuditEvent.action == "project.import"
    )
    if result is not None:
        statement = statement.where(AuditEvent.result == result)
    jobs: set[uuid.UUID] = set()
    for metadata in await session.scalars(statement):
        with contextlib.suppress(TypeError, ValueError):
            jobs.add(uuid.UUID(str(metadata.get("job_id"))))
    return jobs


def _set_aside_repo(root: Path, committed: set[uuid.UUID]) -> bool:
    """Move an unfinished import's ``repo/`` out of the way (the sweep removes it); False when ``repo/``
    exists without the marker of a job that never committed."""
    repo = root / "repo"
    if not os.path.lexists(repo):
        return True
    markers = _dead_jobs([path for path in root.glob(f".import-*{_PUBLISHED}")])
    if not markers - committed:
        return False
    os.rename(repo, root / f".import-sweep-{uuid.uuid4().hex}")
    return True


async def _sweep(env: ImportEnv, slot: ImportSlot) -> None:
    """Clear what interrupted imports left: a dead upload's ``.part``, a dead job's archive, work folder,
    staging, publish marker, or the ``repo/`` set aside by ``_claim_folder``. Safe only here: the lock is
    held and the project is not ``ready``, so nothing else writes these. Each dead job found gets the
    ``project.import`` outcome it never wrote (``import_incomplete``)."""
    root = slot.uploads.parent

    def leftovers() -> list[Path]:
        if not root.is_dir():
            return []
        found = [entry for entry in root.iterdir() if entry.name.startswith(_LEFTOVER_PREFIXES)]
        if slot.uploads.is_symlink():
            found.append(slot.uploads)
        elif slot.uploads.is_dir():
            found.extend(slot.uploads.iterdir())
        return found

    def report(path: str, exc: BaseException) -> None:
        logger.error(
            "project_import_cleanup_failed",
            project_id=str(slot.project_id),
            path=os.path.relpath(path, env.projects.workspace_root),
            error_type=type(exc).__name__,
        )

    def sweep() -> tuple[int, set[uuid.UUID], bool]:
        found = leftovers()
        return len(found), _dead_jobs(found), _remove_paths(found, report)

    try:
        count, dead, clean = await asyncio.to_thread(sweep)
    except OSError as exc:
        report(str(root), exc)
        count, dead, clean = 0, set(), False
    if not clean:
        raise _conflict("cleanup_failed")
    if count:
        logger.warning("project_import_leftovers_removed", project_id=str(slot.project_id), count=count)
    if dead:
        try:
            async with env.projects.sessions() as session:
                dead -= await _jobs_with_outcome(session, slot.project_id)  # those wrote their own
        except Exception:
            logger.exception("project_import_audit_failed", project_id=str(slot.project_id))
            dead = set()
    for job_id in sorted(dead, key=str):
        await _write_audit_detached(
            env, slot.project_id, {"job_id": str(job_id), "reason": "import_incomplete"}
        )


def _dead_jobs(found: list[Path]) -> set[uuid.UUID]:
    """Accepted jobs these files belong to (an archive or a work folder; a ``.part`` was never accepted)."""
    jobs: set[uuid.UUID] = set()
    for path in found:
        name = path.name
        if name.endswith(".archive"):
            raw = name.removesuffix(".archive")
        elif name.startswith(".import-") and not name.startswith(".import-sweep-"):
            raw = name.removeprefix(".import-").removesuffix(_PUBLISHED)
        else:
            continue
        with contextlib.suppress(ValueError):
            jobs.add(uuid.UUID(raw))
    return jobs


def _make_uploads(uploads: Path) -> None:
    uploads.parent.mkdir(exist_ok=True)
    uploads.mkdir(mode=0o700, exist_ok=True)


async def keep_alive(env: ImportEnv, slot: ImportSlot) -> None:
    """Extend this upload's lock; 409 ``import_expired`` when it was lost, 503 when Redis does not answer."""
    try:
        held = await import_lock.refresh(env.redis, slot.project_id, slot.job_id)
    except RedisError as exc:
        logger.error(
            "project_import_lock_unavailable", project_id=str(slot.project_id), error_type=type(exc).__name__
        )
        raise _unavailable() from None
    if not held:
        logger.warning("project_import_lock_lost", project_id=str(slot.project_id), job_id=str(slot.job_id))
        raise _conflict("import_expired")


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
        reason = _refusal(project, repository)
        if reason:
            logger.info("project_import_refused", project_id=str(slot.project_id), reason=reason)
            raise _conflict(reason)
        assert repository is not None  # _refusal said "not an upload project" otherwise
        # Under the row lock: from here to the enqueue no other upload can mark this project.
        await keep_alive(env, slot)
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
        if env.jobs is None:
            raise RuntimeError("no job queue")
        await env.jobs.enqueue_import(
            project_id=slot.project_id, job_id=slot.job_id, user_id=actor.user_id, filename=upload.filename
        )
    except Exception as exc:
        logger.error(
            "project_import_enqueue_failed",
            project_id=str(slot.project_id),
            job_id=str(slot.job_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        await mark_failed(env, slot, "import_not_started", actor=actor)
        await abort_import(env, slot)
        raise _unavailable() from None
    return ImportAccepted(repository, slot.job_id, upload)


Settled = Literal["error", "superseded", "not_waiting"]


async def _settle_failure(
    env: ImportEnv,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    code: str,
    *,
    actor: tuple[str, str],
    ip: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Settled:
    """Write ``error=<code>`` and its ``project.import`` event in one transaction, if the project still
    waits for this job: ``error``; ``superseded`` when a newer upload holds the import; ``not_waiting`` when
    the import already ended (or the project is gone). Database and Redis errors propagate."""
    async with env.projects.sessions() as session:
        project = await session.scalar(
            select(Project).where(Project.id == project_id).with_for_update(key_share=True)
        )
        repository = await session.scalar(select(Repository).where(Repository.project_id == project_id))
        if not _waiting(project, repository):
            return "not_waiting"
        assert repository is not None
        # Under the row lock that finish_import also takes: a newer upload either holds the lock now, or
        # marks the project after this commit.
        if await import_lock.holder(env.redis, project_id) not in (None, str(job_id)):
            return "superseded"
        repository.sync_state = "error"
        repository.error = code
        await env.projects.audit.write(
            session,
            actor_type=actor[0],
            actor_id=actor[1],
            action="project.import",
            result="error",
            resource_type="project",
            resource_id=str(project_id),
            project_id=project_id,
            metadata={"job_id": str(job_id), "reason": code, **(metadata or {})},
            ip=ip,
        )
        await session.commit()
    return "error"


async def mark_failed(env: ImportEnv, slot: ImportSlot, code: str, *, actor: Actor) -> bool:
    """Record why an accepted upload did not start; True when written. Logged, never raised (the caller is
    already failing the request)."""
    try:
        settled = await _settle_failure(
            env, slot.project_id, slot.job_id, code, actor=("user", str(actor.user_id)), ip=actor.ip
        )
    except Exception:
        logger.exception("project_import_state_write_failed", project_id=str(slot.project_id), code=code)
        return False
    if settled != "error":
        logger.error("project_import_state_not_written", project_id=str(slot.project_id), why=settled)
    return settled == "error"


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
        # The next upload's sweep removes it.
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


# --- running it (worker) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Job:
    project_id: uuid.UUID
    job_id: uuid.UUID
    root: Path  # the project folder

    @property
    def slot(self) -> ImportSlot:
        return ImportSlot(self.project_id, self.job_id, self.root / "uploads")

    @property
    def archive(self) -> Path:
        return self.slot.archive

    @property
    def work(self) -> Path:
        """This job's own folder: extraction, git and stack detection happen here, never in ``repo``."""
        return self.root / f".import-{self.job_id}"

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def marker(self) -> Path:
        """Written just before the rename to ``repo/``, removed once the outcome is known: a ``repo/`` with a
        marker was published by an import whose ``ready`` commit may not have landed."""
        return self.root / f".import-{self.job_id}{_PUBLISHED}"

    def leftovers(self) -> list[Path]:
        """This job's work folder and its staging (``import_archive`` stages next to its destination)."""
        if not self.root.is_dir():
            return []
        return [self.work, *self.root.glob(f".{self.work.name}.importing-*")]


class _NotWaiting(Exception):
    """The project stopped waiting for this import (deleted, or the import already ended)."""


class _Lost(Exception):
    """This job no longer holds the project's import lock."""


@dataclass(frozen=True)
class _Outcome:
    report: ImportReport
    commit: str
    stack: dict[str, object]


async def run_import(
    env: ImportEnv,
    *,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    limits: ArchiveLimits = DEFAULT_LIMITS,
) -> str:
    """Turn the accepted archive into the project's repository: ``ready``, ``error`` or ``skipped``; raises
    ``ImportRetry`` when the outcome cannot be decided now (the archive and lock stay for the retry).

    Whatever else happens, this job's work folder goes, and unless a retry follows its archive is deleted and
    its lock released.
    """
    job = _Job(project_id, job_id, env.projects.workspace_root / str(project_id))
    try:
        claim = await asyncio.to_thread(_claim, job.archive)
    except BlockingIOError:
        # Another run of this job (an at-least-once redelivery) holds the archive: it owns every file here.
        logger.warning("project_import_duplicate_run", project_id=str(project_id), job_id=str(job_id))
        return "skipped"
    keep = False
    try:
        try:
            await _remove(env, job, await asyncio.to_thread(job.leftovers))  # an earlier, killed run
            refusal = await _still_ours(env, job, user_id, archive_present=claim is not None)
            if refusal is not None:
                return refusal
            return await _run(env, job, user_id, clean_filename(filename), limits)
        except ImportRetry:
            keep = True
            raise
        finally:
            await _remove(env, job, await asyncio.to_thread(job.leftovers))
            if not keep:
                await _abort(env, job.slot)
    finally:
        if claim is not None:
            os.close(claim)


def _claim(archive: Path) -> int | None:
    """Hold the job's archive for this run (an exclusive ``flock``, released when the process dies): its
    descriptor, or None when there is no archive. ``BlockingIOError`` when another run holds it.
    A ``flock`` is per host: tier S runs every worker on one node."""
    try:
        fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd


async def _still_ours(env: ImportEnv, job: _Job, user_id: uuid.UUID, *, archive_present: bool) -> str | None:
    """None when this job may run; otherwise its outcome."""
    try:
        held = await import_lock.refresh(env.redis, job.project_id, job.job_id)
    except RedisError as exc:
        logger.error(
            "project_import_lock_unknown", project_id=str(job.project_id), error_type=type(exc).__name__
        )
        raise ImportRetry() from None
    if not held:
        logger.warning("project_import_lock_lost", project_id=str(job.project_id), job_id=str(job.job_id))
        return await _fail(env, job, user_id, "import_expired")
    try:
        async with env.projects.sessions() as session:
            project = await session.get(Project, job.project_id)
            repository = await session.scalar(
                select(Repository).where(Repository.project_id == job.project_id)
            )
    except _TRANSIENT as exc:
        logger.error(
            "project_import_state_unknown", project_id=str(job.project_id), error_type=type(exc).__name__
        )
        raise ImportRetry() from None
    if not _waiting(project, repository):
        logger.warning("project_import_not_waiting", project_id=str(job.project_id), job_id=str(job.job_id))
        if repository is not None and repository.sync_state == "ready":
            await _remove(env, job, [job.marker])  # an earlier run's commit did land
        return "skipped"
    if not archive_present:
        # Held lock, waiting project, no archive: this worker does not see the upload volume.
        logger.error("project_import_archive_missing", project_id=str(job.project_id), job_id=str(job.job_id))
        return await _fail(env, job, user_id, "import_failed")
    return None


async def _run(env: ImportEnv, job: _Job, user_id: uuid.UUID, filename: str, limits: ArchiveLimits) -> str:
    try:
        await _take_back(env, job)
        outcome = await _import(env, job, user_id, filename, limits)
        return await _complete(env, job, outcome)
    except _NotWaiting:
        logger.warning("project_import_not_waiting", project_id=str(job.project_id), job_id=str(job.job_id))
        return "skipped"
    except _Lost:
        logger.warning("project_import_lock_lost", project_id=str(job.project_id), job_id=str(job.job_id))
        return await _fail(env, job, user_id, "import_expired")
    except ImportRetry:
        raise
    except _TRANSIENT as exc:
        logger.error(
            "project_import_dependency_failed", project_id=str(job.project_id), error_type=type(exc).__name__
        )
        raise ImportRetry() from None
    except ArchiveRejected as exc:
        code = f"archive_rejected:{exc.reason}"
    except GitError as exc:
        logger.error("project_import_git_failed", project_id=str(job.project_id), reason=exc.reason)
        code = "import_git_failed"
    except Exception:
        logger.exception("project_import_failed", project_id=str(job.project_id), job_id=str(job.job_id))
        code = "import_failed"
    return await _fail(env, job, user_id, code)


async def _take_back(env: ImportEnv, job: _Job) -> None:
    """An earlier run of this job published ``repo/`` and its ``ready`` commit's outcome was unknown: its
    marker proves that ``repo/`` is this job's. Under the project row lock (so a commit still settling in the
    database ends first) the project still waits and this job still holds the lock: the commit did not
    land. Take ``repo/`` back and start over."""
    if not await asyncio.to_thread(job.marker.exists):
        return

    def take_back() -> None:
        if os.path.lexists(job.repo):
            os.rename(job.repo, job.work)
        job.marker.unlink()

    async with env.projects.sessions() as session:
        project = await session.scalar(
            select(Project).where(Project.id == job.project_id).with_for_update(key_share=True)
        )
        repository = await session.scalar(select(Repository).where(Repository.project_id == job.project_id))
        if not _waiting(project, repository):
            raise _NotWaiting()  # it landed after all
        await _keep(env, job)
        await asyncio.to_thread(take_back)
    logger.warning(
        "project_import_publish_taken_back", project_id=str(job.project_id), job_id=str(job.job_id)
    )
    await _remove(env, job, [job.work])


async def _keep(env: ImportEnv, job: _Job) -> None:
    if not await import_lock.refresh(env.redis, job.project_id, job.job_id):
        raise _Lost()


async def _import(
    env: ImportEnv, job: _Job, user_id: uuid.UUID, filename: str, limits: ArchiveLimits
) -> _Outcome:
    async with env.projects.sessions() as session:
        user = await session.get(User, user_id)
        project = await session.get(Project, job.project_id)
    if project is None or project.status == "deleted":
        raise _NotWaiting()
    if user is None:
        raise RuntimeError("the uploader no longer exists")
    report = await asyncio.to_thread(
        import_archive, job.archive, job.work, limits=limits, strip_single_root=True
    )
    await _keep(env, job)  # extraction is the long step
    await env.projects.git.init(job.work, project.default_branch)
    author = commit_author(Actor(user.id, user.email, user.display_name, ()))
    commit = await env.projects.git.commit_all(GitTarget(job.work), f"Import from archive {filename}", author)
    if commit is None:  # an empty archive: nothing to commit, so no base for task worktrees
        raise ArchiveRejected("empty")
    stack = asdict(await detect_stack(Workspace(job.work)))
    return _Outcome(report, commit, stack)


async def _complete(env: ImportEnv, job: _Job, outcome: _Outcome) -> str:
    """Publish ``repo/`` and mark the project ``ready`` together: the rename happens inside the transaction
    (under the project row lock, which ``begin_import`` also takes before it sets anything aside) and is
    undone if the commit fails. The marker stays only while the commit's outcome is unknown."""
    async with env.projects.sessions() as session:
        project = await session.scalar(
            select(Project).where(Project.id == job.project_id).with_for_update(key_share=True)
        )
        repository = await session.scalar(select(Repository).where(Repository.project_id == job.project_id))
        if not _waiting(project, repository):
            raise _NotWaiting()
        assert project is not None and repository is not None
        await _keep(env, job)
        if await asyncio.to_thread(os.path.lexists, job.repo):
            # Not this job's: it publishes only here, and takes back its own earlier publish first.
            raise RuntimeError("the project already has a repo folder")
        await asyncio.to_thread(job.marker.touch)
        try:
            await asyncio.to_thread(os.rename, job.work, job.repo)
        except BaseException:
            await _remove(env, job, [job.marker])  # nothing was published
            raise
        try:
            project.stack = outcome.stack
            repository.sync_state = "ready"
            repository.error = None
            await env.projects.audit.write(
                session,
                actor_type=_WORKER[0],
                actor_id=_WORKER[1],
                action="project.import",
                result="success",
                resource_type="project",
                resource_id=str(job.project_id),
                project_id=job.project_id,
                metadata={
                    "job_id": str(job.job_id),
                    "files": outcome.report.files,
                    "dirs": outcome.report.dirs,
                    "bytes": outcome.report.bytes,
                    "stripped_prefix": outcome.report.stripped_prefix,
                    "commit": outcome.commit,
                },
            )
            await session.commit()
        except Exception:
            with contextlib.suppress(Exception):
                await session.rollback()  # the read-back below must not wait on this session's own locks
            ready = await _is_ready(env, job)
            if ready:
                logger.warning("project_import_commit_reply_lost", project_id=str(job.project_id))
                await _remove(env, job, [job.marker])
                return "ready"
            if ready is None:
                # Unknown whether the commit landed: keep ``repo/`` and its marker. The retry finds the
                # project ``ready`` (done) or still waiting (``_take_back``).
                logger.error("project_import_commit_unknown", project_id=str(job.project_id))
                raise ImportRetry() from None
            await asyncio.to_thread(os.rename, job.repo, job.work)
            await _remove(env, job, [job.marker])
            raise
    await _remove(env, job, [job.marker])
    logger.info("project_import_ready", project_id=str(job.project_id), files=outcome.report.files)
    return "ready"


async def _is_ready(env: ImportEnv, job: _Job) -> bool | None:
    """Did the ``ready`` commit land? ``FOR SHARE`` waits for a commit still settling in the database; None
    when the database does not answer in time."""
    try:
        async with asyncio.timeout(_READ_BACK_S), env.projects.sessions() as session:
            state = await session.scalar(
                select(Repository.sync_state)
                .where(Repository.project_id == job.project_id)
                .with_for_update(read=True)
            )
    except Exception:
        logger.exception("project_import_state_unknown", project_id=str(job.project_id))
        return None
    return state == "ready"


async def _fail(env: ImportEnv, job: _Job, user_id: uuid.UUID, code: str) -> str:
    """``error`` once written; ``skipped`` when the project is no longer this job's to fail."""
    logger.error("project_import_rejected", project_id=str(job.project_id), job_id=str(job.job_id), code=code)
    try:
        settled = await _settle_failure(
            env, job.project_id, job.job_id, code, actor=_WORKER, metadata={"user_id": str(user_id)}
        )
    except Exception as exc:
        logger.error(
            "project_import_state_write_failed",
            project_id=str(job.project_id),
            code=code,
            error_type=type(exc).__name__,
        )
        raise ImportRetry() from None
    if settled == "superseded":
        logger.warning("project_import_superseded", project_id=str(job.project_id), job_id=str(job.job_id))
        await _write_audit_detached(
            env,
            job.project_id,
            {"job_id": str(job.job_id), "reason": "import_superseded", "user_id": str(user_id)},
        )
        return "skipped"
    if settled == "not_waiting":
        logger.warning("project_import_not_waiting", project_id=str(job.project_id), job_id=str(job.job_id))
        return "skipped"
    return "error"


async def _remove(env: ImportEnv, job: _Job, paths: list[Path]) -> None:
    """Remove this job's own files; a failure is logged (the next upload's sweep removes them)."""

    def report(path: str, exc: BaseException) -> None:
        logger.error(
            "project_import_cleanup_failed",
            project_id=str(job.project_id),
            path=os.path.relpath(path, env.projects.workspace_root),
            error_type=type(exc).__name__,
        )

    if paths:
        await asyncio.to_thread(_remove_paths, paths, report)
