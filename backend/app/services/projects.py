"""Projects (FR-012-018, API_CONTRACTS §3): create, list, read, update, archive. Framework-free.

**Scope.** Every operation authorises again here (SECURITY_MODEL §8: the route is only the first door). A
project outside the actor's scope, or one that is ``deleted``, raises ``NOT_FOUND``, never ``FORBIDDEN``, so
its existence never leaks. Inside the scope, a missing permission is ``FORBIDDEN``.

**Workspace.** The repository lives at ``WORKSPACE_ROOT/<project_id>/repo`` (ARCHITECTURE §7). The name the
user typed never reaches a path or a git argument: the folder is the project id, and the name is only file
content written from Python (README) or a template placeholder.

**All or nothing.** The rows are written in one transaction. The folder is created after the rows are
flushed and removed again if anything after that fails. When the commit itself fails, the database decides:
the folder stays if the row exists after all, and goes if it does not. No row without a folder, and no
folder without a row.

**No git inside a transaction.** Reads and updates close their session before git runs, so a slow disk
never holds a connection or a row lock.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import os
import re
import shutil
import unicodedata
import uuid
from collections.abc import AsyncIterator, Coroutine
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import FixedWindowLimiter, RateLimiterUnavailable
from app.core.rbac import RoleGrant, effective_permissions
from app.gitops.adapter import Author, GitAdapter, GitTarget
from app.gitops.errors import GitError
from app.models.projects import Project, Repository
from app.services.task_stats import open_task_count
from app.workspace.fs import Workspace
from app.workspace.stack import detect_stack
from app.workspace.templates_registry import TemplateInfo, list_templates, materialize

logger = get_logger(__name__)

NAME_MAX = 120
DESCRIPTION_MAX = 2000
# templates_registry accepts slugs up to 63 characters; room is kept for a "-<n>" suffix.
SLUG_BASE_MAX = 56
DEFAULT_SLUG = "project"
SLUG_LOCK_TIMEOUT_S = 10
# Creates run git and copy files while holding a DB connection; beyond this many at once they wait in
# CreateGate, without a connection, instead of draining the pool for every other request.
CREATE_CONCURRENCY = 4
# Per user: enough for real work, too few to fill the disk or the pool (no quota yet: FR-153 is P1).
CREATE_RATE_LIMIT = 20
CREATE_RATE_WINDOW_S = 600
# Cleanups of failed creates run as tasks so a second cancel cannot stop them halfway; held here until done.
_settling: set[asyncio.Task[None]] = set()
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200
_NAME_CHARACTER = re.compile(r"\w", re.UNICODE)
SETTINGS_KEYS = frozenset({"plan_approval_required", "auto_remediation"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Controls, format characters (bidi overrides, zero-width) and line/paragraph separators: a name holding
# them can display as another project's name (SECURITY_MODEL §5: approvals show the name).
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_SLUG_SEPARATOR = re.compile(r"[^a-z0-9]+")

StatusFilter = Literal["active", "archived", "all"]


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    email: str
    display_name: str
    grants: tuple[RoleGrant, ...]
    ip: str | None = None  # recorded with every audit event, as auth does


class CreateGate:
    """Bounds project creates in one process: one at a time per owner, ``CREATE_CONCURRENCY`` in all.

    A waiter holds no DB connection. One per app (``Dependencies``): asyncio primitives belong to a loop.
    Across processes, the per-owner advisory lock still serialises the slug choice.
    """

    def __init__(self, concurrency: int = CREATE_CONCURRENCY) -> None:
        self._slots = asyncio.Semaphore(concurrency)
        self._owners: dict[uuid.UUID, tuple[asyncio.Lock, int]] = {}

    @contextlib.asynccontextmanager
    async def turn(self, owner_id: uuid.UUID) -> AsyncIterator[None]:
        lock, users = self._owners.get(owner_id, (asyncio.Lock(), 0))
        self._owners[owner_id] = (lock, users + 1)
        try:
            async with lock, self._slots:
                yield
        finally:
            lock, users = self._owners[owner_id]
            if users == 1:
                del self._owners[owner_id]
            else:
                self._owners[owner_id] = (lock, users - 1)


@dataclass(frozen=True)
class ProjectEnv:
    sessions: async_sessionmaker[AsyncSession]
    audit: AuditWriter
    git: GitAdapter
    workspace_root: Path
    creates: CreateGate = field(default_factory=CreateGate)
    limiter: FixedWindowLimiter | None = None  # None only where no HTTP caller exists (tests, workers)


@dataclass(frozen=True)
class BlankSource:
    pass


@dataclass(frozen=True)
class TemplateSource:
    template_id: str


@dataclass(frozen=True)
class RemoteSource:
    url: str


Source = BlankSource | TemplateSource | RemoteSource


@dataclass(frozen=True)
class ProjectStats:
    files: int | None  # from the index (WP-2.1d); None until it exists
    last_commit: str | None
    open_tasks: int
    # Why the repository could not be read ("repository_missing", ...); never folded into a null commit.
    repository_problem: str | None = None


@dataclass(frozen=True)
class ProjectView:
    project: Project
    repository: Repository | None
    permissions: frozenset[str]
    stats: ProjectStats | None  # only on a single project: it costs a git call
    repository_problem: str | None = None  # in a list, where only the folder is checked

    @property
    def problem(self) -> str | None:
        return self.stats.repository_problem if self.stats is not None else self.repository_problem


# --- errors -----------------------------------------------------------------------------------------------


def not_found() -> AppError:
    return AppError("NOT_FOUND", "Not found", http_status=404)


def _invalid(field: str, message: str, *, where: str = "body") -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": [where, field], "msg": message}],
    )


# --- pure helpers -------------------------------------------------------------------------------------------


def has_hidden_characters(value: str) -> bool:
    return any(unicodedata.category(character) in _HIDDEN_CATEGORIES for character in value)


def name_is_valid(cleaned: str) -> bool:
    return 1 <= len(cleaned) <= NAME_MAX and not has_hidden_characters(cleaned)


def clean_name(name: str) -> str:
    cleaned = name.strip()
    if not name_is_valid(cleaned):
        raise _invalid("name", f"must be 1-{NAME_MAX} characters without control or invisible characters")
    return cleaned


def clean_description(description: str | None) -> str | None:
    if description is None:
        return None
    if len(description) > DESCRIPTION_MAX or "\x00" in description:
        raise _invalid("description", f"must be at most {DESCRIPTION_MAX} characters")
    return description


def slugify(name: str) -> str:
    """ASCII, lower case, ``-`` between words; ``project`` when nothing usable is left."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    slug = _SLUG_SEPARATOR.sub("-", ascii_name).strip("-")[:SLUG_BASE_MAX].strip("-")
    return slug or DEFAULT_SLUG


