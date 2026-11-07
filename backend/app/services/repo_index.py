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
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import case, delete, func, insert, select, update

from app.core.errors import AppError
from app.core.logging import get_logger
from app.gitops.adapter import GitTarget
from app.gitops.errors import GitError, GitInputError
from app.models.projects import Project, RepoIndexEntry, Repository
from app.services import index_lock
from app.services.job_queue import JobQueue
from app.services.projects import Actor, ProjectEnv, enforce_rate, repo_path
from app.services.workspace_api import SANDBOX_DIR, open_repo
from app.workspace.index import (
    SYMBOL_PATTERNS,
    check_glob,
    check_regex,
    classify,
    glob_matches,
    language_for,
    preview,
)
from app.workspace.policy import Access, Policy

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
MAX_SYMBOL_MATCHES = 2 * MAX_SYMBOLS  # parsed grep matches, cross-language noise included
INDEX_DEADLINE_S = 15 * 60.0  # [Menebak] the whole run, inside the worker's 20 min time limit
MAX_ROUNDS = 3  # one run plus the rounds that requests asked for meanwhile
SIZE_MAX = 2**31 - 1  # size_bytes is an int column
INDEX_REQUESTS_PER_HOUR = 10  # [Menebak]

# Search (spec Goals 7-9).
SEARCH_LIMIT_MAX = 200  # API_CONTRACTS §3 "maks 200"
TEXT_TIMEOUT_S = 10.0  # [Menebak] git is killed past this: a pathological regex costs at most this much
TEXT_OUTPUT_MAX = 1024 * 1024  # AG-13 M2: parse and filter time scale with it
TEXT_SEARCHES_PER_MINUTE = 60  # [Menebak] each text search starts one git process
MAX_TEXT_SEARCHES = 2  # [Menebak] git greps running at once in this process (AG-13 M1); the rest is 429
SYMBOL_SEARCHES_PER_MINUTE = 120  # [Menebak] each symbol search scans the project's symbols (AG-13 L3)
SYMBOL_CANDIDATES = 5_000


@dataclass
class IndexEnv:
    projects: ProjectEnv
    redis: Redis | None
    jobs: JobQueue | None = None  # the worker queues one only to hand over a last-round request


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
            # A job holds the lock; it is asked for one more round, since it may have read HEAD already.
            if (held := await index_lock.reuse(env.redis, project_id)) is not None:
                return IndexRequest(uuid.UUID(held), reused=True)
        else:
            raise _unavailable()
    except BaseException as exc:
        # The SET may have landed although its reply did not (timeout, cancel): free a lock this request may
        # hold, or later requests reuse a job that never runs until the lock expires (silent-failure M1).
        with contextlib.suppress(Exception):
            await index_lock.release(env.redis, project_id, job_id)
        if not isinstance(exc, Exception) or isinstance(exc, AppError):
            raise
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
            exc_info=not isinstance(exc, AppError),
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
        held = await index_lock.begin_round(env.redis, project_id, job_id)
    except RedisError as exc:
        # Ownership unknown: running could overlap a newer job. Do nothing; the lock expires.
        logger.error(
            "project_index_lock_unavailable", project_id=str(project_id), error_type=type(exc).__name__
        )
        return "skipped"
    if not held:
        logger.info("project_index_superseded", project_id=str(project_id), job_id=str(job_id))
        return "skipped"
    outcome: Outcome = "skipped"
    rounds = 0
    hand_over = False
    try:
        async with asyncio.timeout(INDEX_DEADLINE_S):
            while True:
                rounds += 1
                outcome = await _index(env.projects, project_id, user_id, trigger)
                # A request that came in meanwhile asked for one more round (the lock stays ours for it).
                if not await index_lock.finish(env.redis, project_id, job_id):
                    break
                if rounds >= MAX_ROUNDS:
                    # That request was answered with this job: a new job serves it (AG-13 D2).
                    hand_over = True
                    break
                if not await index_lock.begin_round(env.redis, project_id, job_id):
                    break
    except TimeoutError:
        # Inside a git command or a DB write: both stop (git killed, transaction rolled back).
        logger.error("project_index_deadline", project_id=str(project_id), job_id=str(job_id))
        await _audit_error(env.projects, project_id, user_id, trigger, "time_limit")
        outcome = "error"
    except RedisError as exc:
        logger.error(
            "project_index_lock_unavailable", project_id=str(project_id), error_type=type(exc).__name__
        )
    finally:
        try:
            await index_lock.release(env.redis, project_id, job_id)
        except RedisError as exc:
            logger.error(
                "project_index_lock_not_released", project_id=str(project_id), error_type=type(exc).__name__
            )
    if hand_over:
        await index_soon(env, project_id, user_id, trigger=trigger)  # logged when it fails
    return outcome


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
        # Strict: None only for a branch with no commit yet. A HEAD git cannot read is an error, never an
        # empty index (silent-failure H1).
        head = await env.git.head_commit(target)
        if head is None and repository.index_commit is not None:
            raise GitError("head_unreadable")  # the indexed branch lost its commits: not a state to index
        if head is not None and head == repository.index_commit and not repository.index_truncated:
            return (
                "unchanged" if await _mark_unchanged(env, project_id, user_id, trigger, head) else "skipped"
            )
        collected = await _collect(env, target, head)
    except Exception as exc:
        reason = _reason(exc)
        if isinstance(exc, AppError):
            logger.error("project_index_git_failed", project_id=str(project_id), reason=reason)
        else:
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
        max_matches=MAX_SYMBOL_MATCHES,
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
            .values(index_commit=head, last_indexed_at=func.now(), index_truncated=collected.truncated)
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


