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
import shutil
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from app.core.errors import AppError
from app.core.logging import get_logger
from app.gitops.adapter import GitTarget
from app.gitops.errors import GitError
from app.models.identity import User
from app.models.projects import Project, Repository
from app.services import import_lock
from app.services.job_queue import JobQueue
from app.services.projects import Actor, ProjectEnv, authorize, commit_author, enforce_rate, repo_path
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


@dataclass(frozen=True)
class ImportEnv:
    projects: ProjectEnv
    redis: Redis
    jobs: JobQueue | None = None  # None in the worker, which runs imports and never enqueues them


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


# --- running it (worker) ------------------------------------------------------------------------------------


@dataclass
class _Job:
    project_id: uuid.UUID
    job_id: uuid.UUID
    archive: Path
    repo: Path
    repo_created: bool = False  # set the moment extraction made ``repo``: only then may this job remove it


class _Gone(Exception):
    """The project stopped being this job's to finish (deleted, or no longer waiting for this import)."""


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
    """Turn the accepted archive into the project's repository: ``ready``, ``error`` or ``skipped``.

    ``skipped`` changes nothing: the job is stale (its lock expired or belongs to a newer upload) or was
    delivered again after it finished. Whatever happens, this job's archive is deleted and its lock released,
    and a repository this job extracted but did not finish is removed.
    """
    root = env.projects.workspace_root / str(project_id)
    job = _Job(project_id, job_id, root / "uploads" / f"{job_id}.archive", root / "repo")
    finished = False
    try:
        if not await _still_ours(env, job):
            return "skipped"
        try:
            outcome = await _import(env, job, user_id, clean_filename(filename), limits)
            await _complete(env, job, outcome)
            finished = True
            return "ready"
        except _Gone:
            logger.warning("project_import_gone", project_id=str(project_id), job_id=str(job_id))
            return "skipped"
        except ArchiveRejected as exc:
            code = f"archive_rejected:{exc.reason}"
        except GitError as exc:
            logger.error("project_import_git_failed", project_id=str(project_id), reason=exc.reason)
            code = "import_git_failed"
        except Exception:
            logger.exception("project_import_failed", project_id=str(project_id), job_id=str(job_id))
            code = "import_failed"
        await _remove_repo(env, job)
        await _fail(env, job, user_id, code)
        return "error"
    finally:
        if job.repo_created and not finished:
            await _remove_repo(env, job)  # cancelled or gone mid-way: no half repository stays
        await _abort(env, ImportSlot(project_id, job_id, job.archive.parent))


async def _still_ours(env: ImportEnv, job: _Job) -> bool:
    try:
        holder = await import_lock.holder(env.redis, job.project_id)
    except RedisError as exc:
        # Unknown whether this job still owns the import: do nothing. The lock expires and the project then
        # reads as import_incomplete, which the user can retry.
        logger.error(
            "project_import_lock_unknown", project_id=str(job.project_id), error_type=type(exc).__name__
        )
        return False
    if holder != str(job.job_id):
        logger.warning("project_import_stale_job", project_id=str(job.project_id), job_id=str(job.job_id))
        return False
    async with env.projects.sessions() as session:
        repository = await session.scalar(select(Repository).where(Repository.project_id == job.project_id))
    waiting = (
        repository is not None and repository.provider == "upload" and repository.sync_state == "importing"
    )
    if not waiting or not await asyncio.to_thread(job.archive.is_file):
        logger.warning("project_import_not_waiting", project_id=str(job.project_id), job_id=str(job.job_id))
        return False
    if await asyncio.to_thread(os.path.lexists, job.repo):
        # Only this job's own earlier attempt (it holds the lock) can have left a repository here mid-way.
        logger.warning("project_import_leftover_removed", project_id=str(job.project_id))
        job.repo_created = True
        await _remove_repo(env, job)
    return True


async def _import(
    env: ImportEnv, job: _Job, user_id: uuid.UUID, filename: str, limits: ArchiveLimits
) -> _Outcome:
    async with env.projects.sessions() as session:
        user = await session.get(User, user_id)
        project = await session.get(Project, job.project_id)
    if user is None or project is None or project.status == "deleted":
        raise _Gone()
    report = await asyncio.to_thread(
        import_archive, job.archive, job.repo, limits=limits, strip_single_root=True
    )
    job.repo_created = True
    await env.projects.git.init(job.repo, project.default_branch)
    author = commit_author(Actor(user.id, user.email, user.display_name, ()))
    commit = await env.projects.git.commit_all(GitTarget(job.repo), f"Import from archive {filename}", author)
    if commit is None:  # an empty archive: nothing to commit, so no base for task worktrees
        raise ArchiveRejected("empty")
    stack = asdict(await detect_stack(Workspace(job.repo)))
    return _Outcome(report, commit, stack)


async def _complete(env: ImportEnv, job: _Job, outcome: _Outcome) -> None:
    async with env.projects.sessions() as session:
        project = await session.scalar(
            select(Project).where(Project.id == job.project_id).with_for_update(key_share=True)
        )
        repository = await session.scalar(select(Repository).where(Repository.project_id == job.project_id))
        if (
            project is None
            or project.status == "deleted"
            or repository is None
            or repository.sync_state != "importing"
        ):
            raise _Gone()
        project.stack = outcome.stack
        repository.sync_state = "ready"
        repository.error = None
        await env.projects.audit.write(
            session,
            actor_type="system",
            actor_id="project-import",
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
    logger.info("project_import_ready", project_id=str(job.project_id), files=outcome.report.files)


async def _fail(env: ImportEnv, job: _Job, user_id: uuid.UUID, code: str) -> None:
    logger.error("project_import_rejected", project_id=str(job.project_id), job_id=str(job.job_id), code=code)
    await mark_failed(env, job.project_id, code)
    try:
        await env.projects.audit.write_detached(
            env.projects.sessions,
            actor_type="system",
            actor_id="project-import",
            action="project.import",
            result="error",
            resource_type="project",
            resource_id=str(job.project_id),
            project_id=job.project_id,
            metadata={"job_id": str(job.job_id), "reason": code, "user_id": str(user_id)},
        )
    except Exception:
        logger.exception("project_import_audit_failed", project_id=str(job.project_id))


async def _remove_repo(env: ImportEnv, job: _Job) -> None:
    """Remove the repository this job extracted (never one it did not make)."""
    if not job.repo_created:
        return

    def report(_function: object, path: str, exc: BaseException) -> None:
        logger.error(
            "project_import_cleanup_failed",
            project_id=str(job.project_id),
            path=os.path.relpath(path, env.projects.workspace_root),
            error_type=type(exc).__name__,
        )

    def remove() -> None:
        if os.path.lexists(job.repo):
            shutil.rmtree(job.repo, onexc=report)

    try:
        await asyncio.to_thread(remove)
    except OSError:
        logger.exception("project_import_cleanup_failed", project_id=str(job.project_id))
    job.repo_created = False
