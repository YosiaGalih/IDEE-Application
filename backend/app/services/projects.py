"""Projects (FR-012-018, API_CONTRACTS §3): create, list, read, update, archive. Framework-free.

**Scope.** Every operation authorises again here (SECURITY_MODEL §8: the route is only the first door). A
project outside the actor's scope, or one that is ``deleted``, raises ``NOT_FOUND``, never ``FORBIDDEN``, so
its existence never leaks. Inside the scope, a missing permission is ``FORBIDDEN``.

**Workspace.** The repository lives at ``WORKSPACE_ROOT/<project_id>/repo`` (ARCHITECTURE §7). The name the
user typed never reaches a path or a git argument: the folder is the project id, and the name is only file
content written from Python (README) or a template placeholder.

**All or nothing.** The rows are written in one transaction. The folder is created after the rows are
flushed and removed again if anything after that fails, including the commit. No row without a folder, and
no folder without a row.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.errors import AppError
from app.core.logging import get_logger
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
SETTINGS_KEYS = frozenset({"plan_approval_required", "auto_remediation"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_AUTHOR_FORBIDDEN = re.compile(r"[<>\x00-\x1f\x7f]")
_SLUG_SEPARATOR = re.compile(r"[^a-z0-9]+")

StatusFilter = Literal["active", "archived", "all"]


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    email: str
    display_name: str
    grants: tuple[RoleGrant, ...]


@dataclass(frozen=True)
class ProjectEnv:
    sessions: async_sessionmaker[AsyncSession]
    audit: AuditWriter
    git: GitAdapter
    workspace_root: Path


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


@dataclass(frozen=True)
class ProjectView:
    project: Project
    repository: Repository | None
    permissions: frozenset[str]
    stats: ProjectStats | None  # only on a single project: it costs a git call


# --- errors -----------------------------------------------------------------------------------------------


def not_found() -> AppError:
    return AppError("NOT_FOUND", "Not found", http_status=404)


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": ["body", field], "msg": message}],
    )


# --- pure helpers -------------------------------------------------------------------------------------------


def clean_name(name: str) -> str:
    cleaned = name.strip()
    if not 1 <= len(cleaned) <= NAME_MAX or _CONTROL.search(cleaned):
        raise _invalid("name", f"must be 1-{NAME_MAX} characters without control characters")
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


def _author(actor: Actor) -> Author:
    """The user as commit author. git refuses ``<``, ``>`` and newlines in an identity: they are dropped."""
    email = _AUTHOR_FORBIDDEN.sub("", actor.email) or f"user-{actor.user_id}@kodekantor.invalid"
    name = _AUTHOR_FORBIDDEN.sub("", actor.display_name).strip() or email
    return Author(name, email)


def repo_path(root: Path, project_id: uuid.UUID) -> Path:
    return root / str(project_id) / "repo"


# --- authorisation ----------------------------------------------------------------------------------------


async def authorize(
    session: AsyncSession, actor: Actor, project_id: uuid.UUID, permission: str, *, lock: bool = False
) -> tuple[Project, frozenset[str]]:
    statement = select(Project).where(Project.id == project_id)
    if lock:
        statement = statement.with_for_update()
    project = await session.scalar(statement)
    permissions = permissions_for(actor, project) if project is not None else frozenset()
    if project is None or not permissions:
        raise not_found()
    if permission not in permissions:
        raise AppError("FORBIDDEN", "Not allowed", http_status=403)
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
) -> None:
    if isinstance(source, TemplateSource):
        await asyncio.to_thread(materialize, source.template_id, repo, project_name=name, project_slug=slug)
        message = f"Initial commit from template {source.template_id}"
    else:
        await asyncio.to_thread(repo.mkdir)
        # A first commit, so a task worktree has a base to branch from (FR-114).
        await asyncio.to_thread((repo / "README.md").write_text, f"# {name}\n", "utf-8")
        message = "Initial commit"
    await git.init(repo, branch)
    if await git.commit_all(GitTarget(repo), message, author) is None:
        raise GitError("nothing_to_commit")


async def create_project(
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
        raise AppError(
            "POLICY_DENIED",
            "Importing a remote repository is not enabled",
            http_status=403,
            details=[{"reason": "remote_import_disabled"}],
        )
    if isinstance(source, TemplateSource) and source.template_id not in {t.id for t in list_templates()}:
        raise _invalid("source", "unknown template")

    project_id = uuid.uuid4()
    root = env.workspace_root / str(project_id)
    repo = repo_path(env.workspace_root, project_id)
    async with env.sessions() as session:
        # Serialise creations by the same owner so two requests never pick the same slug.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"project-slug:{actor.user_id}"},
        )
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
        provider = "template" if isinstance(source, TemplateSource) else "local"
        session.add(
            Repository(project_id=project_id, provider=provider, branch=default_branch, sync_state="ready")
        )
        await session.flush()
        made_folder = False
        try:
            await asyncio.to_thread(root.mkdir)  # a fresh id: an existing folder is a bug, so no exist_ok
            made_folder = True
            await _populate(
                env.git, repo, source, name=name, slug=slug, branch=default_branch, author=_author(actor)
            )
            project.stack = asdict(await detect_stack(Workspace(repo)))
            metadata: dict[str, Any] = {
                "source": "template" if isinstance(source, TemplateSource) else "blank"
            }
            if isinstance(source, TemplateSource):
                metadata["template_id"] = source.template_id
            await env.audit.write(
                session,
                actor_type="user",
                actor_id=str(actor.user_id),
                action="project.create",
                result="success",
                resource_type="project",
                resource_id=str(project_id),
                project_id=project_id,
                metadata=metadata,
            )
            await session.commit()
        except BaseException:
            await session.rollback()
            if made_folder:
                await asyncio.to_thread(shutil.rmtree, root, True)
            raise
    return await get_project(env, actor, project_id)


# --- read -------------------------------------------------------------------------------------------------


async def _stats(env: ProjectEnv, session: AsyncSession, project: Project) -> ProjectStats:
    open_tasks = await open_task_count(session, project.id)
    repo = repo_path(env.workspace_root, project.id)
    last_commit: str | None = None
    if await asyncio.to_thread(repo.is_dir):
        try:
            last_commit = await env.git.current_commit(GitTarget(repo))
        except GitError as exc:
            # A stat, not the request: the page still loads, and the failure is logged, not hidden.
            logger.warning("project_last_commit_failed", project_id=str(project.id), reason=exc.reason)
    return ProjectStats(files=None, last_commit=last_commit, open_tasks=open_tasks)


async def get_project(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, with_stats: bool = True
) -> ProjectView:
    async with env.sessions() as session:
        project, permissions = await authorize(session, actor, project_id, "project:read")
        repository = await session.scalar(select(Repository).where(Repository.project_id == project.id))
        stats = await _stats(env, session, project) if with_stats else None
        return ProjectView(project, repository, permissions, stats)


async def list_projects(
    env: ProjectEnv, actor: Actor, *, status: StatusFilter = "active", q: str | None = None
) -> list[ProjectView]:
    granted = {grant.project_id for grant in actor.grants if grant.project_id is not None}
    statement = (
        select(Project, Repository)
        .outerjoin(Repository, Repository.project_id == Project.id)
        .where(or_(Project.owner_id == actor.user_id, Project.id.in_(granted)), Project.status != "deleted")
        .order_by(Project.updated_at.desc(), Project.id)
    )
    if status != "all":
        statement = statement.where(Project.status == status)
    if q:
        statement = statement.where(
            or_(Project.name.icontains(q, autoescape=True), Project.slug.icontains(q, autoescape=True))
        )
    async with env.sessions() as session:
        rows = (await session.execute(statement)).all()
    # The WHERE above is the visibility rule of core/rbac.py: the owner, or a role scoped to the project.
    return [
        ProjectView(project, repository, permissions_for(actor, project), None)
        for project, repository in rows
    ]


def templates() -> list[TemplateInfo]:
    return list_templates()


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
    )


async def update_project(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, changes: ProjectChanges
) -> ProjectView:
    unknown = changes.fields - {"name", "description", "settings"}
    if unknown:
        raise _invalid(sorted(unknown)[0], "cannot be changed")
    async with env.sessions() as session:
        project, _ = await authorize(session, actor, project_id, "project:write", lock=True)
        if "name" in changes.fields:
            project.name = clean_name(changes.name or "")  # the slug stays: it is the stable id (FR-012)
        if "description" in changes.fields:
            project.description = clean_description(changes.description)
        if "settings" in changes.fields:
            incoming = changes.settings or {}
            bad = set(incoming) - SETTINGS_KEYS
            if bad or not all(isinstance(value, bool) for value in incoming.values()):
                raise _invalid("settings", "unknown setting or not a boolean")
            project.settings = {**project.settings, **incoming}
        if changes.fields:
            await _audit(env, session, actor, project, "project.update", {"fields": sorted(changes.fields)})
            await session.commit()
    return await get_project(env, actor, project_id)


async def set_archived(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, archived: bool
) -> ProjectView:
    source, target = ("active", "archived") if archived else ("archived", "active")
    async with env.sessions() as session:
        project, _ = await authorize(session, actor, project_id, "project:write", lock=True)
        if project.status != source:
            raise AppError(
                "INVALID_TRANSITION",
                f"Project cannot go from {project.status} to {target}",
                http_status=409,
                details=[{"from": project.status, "to": target}],
            )
        project.status = target
        await _audit(env, session, actor, project, "project.archive" if archived else "project.unarchive", {})
        await session.commit()
    return await get_project(env, actor, project_id)
