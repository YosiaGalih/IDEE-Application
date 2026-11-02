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
import os
import re
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select

from app.core.errors import AppError
from app.core.logging import get_logger
from app.gitops.adapter import Branch, GitStatus, GitTarget, LogEntry
from app.gitops.diffparse import Diff, DiffStats
from app.models.projects import Project, Repository
from app.services.projects import Actor, ProjectEnv, authorize, commit_author, repo_path
from app.workspace import fs
from app.workspace.errors import AlreadyExists, PatchRollbackFailed, PathViolation, VersionConflict
from app.workspace.fs import FileChange, TreeResult, Workspace
from app.workspace.policy import Access

logger = get_logger(__name__)

AUDIT_PATHS_MAX = 100  # paths listed in one event; the count is always exact
SANDBOX_DIR = ".kk"  # sandbox artefacts (SECURITY_MODEL §4): never committed, hidden from status and diff

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


def _reason(error: AppError) -> str | None:
    details = error.details or []
    reason = details[0].get("reason") if details else None
    return reason if isinstance(reason, str) else None


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
    if repository is None:  # every project has one (2.1a): an invariant broke, not a state to wait out
        logger.error("workspace_repository_row_missing", project_id=str(project.id))
        raise AppError("INTERNAL_ERROR", "Internal error", http_status=500)
    if repository.sync_state == "error":
        raise AppError(
            "CONFLICT",
            "The repository cannot do this now",
            http_status=409,
            details=[{"reason": "repository_error", "error": repository.error}],
        )
    if repository.sync_state != "ready":  # importing, indexing: it will be ready
        raise _conflict("repository_not_ready")
    repo = repo_path(env.workspace_root, project.id)
    if not await asyncio.to_thread(_is_git_dir, repo / ".git"):
        logger.error("workspace_repository_missing", project_id=str(project.id))
        raise _conflict("repository_missing")
    if write and project.status != "active":
        raise _conflict("project_archived" if project.status == "archived" else "project_not_active")
    return Repo(project, repository, Workspace(repo), GitTarget(repo))


def _is_git_dir(path: Path) -> bool:
    """A real folder, not a symlink to one (the git adapter refuses those too)."""
    return os.path.isdir(path) and not os.path.islink(path)