# --- search -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchHit:
    path: str
    line: int
    preview: str
    symbol: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit]
    truncated: bool  # more matched than returned (limit, output cap, candidate cap)
    index_commit: str | None  # symbol mode: the commit the hits come from
    indexed_at: datetime | None
    skipped: int = 0  # text mode: hits in files whose names are not UTF-8, left out (silent-failure M4)
    index_truncated: bool | None = None  # symbol mode: the index itself stopped at a limit (H2)


# Text searches running now, in this process and per user (AG-13 M1). Plain counters: the event loop is single
# threaded, and checking then counting has no await in between, so a full slot answers 429 and never queues.
_text_running: dict[uuid.UUID, int] = {}


def _busy() -> AppError:
    return AppError(
        "RATE_LIMITED",
        "Another search is still running; retry",
        http_status=429,
        headers={"Retry-After": "1"},
    )


@contextlib.contextmanager
def _text_slot(user_id: uuid.UUID) -> Any:
    if user_id in _text_running or sum(_text_running.values()) >= MAX_TEXT_SEARCHES:
        raise _busy()
    _text_running[user_id] = 1
    try:
        yield
    finally:
        _text_running.pop(user_id, None)


def _invalid(field: str, message: str, reason: str | None = None) -> AppError:
    detail: dict[str, Any] = {"loc": ["query", field], "msg": message}
    if reason is not None:
        detail["reason"] = reason
    return AppError("VALIDATION_ERROR", "Input did not pass validation", http_status=422, details=[detail])


async def search(
    env: ProjectEnv,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    q: str,
    mode: Literal["text", "symbol"],
    glob: str | None,
    regex: bool,
    ignore_case: bool,
    kind: str | None,
    limit: int,
    access: Access = Access.USER_READ,
) -> SearchResult:
    """FR-020 (text: the working tree as it is now) and FR-021 (symbols: the index). Hits in paths the
    ``access`` may not read are dropped (FR-020 denylist; ``.agentignore`` for agents), as are ``.kk/``."""
    if glob is not None:
        try:
            check_glob(glob)
        except ValueError as exc:
            raise _invalid("glob", str(exc)) from None
    if mode == "text" and regex:
        try:
            check_regex(q)
        except ValueError as exc:
            raise _invalid("q", str(exc), "regex_unsupported") from None
    limit = min(max(limit, 1), SEARCH_LIMIT_MAX)
    repo = await open_repo(env, actor, project_id, "workspace:read", write=False)
    policy = await repo.workspace.policy(access)
    if mode == "text":
        await enforce_rate(
            env,
            actor,
            scope="search_text",
            limit=TEXT_SEARCHES_PER_MINUTE,
            window_s=60,
            message="Too many searches; retry later",
        )
        with _text_slot(actor.user_id):
            return await _search_text(env, repo.target, policy, access, q, glob, regex, ignore_case, limit)
    await enforce_rate(
        env,
        actor,
        scope="search_symbol",
        limit=SYMBOL_SEARCHES_PER_MINUTE,
        window_s=60,
        message="Too many searches; retry later",
    )
    return await _search_symbols(env, project_id, policy, access, q, glob, kind, limit)