def permissions_for(actor: Actor, project: Project) -> frozenset[str]:
    if project.status == "deleted":
        return frozenset()
    return effective_permissions(
        actor.grants, user_id=actor.user_id, project_id=project.id, project_owner_id=project.owner_id
    )


def _visible(value: str) -> str:
    """Without ``<``, ``>`` (git refuses them in an identity) and without hidden characters."""
    return "".join(
        character
        for character in value
        if character not in "<>" and unicodedata.category(character) not in _HIDDEN_CATEGORIES
    )


def _author(actor: Actor) -> Author:
    """The user as commit author, as git accepts it and as a reader sees it."""
    email = _visible(actor.email) or f"user-{actor.user_id}@kodekantor.invalid"
    name = _visible(actor.display_name).strip()
    # git refuses a name made only of punctuation ("name consists only of disallowed characters").
    if not _NAME_CHARACTER.search(name):
        name = email.split("@", 1)[0] if _NAME_CHARACTER.search(email.split("@", 1)[0]) else "KodeKantor user"
    return Author(name, email)


def repo_path(root: Path, project_id: uuid.UUID) -> Path:
    return root / str(project_id) / "repo"


# --- authorisation ----------------------------------------------------------------------------------------


async def authorize(
    session: AsyncSession, actor: Actor, project_id: uuid.UUID, permission: str, *, lock: bool = False
) -> tuple[Project, frozenset[str]]:
    project = await session.scalar(select(Project).where(Project.id == project_id))
    permissions = permissions_for(actor, project) if project is not None else frozenset()
    if project is None or not permissions:
        raise not_found()
    if permission not in permissions:
        raise AppError("FORBIDDEN", "Not allowed", http_status=403)
    if lock:
        # Only after the decision, so a stranger never holds a lock on someone else's project. FOR NO KEY
        # UPDATE: rows that reference the project (tasks) can still be inserted meanwhile.
        project = await session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if project is None:
            raise not_found()
        permissions = permissions_for(actor, project)
        if permission not in permissions:
            raise not_found() if not permissions else AppError("FORBIDDEN", "Not allowed", http_status=403)
    return project, permissions


