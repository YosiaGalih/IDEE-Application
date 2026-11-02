"""The project repository over HTTP (API_CONTRACTS §3 tree, §4 files/patches/git; WP-2.1c). Framework-free.

Every call goes through ``open_repo``: the same ``authorize`` the route's door used (404 outside the scope,
403 without the permission), then the repository preconditions:

- the repository is ``ready`` and ``repo/.git`` is a folder, else 409 ``repository_not_ready`` or
  ``repository_missing``. Nothing here writes ``repositories.sync_state`` (AG-13 entry condition, WP-2.1b:
  only an import's ``_complete`` moves an upload project to ``ready``);
- writes need an ``active`` project, else 409 ``project_archived``.

Paths and git arguments are checked where they are used: ``Workspace`` (T2: fd-relative, no ``..``, no
symlink out of the root, ``.git`` denied) and ``GitAdapter`` (T3: no shell, literal pathspecs, no options).
User edits land in the working tree of ``repo/`` on its current branch (ARCHITECTURE §11.3); agents never
use this module (they write task worktrees through the ToolGateway).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import select

from app.core.errors import AppError
from app.core.logging import get_logger
from app.gitops.adapter import Branch, GitStatus, GitTarget, LogEntry
from app.gitops.diffparse import Diff
from app.models.projects import Project, Repository
from app.services.projects import Actor, ProjectEnv, authorize, repo_path
from app.workspace.fs import FileChange, TreeResult, Workspace
from app.workspace.policy import Access

logger = get_logger(__name__)

AUDIT_PATHS_MAX = 100  # paths listed in one patch event; the count is always exact

_TASK_REF = re.compile(r"task:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# Monaco language ids by extension (FR-027); unknown → None (plain text).
_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".sh": "shell",
    ".sql": "sql",
    ".xml": "xml",
    ".txt": "plaintext",
}
_LANGUAGE_NAMES = {"Dockerfile": "dockerfile", "Makefile": "makefile"}


def language_for(path: str) -> str | None:
    """The editor language for ``path`` (by name, then extension), ``None`` when unknown."""
    name = PurePosixPath(path).name
    if name in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[name]
    return _LANGUAGES.get(PurePosixPath(name).suffix.lower())


def _conflict(reason: str) -> AppError:
    return AppError(
        "CONFLICT", "The repository cannot do this now", http_status=409, details=[{"reason": reason}]
    )


@dataclass(frozen=True)
class Repo:
    project: Project
    repository: Repository
    workspace: Workspace
    target: GitTarget


async def open_repo(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, permission: str, *, write: bool
) -> Repo:
    """Authorise ``permission`` and check the preconditions; the repository of a project that can serve it."""
    async with env.sessions() as session:
        project, _ = await authorize(session, actor, project_id, permission)
        repository = await session.scalar(select(Repository).where(Repository.project_id == project.id))
    if repository is None or repository.sync_state != "ready":
        raise _conflict("repository_not_ready")
    repo = repo_path(env.workspace_root, project.id)
    if not await asyncio.to_thread((repo / ".git").is_dir):
        raise _conflict("repository_missing")
    if write and project.status != "active":
        raise _conflict("project_archived")
    return Repo(project, repository, Workspace(repo), GitTarget(repo))


# --- reads --------------------------------------------------------------------------------------------------


async def tree(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, path: str = ".", depth: int = 3
) -> TreeResult:
    repo = await open_repo(env, actor, project_id, "workspace:read", write=False)
    return await repo.workspace.tree(path, Access.USER_READ, depth=depth)


@dataclass(frozen=True)
class FileView:
    path: str
    content: str | None
    encoding: str
    size: int
    sha256: str | None
    language: str | None
    too_large: bool


def _check_ref(ref: str | None) -> None:
    """``main`` (or nothing) is the working tree of ``repo/``. Task worktrees arrive with tasks (WP-3.1)."""
    if ref is None or ref == "main":
        return
    if _TASK_REF.fullmatch(ref):
        raise AppError("NOT_FOUND", "Not found", http_status=404)
    raise AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": ["query", "ref"], "msg": "expected main or task:<id>"}],
    )


async def read_file(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, path: str, *, ref: str | None = None
) -> FileView:
    _check_ref(ref)
    repo = await open_repo(env, actor, project_id, "workspace:read", write=False)
    content = await repo.workspace.read_file(path, Access.USER_READ)
    return FileView(
        path=content.path,
        content=content.content,
        encoding=content.encoding,
        size=content.size,
        sha256=content.sha256,
        language=language_for(content.path),
        too_large=content.too_large,
    )


# --- writes -------------------------------------------------------------------------------------------------


async def _audit(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, action: str, metadata: dict[str, object]
) -> None:
    """One event per successful write (paths and shas, never content). The change already happened on disk:
    a failed event is logged at error level, never turned into a failed request the client would retry."""
    try:
        await env.audit.write_detached(
            env.sessions,
            actor_type="user",
            actor_id=str(actor.user_id),
            action=action,
            result="success",
            resource_type="project",
            resource_id=str(project_id),
            project_id=project_id,
            metadata=metadata,
            ip=actor.ip,
        )
    except Exception:
        logger.exception("workspace_audit_failed", project_id=str(project_id), action=action)


async def write_file(
    env: ProjectEnv,
    actor: Actor,
    project_id: uuid.UUID,
    path: str,
    content: str,
    *,
    expected_sha256: str | None,
) -> FileChange:
    """Save ``content`` if the file is still ``expected_sha256`` (``None``: it must not exist yet), else 409
    ``VERSION_CONFLICT`` (FR-029): a concurrent edit is never overwritten."""
    repo = await open_repo(env, actor, project_id, "workspace:write", write=True)
    change = await repo.workspace.write_file(
        path, content, Access.USER_WRITE, expected_sha256=expected_sha256
    )
    await _audit(
        env,
        actor,
        project_id,
        "workspace.file_written",
        {"path": change.path, "sha256": change.sha256, "old_sha256": change.old_sha256},
    )
    return change


async def create(
    env: ProjectEnv,
    actor: Actor,
    project_id: uuid.UUID,
    path: str,
    *,
    kind: str,
    content: str | None,
) -> str:
    """A new file (``content``, empty when ``None``) or folder; 409 ``CONFLICT`` when the path exists."""
    repo = await open_repo(env, actor, project_id, "workspace:write", write=True)
    if kind == "dir":
        created = await repo.workspace.make_dir(path, Access.USER_WRITE)
        metadata: dict[str, object] = {"path": created, "type": "dir"}
    else:
        change = await repo.workspace.write_file(path, content or "", Access.USER_WRITE, expected_sha256=None)
        created = change.path
        metadata = {"path": created, "type": "file", "sha256": change.sha256}
    await _audit(env, actor, project_id, "workspace.file_created", metadata)
    return created


async def delete_file(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, path: str, *, expected_sha256: str
) -> None:
    """Delete one file that is still ``expected_sha256``. Folders are refused by the workspace
    (``PATH_VIOLATION`` ``not_regular``); deleting folders is a v2.1 feature (§13.3 ``recursive``)."""
    repo = await open_repo(env, actor, project_id, "workspace:write", write=True)
    change = await repo.workspace.delete_file(path, Access.USER_WRITE, expected_sha256=expected_sha256)
    await _audit(
        env,
        actor,
        project_id,
        "workspace.file_deleted",
        {"path": change.path, "old_sha256": change.old_sha256},
    )


async def apply_patch(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, diff: str, *, target: str
) -> list[FileChange]:
    """Apply a unified diff all-or-nothing (FR-031): one hunk that does not apply → 409 ``CONFLICT`` with the
    rejected hunks in ``details`` and no file changed."""
    if target != "main":
        raise AppError(
            "VALIDATION_ERROR",
            "Input did not pass validation",
            http_status=422,
            details=[{"loc": ["body", "target"], "msg": "only main until task worktrees exist"}],
        )
    repo = await open_repo(env, actor, project_id, "workspace:write", write=True)
    changes = await repo.workspace.apply_patch(diff, Access.USER_WRITE)
    await _audit(
        env,
        actor,
        project_id,
        "workspace.patch_applied",
        {"files": len(changes), "paths": [change.path for change in changes[:AUDIT_PATHS_MAX]]},
    )
    return changes


# --- git reads ----------------------------------------------------------------------------------------------

DIFF_MAX_BYTES = 1024 * 1024  # API_CONTRACTS §4 ``Diff.truncated`` past this


async def git_status(env: ProjectEnv, actor: Actor, project_id: uuid.UUID) -> GitStatus:
    repo = await open_repo(env, actor, project_id, "git:read", write=False)
    return await env.git.status(repo.target)


async def git_diff(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, base: str | None, path: str | None
) -> Diff:
    """``base`` (default HEAD) against the working tree, untracked files included (``head`` is None)."""
    repo = await open_repo(env, actor, project_id, "git:read", write=False)
    paths = [path] if path else None
    return await env.git.diff(repo.target, base=base, paths=paths, max_bytes=DIFF_MAX_BYTES)


async def git_branches(env: ProjectEnv, actor: Actor, project_id: uuid.UUID) -> list[Branch]:
    repo = await open_repo(env, actor, project_id, "git:read", write=False)
    return await env.git.branches(repo.target)


async def git_log(env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, limit: int) -> list[LogEntry]:
    repo = await open_repo(env, actor, project_id, "git:read", write=False)
    return await env.git.log(repo.target, limit=limit)
