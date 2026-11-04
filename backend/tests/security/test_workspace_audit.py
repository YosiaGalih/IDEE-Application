"""T10 (WP-2.1c, AG-13 M1): a change that reached the disk or git is on the audit record, whatever happens to
the request afterwards: a file list that fails, git stopped after it wrote the ref, or a cancelled request."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.gitops.adapter import GitAdapter
from app.gitops.errors import GitError
from app.services import workspace_api
from app.workspace.fs import Workspace
from tests.integration.test_git_api import post_json, raw
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    headers,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_workspace_api import blank, put, repo_of, sha, workspace_events


async def eventually(engine: AsyncEngine, project: dict[str, Any], action: str) -> list[dict[str, Any]]:
    """The events once a write the request no longer waits for has ended (bounded)."""
    for _ in range(100):
        if events := await workspace_events(engine, project, action):
            return events
        await asyncio.sleep(0.05)
    return []


async def test_a_commit_whose_file_list_is_too_large_is_still_audited(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    for index in range(4):
        (repo / f"{index:02d}-{'n' * 60}.txt").write_text("x\n")
    app.state.dependencies.git.max_output = 128  # the names (> 250 bytes) pass it; rev-parse does not
    response = await post_json(owner, project, "git/commit", {"message": "Many"})
    assert response.status_code == 201, response.text
    commit = response.json()["data"]["commit"]
    assert raw(repo, "rev-parse", "HEAD") == commit
    assert await workspace_events(engine, project, "git.commit") == [
        {"commit": commit, "selected": None, "files": None, "files_error": "output_too_large", "paths": None}
    ]


async def test_a_commit_git_reported_as_stopped_is_still_audited(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "a.txt").write_text("a\n")
    run = GitAdapter._run  # pyright: ignore[reportPrivateUsage]

    async def stopped(self: GitAdapter, args: list[str], **kwargs: object) -> object:
        result = await run(self, args, **kwargs)  # type: ignore[arg-type]
        if args[0] == "commit":
            raise GitError("timeout")  # killed after the ref was written
        return result

    monkeypatch.setattr(GitAdapter, "_run", stopped)
    response = await post_json(owner, project, "git/commit", {"message": "Landed"})
    assert response.status_code == 201, response.text
    commit = response.json()["data"]["commit"]
    assert raw(repo, "rev-parse", "HEAD") == commit
    assert [event["commit"] for event in await workspace_events(engine, project, "git.commit")] == [commit]


async def test_a_commit_cancelled_after_it_landed_is_still_audited(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window AG-13 found: the commit is in git, the request is cancelled before its event is written."""
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "a.txt").write_text("a\n")
    landed, release = asyncio.Event(), asyncio.Event()
    files = GitAdapter.commit_files

    async def held(self: GitAdapter, target: Any, commit: str) -> list[str]:
        landed.set()
        await release.wait()
        return await files(self, target, commit)

    monkeypatch.setattr(GitAdapter, "commit_files", held)
    request = asyncio.create_task(post_json(owner, project, "git/commit", {"message": "Cancelled"}))
    await asyncio.wait_for(landed.wait(), 10)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    release.set()
    events = await eventually(engine, project, "git.commit")
    assert [event["commit"] for event in events] == [raw(repo, "rev-parse", "HEAD")]
    assert events[0]["paths"] == ["a.txt"]


async def test_a_save_cancelled_after_it_reached_the_disk_is_still_audited(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    current = sha((repo / "README.md").read_bytes())
    written, release = asyncio.Event(), asyncio.Event()
    write_file = Workspace.write_file

    async def held(self: Workspace, *args: Any, **kwargs: Any) -> Any:
        change = await write_file(self, *args, **kwargs)
        written.set()
        await release.wait()
        return change

    monkeypatch.setattr(Workspace, "write_file", held)
    request = asyncio.create_task(put(owner, project, "README.md", "# saved\n", current))
    await asyncio.wait_for(written.wait(), 10)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    release.set()
    events = await eventually(engine, project, "workspace.file_written")
    assert (repo / "README.md").read_text() == "# saved\n"
    assert [(event["path"], event["old_sha256"]) for event in events] == [("README.md", current)]


async def test_a_write_that_fails_after_the_request_was_cancelled_is_logged(
    owner: httpx.AsyncClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AG-13 I-a: nobody awaits the write any more, so its failure must reach the log, not vanish."""
    project = await blank(owner)
    started, release = asyncio.Event(), asyncio.Event()

    async def fails(self: Workspace, *args: Any, **kwargs: Any) -> Any:
        started.set()
        await release.wait()
        raise RuntimeError("disk went away")

    monkeypatch.setattr(Workspace, "write_file", fails)
    request = asyncio.create_task(put(owner, project, "new.txt", "x\n", None))
    await asyncio.wait_for(started.wait(), 10)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    capsys.readouterr()
    release.set()
    for _ in range(40):
        await asyncio.sleep(0.05)
        if "workspace_write_failed_after_cancel" in (out := capsys.readouterr().out):
            assert "RuntimeError" in out
            return
    pytest.fail("the failure of a write nobody awaits was not logged")


async def test_shutdown_waits_for_writes_still_running(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AG-13 delta2 LOW: the loop cancels every task at shutdown; a write between its effect and its event
    must be waited for first, or the M1 window opens again."""
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    current = sha((repo / "README.md").read_bytes())
    written, release = asyncio.Event(), asyncio.Event()
    write_file = Workspace.write_file

    async def held(self: Workspace, *args: Any, **kwargs: Any) -> Any:
        change = await write_file(self, *args, **kwargs)
        written.set()
        await release.wait()
        return change

    monkeypatch.setattr(Workspace, "write_file", held)
    request = asyncio.create_task(put(owner, project, "README.md", "# saved\n", current))
    await asyncio.wait_for(written.wait(), 10)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    asyncio.get_running_loop().call_later(0.3, release.set)
    await workspace_api.drain_writes(grace_s=10)
    assert len(await workspace_events(engine, project, "workspace.file_written")) == 1


async def test_the_app_drains_writes_at_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,  # noqa: F811
) -> None:
    from app import main
    from app.core.config import load_settings
    from tests.conftest import make_test_env

    steps: list[str] = []

    async def drain(grace_s: float = 30) -> None:
        steps.append("drain_writes")

    monkeypatch.setattr(main, "drain_writes", drain)
    application = main.create_app(load_settings(make_test_env(WORKSPACE_ROOT=str(workspace_root))))
    dependencies = application.state.dependencies
    aclose = dependencies.aclose

    async def closing() -> None:
        steps.append("aclose")
        await aclose()

    monkeypatch.setattr(dependencies, "aclose", closing)
    async with application.router.lifespan_context(application):
        assert steps == []
    # AG-13 I-8: the writes still need the database for their audit event, so they drain before it closes.
    assert steps == ["drain_writes", "aclose"]