# --- create -----------------------------------------------------------------------------------------------


async def _free_slug(session: AsyncSession, owner_id: uuid.UUID, base: str) -> str:
    # Every row counts, deleted ones too: UNIQUE (owner_id, slug) covers them.
    taken = set(
        await session.scalars(
            select(Project.slug).where(
                Project.owner_id == owner_id,
                or_(Project.slug == base, Project.slug.startswith(f"{base}-", autoescape=True)),
            )
        )
    )
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


async def _populate(
    git: GitAdapter,
    repo: Path,
    source: BlankSource | TemplateSource,
    *,
    name: str,
    slug: str,
    branch: str,
    author: Author,
) -> str:
    """Files, ``git init``, first commit; returns that commit."""
    if isinstance(source, TemplateSource):
        await asyncio.to_thread(materialize, source.template_id, repo, project_name=name, project_slug=slug)
        message = f"Initial commit from template {source.template_id}"
    else:
        await asyncio.to_thread(repo.mkdir)
        # A first commit, so a task worktree has a base to branch from (FR-114).
        await asyncio.to_thread((repo / "README.md").write_text, f"# {name}\n", "utf-8")
        message = "Initial commit"
    await git.init(repo, branch)
    commit = await git.commit_all(GitTarget(repo), message, author)
    if commit is None:
        raise GitError("first_commit_missing")
    return commit


async def _discard_folder(
    env: ProjectEnv, root: Path, project_id: uuid.UUID, *, commit_attempted: bool
) -> None:
    """Remove the folder of a create that failed, unless its row exists after all (ambiguous commit)."""
    if commit_attempted:
        # A COMMIT can reach the server and still raise here (connection lost on the reply, cancel): only
        # the database knows whether the project exists.
        try:
            async with env.sessions() as session:
                exists = await session.scalar(select(Project.id).where(Project.id == project_id))
        except Exception:
            # Unknown whether the row exists: keeping the folder is the safe side; say so loudly.
            logger.exception(
                "project_create_cleanup_skipped", project_id=str(project_id), reason="row_state_unknown"
            )
            return
        if exists is not None:
            logger.error("project_create_cleanup_skipped", project_id=str(project_id), reason="row_exists")
            return

    def report(_function: object, path: str, exc: BaseException) -> None:
        logger.error(
            "project_create_cleanup_failed",
            project_id=str(project_id),
            path=os.path.relpath(path, env.workspace_root),
            error_type=type(exc).__name__,
        )

    if await asyncio.to_thread(os.path.lexists, root):
        await asyncio.to_thread(shutil.rmtree, root, onexc=report)


async def _create_failed(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, stage: str, exc: BaseException
) -> None:
    """A failed create leaves no row, so its trace is a log line and a detached audit event."""
    reason = exc.reason if isinstance(exc, GitError) else type(exc).__name__
    logger.error("project_create_failed", project_id=str(project_id), stage=stage, reason=reason)
    try:
        await env.audit.write_detached(
            env.sessions,
            actor_type="user",
            actor_id=str(actor.user_id),
            action="project.create",
            result="error",
            resource_type="project",
            resource_id=str(project_id),
            metadata={"stage": stage, "reason": reason},
            ip=actor.ip,
        )
    except Exception:
        logger.exception("project_create_audit_failed", project_id=str(project_id))