def _visible(path: str, glob: str | None, policy: Policy, access: Access) -> bool:
    if _excluded(path):
        return False
    if glob is not None and not glob_matches(glob, path):
        return False
    return not policy.denied(PurePosixPath(path).parts, access)


async def _search_text(
    env: ProjectEnv,
    target: GitTarget,
    policy: Policy,
    access: Access,
    q: str,
    glob: str | None,
    regex: bool,
    ignore_case: bool,
    limit: int,
) -> SearchResult:
    try:
        found = await env.git.grep(
            target,
            [q],
            commit=None,
            fixed=not regex,
            ignore_case=ignore_case,
            only_matching=False,
            timeout_s=TEXT_TIMEOUT_S,
            max_output=TEXT_OUTPUT_MAX,
            threads=1,  # one CPU per search, not one per host CPU (AG-13 M1)
        )
    except GitInputError as exc:
        if exc.reason == "regex":
            raise _invalid("q", "not a valid POSIX extended regular expression", "invalid_regex") from None
        raise

    def keep() -> tuple[list[SearchHit], bool]:
        hits: list[SearchHit] = []
        for match in found.matches:
            if not _visible(match.path, glob, policy, access):
                continue
            if len(hits) >= limit:
                return hits, True
            hits.append(SearchHit(match.path, match.line, preview(match.text, match.column)))
        return hits, found.truncated

    # Filtering tens of thousands of matches (glob, policy) is CPU work: off the event loop (AG-13 M2).
    hits, truncated = await asyncio.to_thread(keep)
    return SearchResult(hits, truncated, None, None, skipped=found.skipped_paths)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _search_symbols(
    env: ProjectEnv,
    project_id: uuid.UUID,
    policy: Policy,
    access: Access,
    q: str,
    glob: str | None,
    kind: str | None,
    limit: int,
) -> SearchResult:
    needle = q.lower()
    pattern = _like_escape(needle)
    name = func.lower(RepoIndexEntry.symbol)
    query = (
        select(RepoIndexEntry.path, RepoIndexEntry.line, RepoIndexEntry.symbol, RepoIndexEntry.symbol_kind)
        .where(
            RepoIndexEntry.project_id == project_id,
            RepoIndexEntry.kind == "symbol",
            name.like(f"%{pattern}%", escape="\\"),
        )
        .order_by(
            case((name == needle, 0), (name.like(f"{pattern}%", escape="\\"), 1), else_=2),
            RepoIndexEntry.path,
            RepoIndexEntry.line,
        )
        .limit(SYMBOL_CANDIDATES + 1)  # one more than kept: "truncated" only when more exist (backend N4)
    )
    if kind is not None:
        query = query.where(RepoIndexEntry.symbol_kind == kind)
    async with env.sessions() as session:
        # One snapshot for the hits and the commit they are reported under: an index swapped in between the
        # two reads must not label old hits with the new commit (backend N4).
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        repository = await session.scalar(select(Repository).where(Repository.project_id == project_id))
        rows = (await session.execute(query)).all()
    hits: list[SearchHit] = []
    truncated = len(rows) > SYMBOL_CANDIDATES
    for path, line, symbol, symbol_kind in rows[:SYMBOL_CANDIDATES]:
        if not _visible(path, glob, policy, access):
            continue
        if len(hits) >= limit:
            truncated = True
            break
        hits.append(SearchHit(path, line or 0, f"{symbol_kind} {symbol}", symbol, symbol_kind))
    return SearchResult(
        hits,
        truncated,
        repository.index_commit if repository is not None else None,
        repository.last_indexed_at if repository is not None else None,
        index_truncated=repository.index_truncated if repository is not None else None,
    )
