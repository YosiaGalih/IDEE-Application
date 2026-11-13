"""Search inputs (WP-2.1d; T2, T3, T4, T5): a query or glob from a request is only ever a pattern, the
search never leaves the repository or reads ``.git``, a pathological regex is bounded, and denied paths stay
hidden."""

from __future__ import annotations

import asyncio
import gc
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


async def _loop_gaps(until: asyncio.Future[Any]) -> list[float]:
    """Gaps between ticks of a 5 ms ticker while ``until`` runs: a blocked event loop shows as a long gap.

    Objects left by earlier tests are frozen while measuring: a full garbage collection walks the whole heap
    and holds the GIL, and after a full suite (about 780k live objects) one pause took 0.48-0.58 s on its own,
    whatever the code under test does. Frozen, a pause covers only what this test allocates."""
    gc.collect()
    gc.freeze()
    try:
        gaps: list[float] = []
        last = time.monotonic()
        while not until.done():
            await asyncio.sleep(0.005)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
        return gaps
    finally:
        gc.unfreeze()


@pytest.mark.parametrize(
    "pattern", ["(a*)\\1*b", "(a|aa)*\\1b", "((a{1,100}){1,100}){1,100}", "a{2}", "x\\9"]
)
async def test_regex_features_that_explode_are_refused(
    owner: httpx.AsyncClient,  # noqa: F811
    pattern: str,
) -> None:
    """AG-13 M1: backreferences (exponential matching) and bounded repetition (glibc compiles it to GBs)
    never reach git."""
    project = await blank(owner)
    response = await search(owner, project, q=pattern, regex="true")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["reason"] == "regex_unsupported"


async def test_an_escaped_brace_is_a_plain_character(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    (repo_of(workspace_root, project) / "b.txt").write_text("x{y}\n")
    response = await search(owner, project, q="x\\{y", regex="true")
    assert response.status_code == 200 and [hit["path"] for hit in response.json()["data"]] == ["b.txt"]


async def test_a_text_search_past_its_timeout_is_409_and_never_blocks_the_loop(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AG-13 L1: the timeout path, through HTTP, with the event loop measured by a ticker."""
    monkeypatch.setattr(repo_index, "TEXT_TIMEOUT_S", 0.001)
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    for index in range(40):
        (repo / f"a{index}.txt").write_text("a" * 50_000 + "\n")
    request = asyncio.ensure_future(search(owner, project, q="a+b", regex="true"))
    gaps = await _loop_gaps(request)
    response = await request
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"][0]["reason"] == "timeout"
    assert response.headers["Retry-After"] == "5"
    assert max(gaps, default=0) < 0.5


async def test_a_large_search_output_never_blocks_the_loop(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AG-13 M2: parsing and filtering a capped output (many matches) runs off the event loop. The cap is
    raised here so that work on the loop would show far above scheduling noise."""
    monkeypatch.setattr(repo_index, "TEXT_OUTPUT_MAX", 8 << 20)
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    for index in range(300):
        (repo / f"m{index}.txt").write_text("e e e e e e e e\n" * 2000)
    request = asyncio.ensure_future(search(owner, project, q="e", glob="*.nomatch"))
    gaps = await _loop_gaps(request)
    response = await request
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["truncated"] is True  # the output cap was reached
    assert max(gaps, default=0) < 0.5


async def test_text_searches_run_one_at_a_time_per_user(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AG-13 M1: one git grep per user at a time, two per process; the rest is 429, never a queue."""
    from app.gitops.adapter import GitAdapter

    project = await blank(owner)
    started, release = asyncio.Event(), asyncio.Event()
    grep = GitAdapter.grep

    async def held(self: GitAdapter, *args: Any, **kwargs: Any) -> Any:
        started.set()
        await release.wait()
        return await grep(self, *args, **kwargs)

    monkeypatch.setattr(GitAdapter, "grep", held)
    first = asyncio.ensure_future(search(owner, project, q="x"))
    await asyncio.wait_for(started.wait(), 10)
    try:
        second = await asyncio.wait_for(search(owner, project, q="y"), 5)  # never queued behind the first
    finally:
        release.set()
    assert second.status_code == 429 and second.headers["Retry-After"] == "1"
    assert (await first).status_code == 200
    assert (await search(owner, project, q="z")).status_code == 200  # the slot is free again
    monkeypatch.setattr(repo_index, "MAX_TEXT_SEARCHES", 0)
    assert (await search(owner, project, q="z")).status_code == 429


async def test_a_text_search_runs_git_on_one_thread(
    owner: httpx.AsyncClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AG-13 D4: ``--threads=1`` (one CPU per search, M1) is pinned on the argv git actually gets."""
    from app.gitops.adapter import GitAdapter

    project = await blank(owner)
    argvs: list[list[str]] = []
    run = GitAdapter._run  # pyright: ignore[reportPrivateUsage]

    async def recorded(self: GitAdapter, args: list[str], **kwargs: Any) -> Any:
        argvs.append(list(args))
        return await run(self, args, **kwargs)

    monkeypatch.setattr(GitAdapter, "_run", recorded)
    for regex in ("false", "true"):
        assert (await search(owner, project, q="needle", regex=regex)).status_code == 200
    greps = [argv for argv in argvs if argv[:1] == ["grep"]]
    assert len(greps) == 2
    assert all("--threads=1" in argv and argv[-3:] == ["-e", "needle", "--"] for argv in greps)


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
