"""Repository index and search (WP-2.1d; FR-020, FR-021, FR-023, API_CONTRACTS §3). Framework-free.

**Index = one commit.** The job reads the HEAD of ``repo/`` from git's object database (``ls-tree`` for
files, one ``git grep -o`` for definition lines) and replaces the project's ``repo_index_entries`` in one
transaction that also moves ``repositories.index_commit``/``last_indexed_at`` and writes the audit event.
Readers see the old index until that commit. Every row names the commit it describes (CLAUDE.md #1).

**What it never writes:** ``repositories.sync_state`` and ``repositories.error`` (AG-13 entry condition
from WP-2.1b: only the import moves an upload project's state). A failed run is an audit event and a log
line; the previous index stays.

**One job per project:** ``index_lock`` (Redis). A request while a job is queued or running gets that job's
id back (``reused``); a job whose lock was lost does nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, func, insert, select, update

from app.core.errors import AppError
from app.core.logging import get_logger
from app.gitops.adapter import GitTarget
from app.models.projects import Project, RepoIndexEntry, Repository
from app.services import index_lock
from app.services.job_queue import JobQueue
from app.services.projects import Actor, ProjectEnv, enforce_rate, repo_path
from app.services.workspace_api import SANDBOX_DIR, open_repo
from app.workspace.index import SYMBOL_PATTERNS, classify, language_for

logger = get_logger(__name__)

Trigger = Literal["user", "create", "import"]
Outcome = Literal["indexed", "unchanged", "skipped", "error"]

# [Menebak] limits (spec Goal 4).
MAX_FILES = 50_000  # = ArchiveLimits.max_entries
MAX_SYMBOLS = 200_000
MAX_SYMBOLS_PER_FILE = 2_000
PATH_MAX_CHARS = 1000  # repo_index_entries.path varchar(1000)
SYMBOL_OUTPUT_MAX = 64 * 1024 * 1024
INDEX_GIT_TIMEOUT_S = 300.0
INSERT_BATCH = 2_000
SIZE_MAX = 2**31 - 1  # size_bytes is an int column
INDEX_REQUESTS_PER_HOUR = 10  # [Menebak]


@dataclass
class IndexEnv:
    projects: ProjectEnv
    redis: Redis | None
    jobs: JobQueue | None = None  # None in the worker, which runs index jobs and never queues them


@dataclass(frozen=True)
class IndexRequest:
    job_id: uuid.UUID
    reused: bool  # a job for this project was already queued or running: its id


def _unavailable() -> AppError:
    return AppError("DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable", http_status=503)


# --- requests -----------------------------------------------------------------------------------------------


async def request_index(
    env: IndexEnv, project_id: uuid.UUID, user_id: uuid.UUID, *, trigger: Trigger
) -> IndexRequest:
    """Queue an index run for ``project_id`` unless one is already queued or running. No authorisation here:
    callers authorise (``index_by_user``) or act for the system after a create or an import."""
    if env.redis is None or env.jobs is None:
        raise _unavailable()
    job_id = uuid.uuid4()
    try:
        for _ in range(2):  # the holder may expire between the two calls
            if await index_lock.acquire(env.redis, project_id, job_id):
                break
            if (held := await index_lock.holder(env.redis, project_id)) is not None:
                return IndexRequest(uuid.UUID(held), reused=True)
        else:
            raise _unavailable()
    except RedisError as exc:
        logger.error(
            "project_index_lock_unavailable", project_id=str(project_id), error_type=type(exc).__name__
        )
        raise _unavailable() from None
    try:
        await env.jobs.enqueue_index(project_id=project_id, job_id=job_id, user_id=user_id, trigger=trigger)
    except BaseException as exc:
        # Not handed over (or not known to be): free the lock, or every request reuses a job that never runs
        # until the lock expires. A job that was sent after all finds the lock gone and does nothing.
        with contextlib.suppress(RedisError):
            await index_lock.release(env.redis, project_id, job_id)
        if not isinstance(exc, Exception):
            raise
        logger.error(
            "project_index_enqueue_failed", project_id=str(project_id), error_type=type(exc).__name__
        )
        raise _unavailable() from None
    return IndexRequest(job_id, reused=False)


async def index_by_user(env: IndexEnv, actor: Actor, project_id: uuid.UUID) -> IndexRequest:
    """``POST /projects/{id}/index``: the repository must serve writes (``open_repo``), then a rate limit."""
    await open_repo(env.projects, actor, project_id, "workspace:write", write=True)
    await enforce_rate(
        env.projects,
        actor,
        scope="index",
        limit=INDEX_REQUESTS_PER_HOUR,
        window_s=3600,
        message="Too many index requests; retry later",
    )
    return await request_index(env, project_id, actor.user_id, trigger="user")


async def index_soon(env: IndexEnv, project_id: uuid.UUID, user_id: uuid.UUID, *, trigger: Trigger) -> None:
    """Best effort after a create or an import (UC-02): a failure is logged, never raised. The user can
    still ask with ``POST /index``."""
    try:
        await request_index(env, project_id, user_id, trigger=trigger)
    except Exception as exc:
        logger.error(
            "project_index_not_requested",
            project_id=str(project_id),
            trigger=trigger,
            error_type=type(exc).__name__,
        )


# --- the job ------------------------------------------------------------------------------------------------


@dataclass
class _Collected:
    rows: list[dict[str, Any]]
    files: int
    symbols: int
    skipped: int
    truncated: bool


async def run_index(
    env: IndexEnv, *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID, trigger: Trigger
) -> Outcome:
    """The worker job. ``skipped``: superseded, or nothing to index (project gone, repository not ready);
    ``error``: git or the database failed, audited, old index intact."""
    if env.redis is None:
        raise RuntimeError("the index job needs Redis for its lock")
    try:
        held = await index_lock.refresh(env.redis, project_id, job_id)
    except RedisError as exc:
        # Ownership unknown: running could overlap a newer job. Do nothing; the lock expires.
        logger.error(
            "project_index_lock_unavailable", project_id=str(project_id), error_type=type(exc).__name__
        )
        return "skipped"
    if not held:
        logger.info("project_index_superseded", project_id=str(project_id), job_id=str(job_id))
        return "skipped"
    try:
        return await _index(env.projects, project_id, user_id, trigger)
    finally:
        try:
            await index_lock.release(env.redis, project_id, job_id)
        except RedisError as exc:
            logger.error(
                "project_index_lock_not_released", project_id=str(project_id), error_type=type(exc).__name__
            )


async def _index(env: ProjectEnv, project_id: uuid.UUID, user_id: uuid.UUID, trigger: Trigger) -> Outcome:
    async with env.sessions() as session:
        project = await session.get(Project, project_id)
        repository = await session.scalar(select(Repository).where(Repository.project_id == project_id))
    if project is None or repository is None:
        logger.warning("project_index_project_gone", project_id=str(project_id))
        return "skipped"
    if repository.sync_state != "ready":
        logger.warning("project_index_repository_not_ready", project_id=str(project_id))
        return "skipped"
    root = repo_path(env.workspace_root, project_id)
    if not await asyncio.to_thread(_is_git_dir, root / ".git"):
        logger.error("project_index_repository_missing", project_id=str(project_id))
        await _audit_error(env, project_id, user_id, trigger, "repository_missing")
        return "error"
    target = GitTarget(root)
    try:
        head = await env.git.current_commit(target)
        if head is not None and head == repository.index_commit:
            return (
                "unchanged" if await _mark_unchanged(env, project_id, user_id, trigger, head) else "skipped"
            )
        collected = await _collect(env, target, head)
    except Exception as exc:
        reason = _reason(exc)
        if not isinstance(exc, AppError):
            logger.exception("project_index_git_failed", project_id=str(project_id))
        await _audit_error(env, project_id, user_id, trigger, reason)
        return "error"
    try:
        written = await _replace(env, project_id, user_id, trigger, head, collected)
    except Exception:
        logger.exception("project_index_write_failed", project_id=str(project_id))
        await _audit_error(env, project_id, user_id, trigger, "write_failed")
        return "error"
    return "indexed" if written else "skipped"


async def _collect(env: ProjectEnv, target: GitTarget, head: str | None) -> _Collected:
    if head is None:  # no commit yet: an empty index
        return _Collected([], 0, 0, 0, False)
    listing = await env.git.list_files(target, head)
    found = await env.git.grep(
        target,
        list(SYMBOL_PATTERNS),
        commit=head,
        fixed=False,
        ignore_case=False,
        only_matching=True,
        timeout_s=INDEX_GIT_TIMEOUT_S,
        max_output=SYMBOL_OUTPUT_MAX,
    )
    truncated = listing.truncated or found.truncated
    skipped = listing.skipped_paths  # paths left out; grep's count is of matches in those same files
    rows: list[dict[str, Any]] = []
    kept: set[str] = set()
    for file in listing.files:
        if _excluded(file.path):
            skipped += 1
            continue
        if len(kept) >= MAX_FILES:
            truncated = True
            break
        kept.add(file.path)
        rows.append(
            _row(
                head, file.path, "file", language=language_for(file.path), size_bytes=min(file.size, SIZE_MAX)
            )
        )
    per_file: Counter[str] = Counter()
    symbols = 0
    for match in found.matches:
        if match.path not in kept:
            continue
        symbol = classify(match.path, match.text)
        if symbol is None:
            continue
        if symbols >= MAX_SYMBOLS:
            truncated = True
            break
        if per_file[match.path] >= MAX_SYMBOLS_PER_FILE:
            truncated = True
            continue
        per_file[match.path] += 1
        symbols += 1
        rows.append(
            _row(
                head,
                match.path,
                "symbol",
                symbol=symbol.name,
                symbol_kind=symbol.kind,
                line=match.line,
                language=language_for(match.path),
            )
        )
    if truncated:
        logger.warning("project_index_truncated", files=len(kept), symbols=symbols)
    return _Collected(rows, len(kept), symbols, skipped, truncated)


def _excluded(path: str) -> bool:
    """Sandbox artefacts never enter the index (they never enter git either), nor paths the column cannot
    hold."""
    return path == SANDBOX_DIR or path.startswith(SANDBOX_DIR + "/") or len(path) > PATH_MAX_CHARS


def _row(commit: str, path: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {
        "path": path,
        "kind": kind,
        "symbol": None,
        "symbol_kind": None,
        "line": None,
        "language": None,
        "size_bytes": None,
        "sha256": None,  # ls-tree gives git object ids, not content sha256 (spec: Keputusan)
        "commit": commit,
        **fields,
    }


async def _locked_project(session: Any, project_id: uuid.UUID) -> Project | None:
    """The project row, locked as import locks it (projects before repositories: no deadlock with import)."""
    return await session.scalar(
        select(Project).where(Project.id == project_id).with_for_update(key_share=True)
    )


async def _replace(
    env: ProjectEnv,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    trigger: Trigger,
    head: str | None,
    collected: _Collected,
) -> bool:
    """Swap the whole index in one transaction; False when the project was deleted meanwhile."""
    async with env.sessions() as session:
        if await _locked_project(session, project_id) is None:
            logger.warning("project_index_project_gone", project_id=str(project_id))
            return False
        await session.execute(delete(RepoIndexEntry).where(RepoIndexEntry.project_id == project_id))
        rows = [{"project_id": project_id, **row} for row in collected.rows]
        for start in range(0, len(rows), INSERT_BATCH):
            await session.execute(insert(RepoIndexEntry), rows[start : start + INSERT_BATCH])
        await session.execute(
            update(Repository)
            .where(Repository.project_id == project_id)
            .values(index_commit=head, last_indexed_at=func.now())
        )
        await _audit(
            env,
            session,
            project_id,
            user_id,
            "success",
            {
                "commit": head,
                "files": collected.files,
                "symbols": collected.symbols,
                "skipped": collected.skipped,
                "truncated": collected.truncated,
                "unchanged": False,
                "trigger": trigger,
            },
        )
        await session.commit()
    return True


async def _mark_unchanged(
    env: ProjectEnv, project_id: uuid.UUID, user_id: uuid.UUID, trigger: Trigger, head: str
) -> bool:
    """HEAD is the indexed commit: nothing is rewritten; the check itself is recorded."""
    async with env.sessions() as session:
        if await _locked_project(session, project_id) is None:
            return False
        await session.execute(
            update(Repository).where(Repository.project_id == project_id).values(last_indexed_at=func.now())
        )
        await _audit(
            env,
            session,
            project_id,
            user_id,
            "success",
            {"commit": head, "unchanged": True, "trigger": trigger},
        )
        await session.commit()
    return True


async def _audit(
    env: ProjectEnv,
    session: Any,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    result: str,
    metadata: dict[str, Any],
) -> None:
    await env.audit.write(
        session,
        actor_type="user",
        actor_id=str(user_id),
        action="project.index",
        result=result,
        resource_type="project",
        resource_id=str(project_id),
        project_id=project_id,
        metadata=metadata,
    )


async def _audit_error(
    env: ProjectEnv, project_id: uuid.UUID, user_id: uuid.UUID, trigger: Trigger, reason: str
) -> None:
    try:
        await env.audit.write_detached(
            env.sessions,
            actor_type="user",
            actor_id=str(user_id),
            action="project.index",
            result="error",
            resource_type="project",
            resource_id=str(project_id),
            project_id=project_id,
            metadata={"reason": reason, "trigger": trigger},
        )
    except Exception:
        logger.exception("project_index_audit_failed", project_id=str(project_id), reason=reason)


def _reason(exc: BaseException) -> str:
    if isinstance(exc, AppError):
        details = exc.details or []
        reason = details[0].get("reason") if details else None
        if isinstance(reason, str):
            return reason
    return type(exc).__name__


def _is_git_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()
