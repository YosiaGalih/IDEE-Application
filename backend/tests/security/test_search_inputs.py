"""Search inputs (WP-2.1d; T2, T3, T4, T5): a query or glob from a request is only ever a pattern, the
search never leaves the repository or reads ``.git``, a pathological regex is bounded, and denied paths stay
hidden."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.audit import AuditWriter
from app.core.redaction import Redactor
from app.services import projects, repo_index
from app.workspace.policy import Access
from tests.integration.test_project_imports import owner_actor
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_workspace_api import blank, repo_of

OPTION_LIKE = ["-v", "--output={pwned}", "-f/etc/passwd", "--open-files-in-pager=touch {pwned}"]


async def search(client: httpx.AsyncClient, project: dict[str, Any], **params: str) -> httpx.Response:
    return await client.get(f"{PROJECTS}/{project['id']}/search", params=params)


@pytest.mark.parametrize("value", OPTION_LIKE)
async def test_option_like_queries_are_patterns(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
    value: str,
) -> None:
    pwned = tmp_path / "pwned"
    project = await blank(owner)
    (repo_of(workspace_root, project) / "a.txt").write_text("plain line\nanother line\n")
    for regex in ("false", "true"):
        response = await search(owner, project, q=value.format(pwned=pwned), regex=regex)
        assert response.status_code in {200, 422}, response.text
        if response.status_code == 200:
            assert response.json()["data"] == []  # "-v" is the text "-v", not "invert match"
    assert not pwned.exists()


async def test_symlinks_out_of_the_repository_are_not_searched(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("OUTSIDE_MARKER_7c1\n")
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "file_link").symlink_to(outside / "secret.txt")
    (repo / "dir_link").symlink_to(outside)
    response = await search(owner, project, q="OUTSIDE_MARKER_7c1")
    assert response.status_code == 200 and response.json()["data"] == []


async def test_git_dir_is_never_searched(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / ".git" / "description").write_text("GITDIR_MARKER_9d2\n")
    response = await search(owner, project, q="GITDIR_MARKER_9d2")
    assert response.status_code == 200 and response.json()["data"] == []


async def test_pathological_regex_is_bounded(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regex runs in git, which is killed at the timeout; the event loop keeps answering meanwhile."""
    monkeypatch.setattr(repo_index, "TEXT_TIMEOUT_S", 1.0)
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    for index in range(40):
        (repo / f"a{index}.txt").write_text("a" * 50_000 + "\n")
    health = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    started = time.monotonic()
    answered: list[float] = []

    async def ping() -> None:
        while time.monotonic() - started < 1.5:
            sent = time.monotonic()
            assert (await health.get("/api/v1/health/live")).status_code == 200
            answered.append(time.monotonic() - sent)
            await asyncio.sleep(0.05)

    try:
        response, _ = await asyncio.gather(search(owner, project, q="(a*)*(a*)*(a*)*b", regex="true"), ping())
    finally:
        await health.aclose()
    assert time.monotonic() - started < 5
    assert response.status_code in {200, 409}, response.text
    if response.status_code == 409:
        assert response.json()["error"]["details"][0]["reason"] == "timeout"
    assert answered and max(answered) < 0.5  # the loop never waited on the regex


async def test_agent_read_drops_denied_paths(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / ".env.local").write_text("TOKEN_MARKER=1\n")
    (repo / "private").mkdir()
    (repo / "private" / "notes.txt").write_text("TOKEN_MARKER in notes\n")
    (repo / "public.txt").write_text("TOKEN_MARKER in public\n")
    (repo / ".agentignore").write_text("private/\n")
    deps = app.state.dependencies
    env = projects.ProjectEnv(
        deps.sessions, AuditWriter(Redactor()), deps.git, workspace_root, redis=deps.redis
    )

    async def paths(access: Access) -> set[str]:
        result = await repo_index.search(
            env, owner_actor(owner_id), uuid.UUID(project["id"]), q="TOKEN_MARKER", mode="text", glob=None,
            regex=False, ignore_case=False, kind=None, limit=200, access=access,
        )  # fmt: skip
        return {hit.path for hit in result.hits}

    assert await paths(Access.AGENT_READ) == {"public.txt"}
    assert await paths(Access.USER_READ) == {".env.local", "private/notes.txt", "public.txt"}


@pytest.mark.parametrize(("field", "value"), [("q", "a\x00b"), ("q", "a\nb"), ("glob", "*\x01")])
async def test_control_characters_are_422(
    owner: httpx.AsyncClient,  # noqa: F811
    field: str,
    value: str,
) -> None:
    project = await blank(owner)
    params = {"q": "x", field: value}
    response = await search(owner, project, **params)
    assert response.status_code == 422, response.text