async def _settle_failed_create(
    env: ProjectEnv,
    actor: Actor,
    project_id: uuid.UUID,
    root: Path,
    *,
    stage: str,
    exc: BaseException,
    folder_may_exist: bool,
) -> None:
    if not (isinstance(exc, AppError) and exc.code == "RATE_LIMITED"):
        await _create_failed(env, actor, project_id, stage, exc)
    if folder_may_exist:
        await _discard_folder(env, root, project_id, commit_attempted=stage == "commit")


async def _settle(coroutine: Coroutine[Any, Any, None]) -> None:
    """Run a cleanup to its end even if the request is cancelled again while it runs."""
    task = asyncio.create_task(coroutine)
    _settling.add(task)
    task.add_done_callback(_settling.discard)
    await asyncio.shield(task)  # a cancel here leaves the task running; it still finishes the cleanup


async def create_project(
    env: ProjectEnv,
    actor: Actor,
    *,
    name: str,
    description: str | None,
    source: Source,
    default_branch: str,
) -> ProjectView:
    await _enforce_create_rate(env, actor)
    async with env.creates.turn(actor.user_id):
        return await _create_project(
            env, actor, name=name, description=description, source=source, default_branch=default_branch
        )


async def _enforce_create_rate(env: ProjectEnv, actor: Actor) -> None:
    if env.limiter is None:
        return
    try:
        decision = await env.limiter.hit(
            f"kk:rl:project-create:{actor.user_id}",
            limit=CREATE_RATE_LIMIT,
            window_seconds=CREATE_RATE_WINDOW_S,
        )
    except RateLimiterUnavailable:
        # Fail closed, as login does: an unenforced limit is not a limit.
        raise AppError(
            "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable", http_status=503
        ) from None
    if not decision.allowed:
        logger.warning("rate_limited", scope="project-create", user_id=str(actor.user_id))
        raise AppError(
            "RATE_LIMITED",
            "Too many projects created; try again later",
            http_status=429,
            headers={"Retry-After": str(decision.retry_after)},
        )


