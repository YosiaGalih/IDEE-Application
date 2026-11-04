"""Repository index (WP-2.1d; DATA_MODEL ``repo_index_entries``, FR-023) on real PostgreSQL, Redis and git."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.redaction import Redactor
from app.gitops.adapter import GitAdapter
from app.gitops.errors import GitError
from app.models.audit import AuditEvent
from app.models.projects import Project, RepoIndexEntry, Repository
from app.services import index_lock, projects, repo_index
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    git,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_workspace_api import blank, repo_of, workspace_events


async def _project(engine: AsyncEngine, owner_user: uuid.UUID) -> uuid.UUID:
    project_id = uuid.uuid4()
    async with make_sessionmaker(engine)() as session:
        session.add(
            Project(
                id=project_id,
                owner_id=owner_user,
                name="Idx",
                slug=f"idx-{project_id.hex[:8]}",
                workspace_ref=str(project_id),
            )
        )
        await session.commit()
    return project_id


async def test_index_entries_go_with_their_project(engine: AsyncEngine, owner_id: uuid.UUID) -> None:  # noqa: F811
    project_id = await _project(engine, owner_id)
    async with make_sessionmaker(engine)() as session:
        session.add_all(
            [
                RepoIndexEntry(project_id=project_id, path="a.py", kind="file", commit="c" * 40),
                RepoIndexEntry(
                    project_id=project_id,
                    path="a.py",
                    kind="symbol",
                    symbol="main",
                    symbol_kind="function",
                    line=1,
                    language="python",
                    commit="c" * 40,
                ),
            ]
        )
        await session.commit()
    async with make_sessionmaker(engine)() as session:
        with pytest.raises(IntegrityError):
            session.add(RepoIndexEntry(project_id=project_id, path="b", kind="dir", commit="c" * 40))
            await session.commit()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
        left = await conn.scalar(
            text("SELECT count(*) FROM repo_index_entries WHERE project_id = :id"), {"id": project_id}
        )
    assert left == 0


async def test_symbol_patterns_find_every_language_through_git(tmp_path: Path) -> None:
    """``SYMBOL_PATTERNS`` (ERE, run by git) and ``classify`` (Python) agree on every sample definition."""
    from app.gitops.adapter import Author, GitAdapter, GitTarget
    from app.workspace.index import SYMBOL_PATTERNS, classify
    from tests.unit.test_repo_index import DEFINITIONS

    git = GitAdapter()
    root = tmp_path / "repo"
    root.mkdir()
    await git.init(root, "main")
    by_file: dict[str, list[str]] = {}
    for path, sample, _ in DEFINITIONS:
        by_file.setdefault(path, []).append(sample)
    for path, lines in by_file.items():
        (root / path).write_text("\n".join(["// filler", *lines, ""]))
    (root / "not_js.py").write_text("function f() {}\n")
    target = GitTarget(root)
    sha = await git.commit(target, "samples", Author("Dev", "dev@example.test"))
    assert sha is not None
    result = await git.grep(target, list(SYMBOL_PATTERNS), commit=sha, fixed=False, ignore_case=False,
                            only_matching=True, timeout_s=30, max_output=1 << 20)  # fmt: skip
    found = {(m.path, classify(m.path, m.text)) for m in result.matches}
    found.discard(("not_js.py", None))
    assert found == {(path, expected) for path, _, expected in DEFINITIONS}


# --- Task 4: the index job and the request path -------------------------------------------------------------


class IndexQueue:
    """Stands in for Celery: records index jobs instead of sending them."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]] = []
        self.fail = fail

    async def enqueue_import(self, **_: object) -> None:  # pragma: no cover - not used here
        raise AssertionError("no import expected")

    async def enqueue_index(
        self, *, project_id: uuid.UUID, job_id: uuid.UUID, user_id: uuid.UUID, trigger: str
    ) -> None:
        if self.fail:
            raise ConnectionError("broker unreachable")
        self.calls.append((project_id, job_id, user_id, trigger))


def index_env(api: FastAPI, root: Path, queue: IndexQueue | None = None) -> repo_index.IndexEnv:
    deps = api.state.dependencies
    projects_env = projects.ProjectEnv(
        deps.sessions, AuditWriter(Redactor()), deps.git, root, redis=deps.redis
    )
    return repo_index.IndexEnv(projects=projects_env, redis=deps.redis, jobs=queue or IndexQueue())