def _in_sandbox_dir(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and parts[0] == SANDBOX_DIR


# --- reads --------------------------------------------------------------------------------------------------


async def tree(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, path: str = ".", depth: int = 3
) -> TreeResult:
    repo = await open_repo(env, actor, project_id, "workspace:read", write=False)
    return await repo.workspace.tree(
        path or ".", Access.USER_READ, depth=depth, max_entries=fs.MAX_TREE_ENTRIES
    )


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
    if not ref or ref == "main":
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


_RUNNING: set[asyncio.Task[Any]] = set()  # writes that outlive their request: referenced until they end


async def _to_the_end[T](work: Coroutine[Any, Any, T], *, project_id: uuid.UUID, action: str) -> T:
    """Run a write and its audit event to their end even if the request is cancelled (AG-13 M1): the change
    reaches the disk or git before its event, and must never be left without one. A cancel stops the request
    from waiting, not the write; a failure nobody awaits any more is logged. Shutdown waits for these
    (``drain_writes``)."""
    task = asyncio.create_task(work, name=f"{action} {project_id}")
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(_log_unawaited)
        raise


def _log_unawaited(task: asyncio.Task[Any]) -> None:
    write = task.get_name()  # "<action> <project_id>"
    if task.cancelled():  # only at shutdown, past the drain's grace: the loop cancels every task
        logger.error("workspace_write_cancelled_after_cancel", write=write)
        return
    error = task.exception()
    if isinstance(error, AppError):  # an answer (409, 400, ...) nobody reads any more
        logger.warning(
            "workspace_write_failed_after_cancel", write=write, error=error.code, reason=_reason(error)
        )
    elif error is not None:
        logger.error(
            "workspace_write_failed_after_cancel", write=write, error=type(error).__name__, exc_info=error
        )


async def drain_writes(grace_s: float = 30) -> None:
    """At shutdown: let writes that outlived their request reach their audit event before the loop cancels
    every task (AG-13 delta2). What is still running after ``grace_s`` is logged by name."""
    if _RUNNING:
        _, pending = await asyncio.wait(set(_RUNNING), timeout=grace_s)
        if pending:
            logger.error(
                "workspace_writes_unfinished_at_shutdown", writes=sorted(task.get_name() for task in pending)
            )


async def _audit(
    env: ProjectEnv,
    actor: Actor,
    project_id: uuid.UUID,
    action: str,
    metadata: dict[str, object],
    *,
    result: str = "success",
) -> None:
    """One event per write (paths and shas, never content). Callers run inside ``_to_the_end``, so a cancelled
    request still gets its event. A failed event is logged at error level, never turned into a failed request
    the client would retry (and then get ``VERSION_CONFLICT``)."""
    try:
        await env.audit.write_detached(
            env.sessions,
            actor_type="user",
            actor_id=str(actor.user_id),
            action=action,
            result=result,
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

    async def write() -> FileChange:
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

    return await _to_the_end(write(), project_id=project_id, action="workspace.file_written")


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

    async def write() -> str:
        if kind == "dir":
            created = await repo.workspace.make_dir(path, Access.USER_WRITE)
            metadata: dict[str, object] = {"path": created, "type": "dir"}
        else:
            try:
                change = await repo.workspace.write_file(
                    path, content or "", Access.USER_WRITE, expected_sha256=None
                )
            except VersionConflict:
                raise AlreadyExists() from None  # a file is there: the name is taken, nothing to reload
            except PathViolation as exc:
                if _reason(exc) == "not_regular":
                    raise AlreadyExists() from None  # a folder is there
                raise
            created = change.path
            metadata = {"path": created, "type": "file", "sha256": change.sha256}
        await _audit(env, actor, project_id, "workspace.file_created", metadata)
        return created

    return await _to_the_end(write(), project_id=project_id, action="workspace.file_created")


async def delete_file(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, path: str, *, expected_sha256: str
) -> None:
    """Delete one file that is still ``expected_sha256``. Folders are refused by the workspace
    (``PATH_VIOLATION`` ``not_regular``); deleting folders is a v2.1 feature (§13.3 ``recursive``)."""
    repo = await open_repo(env, actor, project_id, "workspace:write", write=True)

    async def write() -> None:
        change = await repo.workspace.delete_file(path, Access.USER_WRITE, expected_sha256=expected_sha256)
        await _audit(
            env,
            actor,
            project_id,
            "workspace.file_deleted",
            {"path": change.path, "old_sha256": change.old_sha256},
        )

    await _to_the_end(write(), project_id=project_id, action="workspace.file_deleted")


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

    async def write() -> list[FileChange]:
        try:
            changes = await repo.workspace.apply_patch(diff, Access.USER_WRITE)
        except PatchRollbackFailed as exc:
            # The tree is partly patched: that change must be on the record, not only in a log line.
            paths = [str(detail.get("path")) for detail in exc.details or []]
            await _audit(
                env,
                actor,
                project_id,
                "workspace.patch_applied",
                {"reason": "rollback_failed", "paths": paths[:AUDIT_PATHS_MAX]},
                result="error",
            )
            raise
        await _audit(
            env,
            actor,
            project_id,
            "workspace.patch_applied",
            {"files": len(changes), "paths": [change.path for change in changes[:AUDIT_PATHS_MAX]]},
        )
        return changes

    return await _to_the_end(write(), project_id=project_id, action="workspace.patch_applied")


# --- git reads ----------------------------------------------------------------------------------------------

DIFF_MAX_BYTES = 1024 * 1024  # API_CONTRACTS §4 ``Diff.truncated`` past this


async def git_status(env: ProjectEnv, actor: Actor, project_id: uuid.UUID) -> GitStatus:
    """``.kk`` is left out (it can never be committed), so ``clean`` means "nothing to commit"."""
    repo = await open_repo(env, actor, project_id, "git:read", write=False)
    status = await env.git.status(repo.target)
    changes = [change for change in status.changes if not _in_sandbox_dir(change.path)]
    return replace(status, changes=changes, clean=not changes)


async def git_diff(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, base: str | None, path: str | None
) -> Diff:
    """``base`` (default HEAD) against the working tree, untracked files included (``head`` is None)."""
    repo = await open_repo(env, actor, project_id, "git:read", write=False)
    paths = [path] if path else None
    diff = await env.git.diff(repo.target, base=base, paths=paths, max_bytes=DIFF_MAX_BYTES)
    files = [file for file in diff.files if not _in_sandbox_dir(file.path)]
    if len(files) == len(diff.files):
        return diff
    stats = DiffStats(
        files=len(files),
        additions=sum(file.additions for file in files),
        deletions=sum(file.deletions for file in files),
    )
    return replace(diff, files=files, stats=stats)


async def git_branches(env: ProjectEnv, actor: Actor, project_id: uuid.UUID) -> list[Branch]:
    repo = await open_repo(env, actor, project_id, "git:read", write=False)
    return await env.git.branches(repo.target)


async def git_log(env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, limit: int) -> list[LogEntry]:
    repo = await open_repo(env, actor, project_id, "git:read", write=False)
    return await env.git.log(repo.target, limit=limit)


# --- git writes ---------------------------------------------------------------------------------------------

RESERVED_BRANCH_PREFIX = "kk/"  # task and user branches belong to the platform (FR-114, §13.3)


def _reserved_branch(name: str) -> bool:
    """``kk`` itself would block every ``kk/task-*`` ref (a file and a folder cannot share a name);
    ``refs/...`` and ``HEAD`` would make refs that read as something else."""
    return name in ("kk", "HEAD") or name.startswith((RESERVED_BRANCH_PREFIX, "refs/"))


async def create_branch(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, name: str, from_ref: str | None
) -> Branch:
    """A new branch at ``from_ref`` (default HEAD). Only the ref is created: no checkout, the working tree
    and its uncommitted edits stay as they are. Deleting or moving branches is not offered (FR-118)."""
    if _reserved_branch(name):
        raise AppError(
            "VALIDATION_ERROR",
            "Input did not pass validation",
            http_status=422,
            details=[{"loc": ["body", "name"], "msg": "kk, kk/..., refs/... and HEAD are reserved"}],
        )
    repo = await open_repo(env, actor, project_id, "git:branch", write=True)
    if any(branch.name == name for branch in await env.git.branches(repo.target)):
        raise _conflict("branch_exists")

    async def write() -> str:
        head = await env.git.create_branch(repo.target, name, from_ref or "HEAD")
        await _audit(env, actor, project_id, "git.branch_created", {"name": name, "head": head})
        return head

    head = await _to_the_end(write(), project_id=project_id, action="git.branch_created")
    return Branch(name=name, head=head, is_task_branch=False)


async def commit(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, message: str, paths: list[str] | None
) -> str:
    """Commit the working tree of ``repo/`` (or only ``paths``) on its current branch, as the user. Holds the
    workspace lock, so a concurrent save is either in this commit or left for the next one, never half in.
    ``.kk`` is never staged (SECURITY_MODEL §4). Nothing staged → 409 ``nothing_to_commit``."""
    if paths is not None and any(_in_sandbox_dir(path) for path in paths):
        raise AppError(
            "VALIDATION_ERROR",
            "Input did not pass validation",
            http_status=422,
            details=[{"loc": ["body", "paths"], "msg": "path_excluded: .kk is never committed"}],
        )
    repo = await open_repo(env, actor, project_id, "workspace:write", write=True)

    async def write() -> str | None:
        # The lock is taken inside: a cancelled request must not release it while git still runs.
        async with repo.workspace.exclusive():
            sha = await env.git.commit(repo.target, message, commit_author(actor), paths=paths)
        if sha is not None:
            await _record_commit(env, actor, project_id, repo.target, sha, paths)
        return sha

    sha = await _to_the_end(write(), project_id=project_id, action="git.commit")
    if sha is None:
        raise _conflict("nothing_to_commit")
    return sha


async def _record_commit(
    env: ProjectEnv,
    actor: Actor,
    project_id: uuid.UUID,
    target: GitTarget,
    sha: str,
    paths: list[str] | None,
) -> None:
    """The commit is in git: its event is written whatever happens to the file list (AG-13 M1)."""
    # selected None: the whole working tree was committed
    metadata: dict[str, object] = {"commit": sha, "selected": len(paths) if paths is not None else None}
    try:
        files = await env.git.commit_files(target, sha)
    except Exception as exc:
        reason = _reason(exc) if isinstance(exc, AppError) else None
        logger.error(
            "git_commit_files_failed",
            project_id=str(project_id),
            commit=sha,
            reason=reason,
            error=type(exc).__name__,
            exc_info=not isinstance(exc, AppError),
        )
        metadata |= {"files": None, "files_error": reason or type(exc).__name__, "paths": None}
    else:
        metadata |= {
            "files": len(files),
            "paths": files[:AUDIT_PATHS_MAX],  # what the commit holds, not what was asked for
        }
    await _audit(env, actor, project_id, "git.commit", metadata)