async def _create_project(
    env: ProjectEnv,
    actor: Actor,
    *,
    name: str,
    description: str | None,
    source: Source,
    default_branch: str,
) -> ProjectView:
    name = clean_name(name)
    description = clean_description(description)
    if isinstance(source, RemoteSource):
        # FR-016 is P1 and needs internet egress, which waits for gate G4 (conflict #65).
        try:
            await env.audit.write_detached(
                env.sessions,
                actor_type="user",
                actor_id=str(actor.user_id),
                action="project.create",
                result="denied",
                resource_type="project",
                metadata={"source": "git_https", "reason": "remote_import_disabled"},
                ip=actor.ip,
            )
        except Exception:
            # The refusal stands either way; a lost audit event is logged, never turned into a 500.
            logger.exception("project_create_denied_audit_failed", user_id=str(actor.user_id))
        raise AppError(
            "POLICY_DENIED",
            "Importing a remote repository is not enabled",
            http_status=403,
            details=[{"reason": "remote_import_disabled"}],
        )
    if isinstance(source, TemplateSource) and source.template_id not in template_ids():
        raise _invalid("source", "unknown template")

    project_id = uuid.uuid4()
    root = env.workspace_root / str(project_id)
    repo = repo_path(env.workspace_root, project_id)
    provider = "template" if isinstance(source, TemplateSource) else "local"
    stage = "database"
    # Set before mkdir: a cancelled await can still create the folder in its thread.
    folder_may_exist = False
    try:
        async with env.sessions() as session:
            try:
                # Bounded wait: one stuck create must not hold the owner's other creates forever.
                # set_config(..., true) is SET LOCAL with a bound value (no SQL built from strings).
                await session.execute(
                    text("SELECT set_config('lock_timeout', :timeout, true)"),
                    {"timeout": f"{SLUG_LOCK_TIMEOUT_S}s"},
                )
                # Serialise creations by the same owner so two requests never pick the same slug.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"project-slug:{actor.user_id}"},
                )
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) == "55P03":  # lock_not_available
                    raise AppError(
                        "RATE_LIMITED",
                        "Another project of yours is being created; try again shortly",
                        http_status=429,
                        headers={"Retry-After": str(SLUG_LOCK_TIMEOUT_S)},
                    ) from None
                raise
            slug = await _free_slug(session, actor.user_id, slugify(name))
            project = Project(
                id=project_id,
                owner_id=actor.user_id,
                name=name,
                slug=slug,
                description=description,
                workspace_ref=str(project_id),
                default_branch=default_branch,
                status="active",
                settings={},
                stack={},
            )
            session.add(project)
            await session.flush()
            repository = Repository(
                project_id=project_id, provider=provider, branch=default_branch, sync_state="ready"
            )
            session.add(repository)
            await session.flush()
            stage = "workspace"
            folder_may_exist = True
            await asyncio.to_thread(root.mkdir)  # a fresh id: an existing folder is a bug, so no exist_ok
            stage = "git"
            first_commit = await _populate(
                env.git, repo, source, name=name, slug=slug, branch=default_branch, author=_author(actor)
            )
            stage = "stack"
            project.stack = asdict(await detect_stack(Workspace(repo)))
            metadata: dict[str, Any] = {
                "source": "template" if isinstance(source, TemplateSource) else "blank"
            }
            if isinstance(source, TemplateSource):
                metadata["template_id"] = source.template_id
            stage = "commit"
            await _audit(env, session, actor, project, "project.create", metadata)
            await _load_updated_at(session, project)
            await session.commit()
    except BaseException as exc:
        # Also when the session's own rollback raised: this block runs whatever left the `async with`.
        await _settle(
            _settle_failed_create(
                env, actor, project_id, root, stage=stage, exc=exc, folder_may_exist=folder_may_exist
            )
        )
        if isinstance(exc, GitError):
            # Infrastructure, not the request: a 5xx, which the idempotency store never keeps (a 409 would
            # be replayed for 24 h to a retry with the same key).
            raise AppError("GIT_ERROR", "Git operation failed", http_status=500, details=exc.details) from exc
        raise
    # Built from what was just written: a read after the commit could fail and report an error for a project
    # that exists (the client would then retry and create a second one).
    return ProjectView(
        project,
        repository,
        permissions_for(actor, project),
        ProjectStats(files=None, last_commit=first_commit, open_tasks=0, repository_problem=None),
    )


# --- read -------------------------------------------------------------------------------------------------


async def _repository_check(env: ProjectEnv, project_id: uuid.UUID) -> tuple[str | None, str | None]:
    """(last commit, problem). A problem is logged at error level, never turned into a quiet null.

    It never raises: it runs after a mutation is committed, where an exception would report a failure for
    a change that was made.
    """
    try:
        return await _read_repository(env, project_id)
    except Exception:
        logger.exception("project_repository_unreadable", project_id=str(project_id), reason="unexpected")
        return None, "repository_unreadable"


async def _read_repository(env: ProjectEnv, project_id: uuid.UUID) -> tuple[str | None, str | None]:
    repo = repo_path(env.workspace_root, project_id)
    if not await asyncio.to_thread((repo / ".git").is_dir):
        logger.error("project_repository_missing", project_id=str(project_id))
        return None, "repository_missing"
    try:
        commit = await env.git.current_commit(GitTarget(repo))
    except GitError as exc:
        logger.error("project_repository_unreadable", project_id=str(project_id), reason=exc.reason)
        if exc.reason == "git_metadata_unsafe":  # rewritten git metadata: a tamper signal (ADR-026)
            await _audit_unsafe(env, project_id)
            return None, "repository_unsafe"
        return None, "repository_unreadable"
    if commit is None:
        logger.error("project_repository_without_commit", project_id=str(project_id))
        return None, "repository_without_commit"
    return commit, None