async def run_job(env: repo_index.IndexEnv, project_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Request an index (reusing a job already queued) and run it where the worker would."""
    request = await repo_index.request_index(env, project_id, user_id, trigger="user")
    return await repo_index.run_index(
        env, project_id=project_id, job_id=request.job_id, user_id=user_id, trigger="user"
    )


async def entries(
    engine: AsyncEngine, project_id: uuid.UUID
) -> list[tuple[str, str, str | None, int | None]]:
    async with make_sessionmaker(engine)() as session:
        rows = await session.execute(
            select(RepoIndexEntry.path, RepoIndexEntry.kind, RepoIndexEntry.symbol, RepoIndexEntry.line)
            .where(RepoIndexEntry.project_id == project_id)
            .order_by(RepoIndexEntry.kind, RepoIndexEntry.path, RepoIndexEntry.line)
        )
        return [tuple(row) for row in rows]  # type: ignore[misc]


async def repository_row(engine: AsyncEngine, project_id: uuid.UUID) -> Repository:
    async with make_sessionmaker(engine)() as session:
        repository = await session.scalar(select(Repository).where(Repository.project_id == project_id))
        assert repository is not None
        return repository


async def project_with_code(client: httpx.AsyncClient, root: Path) -> tuple[uuid.UUID, Path]:
    project = await blank(client)
    repo = repo_of(root, project)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "app.py").write_text("import os\n\ndef main():\n    pass\n")
    git(repo, "add", "src/app.py")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "code")
    return uuid.UUID(project["id"]), repo


async def test_index_lists_files_and_symbols_of_head(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id, repo = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    assert await run_job(env, project_id, owner_id) == "indexed"
    head = git(repo, "rev-parse", "HEAD")
    tracked = sorted(git(repo, "ls-files").split("\n"))
    found = await entries(engine, project_id)
    assert [path for path, kind, _, _ in found if kind == "file"] == tracked
    assert [(path, symbol, line) for path, kind, symbol, line in found if kind == "symbol"] == [
        ("src/app.py", "main", 3)
    ]
    repository = await repository_row(engine, project_id)
    assert repository.index_commit == head and repository.last_indexed_at is not None
    [event] = await workspace_events(engine, {"id": str(project_id)}, "project.index")
    assert event == {
        "commit": head, "files": len(tracked), "symbols": 1, "skipped": 0, "truncated": False,
        "unchanged": False, "trigger": "user",
    }  # fmt: skip


async def test_index_of_an_unchanged_head_rewrites_nothing(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    assert await run_job(env, project_id, owner_id) == "indexed"
    async with make_sessionmaker(engine)() as session:
        ids = sorted(
            (
                await session.scalars(
                    select(RepoIndexEntry.id).where(RepoIndexEntry.project_id == project_id)
                )
            ).all()
        )
    first = (await repository_row(engine, project_id)).last_indexed_at
    assert await run_job(env, project_id, owner_id) == "unchanged"
    async with make_sessionmaker(engine)() as session:
        again = sorted(
            (
                await session.scalars(
                    select(RepoIndexEntry.id).where(RepoIndexEntry.project_id == project_id)
                )
            ).all()
        )
    assert again == ids
    second = (await repository_row(engine, project_id)).last_indexed_at
    assert first is not None and second is not None and second > first
    events = await workspace_events(engine, {"id": str(project_id)}, "project.index")
    assert [event["unchanged"] for event in events] == [False, True]


async def test_a_new_commit_replaces_the_index(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id, repo = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    await run_job(env, project_id, owner_id)
    git(repo, "rm", "-q", "src/app.py")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "gone")
    assert await run_job(env, project_id, owner_id) == "indexed"
    found = await entries(engine, project_id)
    assert all(path != "src/app.py" for path, _, _, _ in found)
    assert (await repository_row(engine, project_id)).index_commit == git(repo, "rev-parse", "HEAD")


async def test_uncommitted_edits_are_not_indexed(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id, repo = await project_with_code(owner, workspace_root)
    (repo / "src" / "app.py").write_text("def edited():\n    pass\n")
    (repo / "draft.py").write_text("def draft():\n    pass\n")
    await run_job(index_env(app, workspace_root), project_id, owner_id)
    symbols = {symbol for _, kind, symbol, _ in await entries(engine, project_id) if kind == "symbol"}
    assert symbols == {"main"}


async def test_index_limits_mark_truncated(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    monkeypatch.setattr(repo_index, "MAX_FILES", 1)
    await run_job(index_env(app, workspace_root), project_id, owner_id)
    assert len([1 for _, kind, _, _ in await entries(engine, project_id) if kind == "file"]) == 1
    [event] = await workspace_events(engine, {"id": str(project_id)}, "project.index")
    assert event["truncated"] is True


async def test_a_git_failure_keeps_the_old_index(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, repo = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    await run_job(env, project_id, owner_id)
    before = await entries(engine, project_id)
    (repo / "b.py").write_text("def b():\n    pass\n")
    git(repo, "add", "b.py")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "b")

    async def stopped(*_: object, **__: object) -> object:
        raise GitError("timeout")

    monkeypatch.setattr(GitAdapter, "grep", stopped)
    assert await run_job(env, project_id, owner_id) == "error"
    assert await entries(engine, project_id) == before
    events = await audit_rows(engine, project_id)
    assert events[-1] == ("error", {"reason": "timeout", "trigger": "user"})
    assert await index_lock.holder(app.state.dependencies.redis, project_id) is None


async def test_index_never_writes_repository_state(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    before = await repository_row(engine, project_id)
    await run_job(index_env(app, workspace_root), project_id, owner_id)
    after = await repository_row(engine, project_id)
    assert (after.sync_state, after.error, after.provider, after.branch) == (
        before.sync_state, before.error, before.provider, before.branch,
    )  # fmt: skip


async def test_a_job_without_the_lock_does_nothing(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    redis = app.state.dependencies.redis
    await redis.delete(index_lock.lock_key(project_id))
    await index_lock.acquire(redis, project_id, uuid.uuid4())  # a newer request holds it
    outcome = await repo_index.run_index(
        env, project_id=project_id, job_id=uuid.uuid4(), user_id=owner_id, trigger="user"
    )
    assert outcome == "skipped"
    assert await entries(engine, project_id) == []
    assert await workspace_events(engine, {"id": str(project_id)}, "project.index") == []


async def test_request_reuses_a_running_job(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    await app.state.dependencies.redis.delete(index_lock.lock_key(project_id))
    queue = IndexQueue()
    env = index_env(app, workspace_root, queue)
    first = await repo_index.request_index(env, project_id, owner_id, trigger="user")
    second = await repo_index.request_index(env, project_id, owner_id, trigger="user")
    assert (first.reused, second.reused, second.job_id) == (False, True, first.job_id)
    assert [call[1] for call in queue.calls] == [first.job_id]


async def test_a_failed_enqueue_frees_the_lock_and_is_503(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    redis = app.state.dependencies.redis
    await redis.delete(index_lock.lock_key(project_id))
    env = index_env(app, workspace_root, IndexQueue(fail=True))
    with pytest.raises(AppError) as raised:
        await repo_index.request_index(env, project_id, owner_id, trigger="user")
    assert raised.value.http_status == 503
    assert await index_lock.holder(redis, project_id) is None


async def test_index_soon_never_raises(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    await app.state.dependencies.redis.delete(index_lock.lock_key(project_id))
    env = index_env(app, workspace_root, IndexQueue(fail=True))
    capsys.readouterr()
    await repo_index.index_soon(env, project_id, owner_id, trigger="create")
    assert "project_index_not_requested" in capsys.readouterr().out


async def test_a_project_deleted_during_the_job_is_skipped(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    list_files = GitAdapter.list_files

    async def then_delete(self: GitAdapter, *args: Any, **kwargs: Any) -> Any:
        listing = await list_files(self, *args, **kwargs)
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
        return listing

    monkeypatch.setattr(GitAdapter, "list_files", then_delete)
    assert await run_job(env, project_id, owner_id) == "skipped"
    assert await index_lock.holder(app.state.dependencies.redis, project_id) is None


async def test_non_utf8_paths_are_skipped(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id, repo = await project_with_code(owner, workspace_root)
    (repo / os.fsdecode(b"\xff.py")).write_text("def hidden():\n    pass\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "latin-1")
    await run_job(index_env(app, workspace_root), project_id, owner_id)
    assert "hidden" not in {symbol for _, _, symbol, _ in await entries(engine, project_id)}
    [event] = await workspace_events(engine, {"id": str(project_id)}, "project.index")
    assert event["skipped"] == 1


async def test_stats_files_counts_indexed_files(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project_id, repo = await project_with_code(owner, workspace_root)
    url = f"{PROJECTS}/{project_id}"
    assert (await owner.get(url)).json()["data"]["stats"]["files"] is None
    await run_job(index_env(app, workspace_root), project_id, owner_id)
    assert (await owner.get(url)).json()["data"]["stats"]["files"] == len(git(repo, "ls-files").split("\n"))


async def audit_rows(engine: AsyncEngine, project_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    async with make_sessionmaker(engine)() as session:
        rows = await session.execute(
            select(AuditEvent.result, AuditEvent.metadata_)
            .where(AuditEvent.action == "project.index", AuditEvent.resource_id == str(project_id))
            .order_by(AuditEvent.id)
        )
        return [(result, metadata) for result, metadata in rows]


# --- review round (silent-failure H1/H2/M1/M2, backend S3/S4/N6) --------------------------------------------


async def test_an_unreadable_head_keeps_the_index_and_is_an_error(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id, repo = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    await run_job(env, project_id, owner_id)
    before = await entries(engine, project_id)
    indexed = (await repository_row(engine, project_id)).last_indexed_at
    branch = git(repo, "symbolic-ref", "HEAD")
    (repo / ".git" / branch).write_text("1" * 40 + "\n")  # the branch names a commit that is not there
    assert await run_job(env, project_id, owner_id) == "error"
    assert await entries(engine, project_id) == before
    assert (await repository_row(engine, project_id)).last_indexed_at == indexed
    assert (await audit_rows(engine, project_id))[-1] == (
        "error",
        {"reason": "head_unreadable", "trigger": "user"},
    )


async def test_a_truncated_index_is_flagged_and_rebuilt(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    monkeypatch.setattr(repo_index, "MAX_FILES", 1)
    assert await run_job(env, project_id, owner_id) == "indexed"
    assert (await repository_row(engine, project_id)).index_truncated is True
    assert await run_job(env, project_id, owner_id) == "indexed"  # never "unchanged" over a partial index
    monkeypatch.setattr(repo_index, "MAX_FILES", 50_000)
    assert await run_job(env, project_id, owner_id) == "indexed"
    assert (await repository_row(engine, project_id)).index_truncated is False
    assert await run_job(env, project_id, owner_id) == "unchanged"


async def test_a_request_while_the_job_runs_gets_another_round(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend S4: a job that already read HEAD must not answer for a commit made after it read."""
    project_id, repo = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    request = await repo_index.request_index(env, project_id, owner_id, trigger="user")
    list_files = GitAdapter.list_files
    rounds: list[int] = []

    async def commit_meanwhile(self: GitAdapter, *args: Any, **kwargs: Any) -> Any:
        listing = await list_files(self, *args, **kwargs)
        if not rounds:  # the user commits and asks again while the first round runs
            (repo / "later.py").write_text("def later():\n    pass\n")
            git(repo, "add", "later.py")
            git(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "later")
            again = await repo_index.request_index(env, project_id, owner_id, trigger="user")
            assert (again.job_id, again.reused) == (request.job_id, True)
        rounds.append(1)
        return listing

    monkeypatch.setattr(GitAdapter, "list_files", commit_meanwhile)
    outcome = await repo_index.run_index(
        env, project_id=project_id, job_id=request.job_id, user_id=owner_id, trigger="user"
    )
    assert (outcome, len(rounds)) == ("indexed", 2)
    assert (await repository_row(engine, project_id)).index_commit == git(repo, "rev-parse", "HEAD")
    assert "later" in {symbol for _, _, symbol, _ in await entries(engine, project_id)}
    assert await index_lock.holder(app.state.dependencies.redis, project_id) is None


async def test_a_job_past_its_deadline_is_audited_and_frees_the_lock(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _ = await project_with_code(owner, workspace_root)
    monkeypatch.setattr(repo_index, "INDEX_DEADLINE_S", 0.2)

    async def stuck(*_: object, **__: object) -> object:
        await asyncio.sleep(30)
        raise AssertionError("not reached")

    monkeypatch.setattr(GitAdapter, "grep", stuck)
    assert await run_job(index_env(app, workspace_root), project_id, owner_id) == "error"
    assert (await audit_rows(engine, project_id))[-1] == (
        "error",
        {"reason": "time_limit", "trigger": "user"},
    )
    assert await index_lock.holder(app.state.dependencies.redis, project_id) is None


async def test_a_lock_set_whose_answer_was_lost_is_freed(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silent-failure M1: the SET reached Redis, its reply did not; no ghost job may hold the lock."""
    from redis.exceptions import TimeoutError as RedisTimeout

    project_id, _ = await project_with_code(owner, workspace_root)
    redis = app.state.dependencies.redis
    await redis.delete(index_lock.lock_key(project_id))
    acquire = index_lock.acquire

    async def lost_reply(*args: Any) -> bool:
        await acquire(*args)
        raise RedisTimeout("reply lost")

    monkeypatch.setattr(index_lock, "acquire", lost_reply)
    with pytest.raises(AppError) as raised:
        await repo_index.request_index(index_env(app, workspace_root), project_id, owner_id, trigger="user")
    assert raised.value.http_status == 503
    assert await index_lock.holder(redis, project_id) is None


async def test_a_failure_inside_the_swap_keeps_the_old_index(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend N6: the DELETE and INSERTs roll back with the audit write that failed after them."""
    project_id, repo = await project_with_code(owner, workspace_root)
    env = index_env(app, workspace_root)
    await run_job(env, project_id, owner_id)
    before = await entries(engine, project_id)
    (repo / "b.py").write_text("def b():\n    pass\n")
    git(repo, "add", "b.py")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "b")
    write = AuditWriter.write

    async def failing(self: AuditWriter, session: Any, **fields: Any) -> int:
        if fields.get("result") == "success":
            raise RuntimeError("audit store down")
        return await write(self, session, **fields)

    monkeypatch.setattr(AuditWriter, "write", failing)
    assert await run_job(env, project_id, owner_id) == "error"
    assert await entries(engine, project_id) == before