async def _audit_unsafe(env: ProjectEnv, project_id: uuid.UUID) -> None:
    try:
        await env.audit.write_detached(
            env.sessions,
            actor_type="system",
            actor_id="projects",
            action="project.repository_unsafe",
            result="error",
            resource_type="project",
            resource_id=str(project_id),
            project_id=project_id,
            metadata={"reason": "git_metadata_unsafe"},
        )
    except Exception:
        # The read goes on; the error log above already names the project.
        logger.exception("project_repository_unsafe_audit_failed", project_id=str(project_id))


async def _stats(env: ProjectEnv, project_id: uuid.UUID, open_tasks: int) -> ProjectStats:
    """The git half of the stats, run with no session open."""
    last_commit, problem = await _repository_check(env, project_id)
    return ProjectStats(
        files=None, last_commit=last_commit, open_tasks=open_tasks, repository_problem=problem
    )


async def _missing_repositories(env: ProjectEnv, project_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Projects whose repository folder is gone: one stat per project, in one thread."""

    def check() -> set[uuid.UUID]:
        return {pid for pid in project_ids if not (repo_path(env.workspace_root, pid) / ".git").is_dir()}

    missing = await asyncio.to_thread(check)
    for project_id in missing:
        logger.error("project_repository_missing", project_id=str(project_id))
    return missing


async def get_project(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, with_stats: bool = True
) -> ProjectView:
    async with env.sessions() as session:
        project, permissions = await authorize(session, actor, project_id, "project:read")
        repository = await session.scalar(select(Repository).where(Repository.project_id == project.id))
        open_tasks = await open_task_count(session, project.id) if with_stats else 0
    stats = await _stats(env, project.id, open_tasks) if with_stats else None
    return ProjectView(project, repository, permissions, stats)


@dataclass(frozen=True)
class ProjectPage:
    items: list[ProjectView]
    page: int
    page_size: int
    total: int


async def list_projects(
    env: ProjectEnv,
    actor: Actor,
    *,
    status: StatusFilter = "active",
    q: str | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
) -> ProjectPage:
    if page < 1 or not 1 <= page_size <= PAGE_SIZE_MAX:
        raise _invalid("page_size", f"page >= 1 and page_size 1-{PAGE_SIZE_MAX}", where="query")
    if q is not None and _CONTROL.search(q):
        raise _invalid("q", "must not contain control characters", where="query")
    granted = {grant.project_id for grant in actor.grants if grant.project_id is not None}
    # The visibility rule of core/rbac.py: the owner, or a role scoped to the project.
    conditions = [
        or_(Project.owner_id == actor.user_id, Project.id.in_(granted)),
        Project.status != "deleted",
    ]
    if status != "all":
        conditions.append(Project.status == status)
    if q:
        conditions.append(
            or_(Project.name.icontains(q, autoescape=True), Project.slug.icontains(q, autoescape=True))
        )
    statement = (
        select(Project, Repository)
        .outerjoin(Repository, Repository.project_id == Project.id)
        .where(*conditions)
        .order_by(Project.updated_at.desc(), Project.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    async with env.sessions() as session:
        total = await session.scalar(select(func.count()).select_from(Project).where(*conditions)) or 0
        rows = (await session.execute(statement)).all()
    ready = [
        project.id
        for project, repository in rows
        if repository is not None and repository.sync_state == "ready"
    ]
    missing = await _missing_repositories(env, ready)
    items = [
        ProjectView(
            project,
            repository,
            permissions_for(actor, project),
            None,
            repository_problem="repository_missing" if project.id in missing else None,
        )
        for project, repository in rows
    ]
    return ProjectPage(items=items, page=page, page_size=page_size, total=total)


@functools.cache
def _template_catalog() -> tuple[TemplateInfo, ...]:
    # The templates ship with the image and never change while it runs: read them once.
    return tuple(list_templates())


def templates() -> list[TemplateInfo]:
    return list(_template_catalog())


def template_ids() -> frozenset[str]:
    return frozenset(template.id for template in _template_catalog())


async def load_templates() -> None:
    """Read the catalog off the event loop once, at startup."""
    await asyncio.to_thread(_template_catalog)


# --- update -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectChanges:
    """Only the fields the request carried: ``fields`` says which, so ``description=None`` can clear it."""

    fields: frozenset[str]
    name: str | None = None
    description: str | None = None
    settings: dict[str, Any] | None = None


async def _audit(
    env: ProjectEnv,
    session: AsyncSession,
    actor: Actor,
    project: Project,
    action: str,
    metadata: dict[str, Any],
) -> None:
    await env.audit.write(
        session,
        actor_type="user",
        actor_id=str(actor.user_id),
        action=action,
        result="success",
        resource_type="project",
        resource_id=str(project.id),
        project_id=project.id,
        metadata=metadata,
        ip=actor.ip,
    )


async def update_project(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, changes: ProjectChanges
) -> ProjectView:
    unknown = changes.fields - {"name", "description", "settings"}
    if unknown:
        raise _invalid(sorted(unknown)[0], "cannot be changed")
    async with env.sessions() as session:
        project, permissions = await authorize(session, actor, project_id, "project:write", lock=True)
        changed: set[str] = set()
        if "name" in changes.fields:
            name = clean_name(changes.name or "")  # the slug stays: it is the stable id (FR-012)
            if name != project.name:
                project.name = name
                changed.add("name")
        if "description" in changes.fields:
            description = clean_description(changes.description)
            if description != project.description:
                project.description = description
                changed.add("description")
        changed_settings: dict[str, bool] = {}
        if "settings" in changes.fields:
            incoming = changes.settings or {}
            bad = set(incoming) - SETTINGS_KEYS
            if bad or not all(isinstance(value, bool) for value in incoming.values()):
                raise _invalid("settings", "unknown setting or not a boolean")
            changed_settings = {
                key: value for key, value in incoming.items() if project.settings.get(key) != value
            }
            if changed_settings:
                project.settings = {**project.settings, **changed_settings}
                changed.add("settings")
        if changed:  # the audit records what changed, not what was sent
            metadata: dict[str, Any] = {"fields": sorted(changed)}
            if changed_settings:
                # Booleans, never secret: turning an approval control off must leave its value on record.
                metadata["settings"] = changed_settings
            await _audit(env, session, actor, project, "project.update", metadata)
        repository, open_tasks = await _read_and_commit(session, project)
    # After the commit nothing may fail the request: _stats reports repository problems, never raises.
    return ProjectView(project, repository, permissions, await _stats(env, project.id, open_tasks))


async def set_archived(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, archived: bool
) -> ProjectView:
    source, target = ("active", "archived") if archived else ("archived", "active")
    async with env.sessions() as session:
        project, permissions = await authorize(session, actor, project_id, "project:write", lock=True)
        if project.status != source:
            raise AppError(
                "INVALID_TRANSITION",
                f"Project cannot go from {project.status} to {target}",
                http_status=409,
                details=[{"from": project.status, "to": target}],
            )
        project.status = target
        await _audit(env, session, actor, project, "project.archive" if archived else "project.unarchive", {})
        repository, open_tasks = await _read_and_commit(session, project)
    # After the commit nothing may fail the request: _stats reports repository problems, never raises.
    return ProjectView(project, repository, permissions, await _stats(env, project.id, open_tasks))


async def _read_and_commit(session: AsyncSession, project: Project) -> tuple[Repository | None, int]:
    """Read what the response needs from the database, then commit. Git runs later, with no lock held."""
    repository = await session.scalar(select(Repository).where(Repository.project_id == project.id))
    open_tasks = await open_task_count(session, project.id)
    await _load_updated_at(session, project)
    await session.commit()
    return repository, open_tasks


async def _load_updated_at(session: AsyncSession, project: Project) -> None:
    """The database sets ``updated_at`` on UPDATE, which leaves the attribute unloaded. Load it inside the
    transaction (the value the commit keeps), since the response is built after the session is closed."""
    await session.flush()
    await session.refresh(project, attribute_names=["updated_at"])
