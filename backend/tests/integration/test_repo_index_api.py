"""Index and search routes (WP-2.1d; API_CONTRACTS §3 ``POST /index``, ``GET /search``) on real services."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.services import index_lock, repo_index
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    git,
    headers,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_repo_index import IndexQueue, index_env, project_with_code, run_job
from tests.integration.test_workspace_api import blank, put, reason, repo_of, sha, url


def search_url(project_id: uuid.UUID | str, **params: Any) -> str:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"{PROJECTS}/{project_id}/search?{query}"


async def indexed(
    api: FastAPI, client: httpx.AsyncClient, root: Path, user: uuid.UUID
) -> tuple[uuid.UUID, Path]:
    project_id, repo = await project_with_code(client, root)
    assert await run_job(index_env(api, root), project_id, user) == "indexed"
    return project_id, repo


async def test_text_search_sees_edits_not_yet_committed(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    current = sha((repo / "README.md").read_bytes())
    assert (
        await put(owner, project, "README.md", "# Unsaved to git\nfind me here\n", current)
    ).status_code == 200
    response = await owner.get(search_url(project["id"], q="find+me"))
    assert response.status_code == 200, response.text
    assert response.json()["data"] == [{"path": "README.md", "line": 2, "preview": "find me here"}]
    assert response.json()["meta"]["truncated"] is False


async def test_text_search_glob_limit_and_truncated(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "many.txt").write_text("needle\n" * 300)
    (repo / "other.md").write_text("needle\n")
    response = await owner.get(search_url(project["id"], q="needle", glob="*.txt", limit=50))
    body = response.json()
    assert len(body["data"]) == 50 and body["meta"]["truncated"] is True
    assert {hit["path"] for hit in body["data"]} == {"many.txt"}
    only_md = (await owner.get(search_url(project["id"], q="needle", glob="*.md"))).json()
    assert ([hit["path"] for hit in only_md["data"]], only_md["meta"]["truncated"]) == (["other.md"], False)


async def test_text_search_regex_and_invalid_regex(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    (repo_of(workspace_root, project) / "g.txt").write_text("greet\n")
    found = await owner.get(search_url(project["id"], q="gr.et", regex="true"))
    assert [hit["path"] for hit in found.json()["data"]] == ["g.txt"]
    literal = await owner.get(search_url(project["id"], q="gr.et"))
    assert literal.json()["data"] == []
    invalid = await owner.get(search_url(project["id"], q="(", regex="true"))
    assert invalid.status_code == 422 and reason(invalid) == "invalid_regex"


async def test_text_search_ignore_case_by_default(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    (repo_of(workspace_root, project) / "c.txt").write_text("MiXeD\n")
    assert len((await owner.get(search_url(project["id"], q="mixed"))).json()["data"]) == 1
    strict = await owner.get(search_url(project["id"], q="mixed", ignore_case="false"))
    assert strict.json()["data"] == []


async def test_symbol_search_orders_exact_prefix_contains(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project_id, repo = await project_with_code(owner, workspace_root)
    (repo / "r.py").write_text("def prerun():\n    pass\n\ndef runner():\n    pass\n\ndef run():\n    pass\n")
    git(repo, "add", "r.py")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "r")
    await run_job(index_env(app, workspace_root), project_id, owner_id)
    body = (await owner.get(search_url(project_id, q="run", mode="symbol"))).json()
    assert [hit["symbol"] for hit in body["data"]] == ["run", "runner", "prerun"]
    assert body["data"][0] == {
        "path": "r.py",
        "line": 7,
        "preview": "function run",
        "symbol": "run",
        "kind": "function",
    }
    assert body["meta"]["index_commit"] == git(repo, "rev-parse", "HEAD")
    assert body["meta"]["indexed_at"] is not None


async def test_symbol_search_filters_kind_and_glob(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project_id, repo = await project_with_code(owner, workspace_root)
    (repo / "lib").mkdir()
    (repo / "lib" / "m.py").write_text("class Main:\n    pass\n")
    git(repo, "add", "lib/m.py")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "m")
    await run_job(index_env(app, workspace_root), project_id, owner_id)
    classes = (await owner.get(search_url(project_id, q="main", mode="symbol", kind="class"))).json()["data"]
    assert [(hit["path"], hit["kind"]) for hit in classes] == [("lib/m.py", "class")]
    in_src = (await owner.get(search_url(project_id, q="main", mode="symbol", glob="src/*"))).json()["data"]
    assert [hit["path"] for hit in in_src] == ["src/app.py"]


async def test_symbol_search_escapes_like_wildcards(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project_id, _ = await indexed(app, owner, workspace_root, owner_id)
    for wildcard in ("%25", "_", "%5C"):
        response = await owner.get(search_url(project_id, q=wildcard, mode="symbol"))
        assert response.status_code == 200 and response.json()["data"] == [], wildcard


async def test_symbol_search_before_any_index(
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    project = await blank(owner)
    body = (await owner.get(search_url(project["id"], q="main", mode="symbol"))).json()
    assert body["data"] == [] and body["meta"]["index_commit"] is None


async def test_long_lines_get_a_short_preview(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    (repo_of(workspace_root, project) / "min.js").write_text("x" * 2_000_000 + "NEEDLE" + "y" * 1000 + "\n")
    [hit] = (await owner.get(search_url(project["id"], q="NEEDLE"))).json()["data"]
    assert len(hit["preview"]) <= 200 and "NEEDLE" in hit["preview"]


async def test_post_index_is_202_and_reuses_a_running_job(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    project = await blank(owner)
    redis = app.state.dependencies.redis
    await redis.delete(index_lock.lock_key(uuid.UUID(project["id"])))
    app.state.dependencies.jobs = queue = IndexQueue()
    first = await owner.post(url(project, "index"), headers=headers(owner))
    second = await owner.post(url(project, "index"), headers=headers(owner))
    assert (first.status_code, second.status_code) == (202, 202), first.text
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]
    assert (first.json()["meta"]["reused"], second.json()["meta"]["reused"]) == (False, True)
    assert [call[3] for call in queue.calls] == ["user"]


async def test_index_needs_a_ready_active_repository(
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    upload = (
        await owner.post(PROJECTS, json={"name": "Up", "source": {"type": "upload"}}, headers=headers(owner))
    ).json()["data"]
    not_ready = await owner.post(url(upload, "index"), headers=headers(owner))
    assert (not_ready.status_code, reason(not_ready)) == (409, "repository_not_ready")
    project = await blank(owner, "Arsip")
    assert (await owner.post(url(project, "archive"), headers=headers(owner))).status_code == 200
    archived = await owner.post(url(project, "index"), headers=headers(owner))
    assert (archived.status_code, reason(archived)) == (409, "project_archived")


async def test_index_requests_are_rate_limited(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    project = await blank(owner)
    app.state.dependencies.jobs = IndexQueue()
    codes = [(await owner.post(url(project, "index"), headers=headers(owner))).status_code for _ in range(11)]
    assert codes == [202] * 10 + [429]


async def test_text_searches_are_rate_limited(
    owner: httpx.AsyncClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo_index, "TEXT_SEARCHES_PER_MINUTE", 3)
    project = await blank(owner)
    codes = [(await owner.get(search_url(project["id"], q="x"))).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


async def test_creating_a_project_requests_an_index(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
) -> None:
    app.state.dependencies.jobs = queue = IndexQueue()
    project = await blank(owner)
    assert [(str(call[0]), call[2], call[3]) for call in queue.calls] == [(project["id"], owner_id, "create")]
    held = await index_lock.holder(app.state.dependencies.redis, uuid.UUID(project["id"]))
    assert held == str(queue.calls[0][1])


async def test_a_failed_index_request_does_not_fail_the_create(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    app.state.dependencies.jobs = IndexQueue(fail=True)
    response = await owner.post(
        PROJECTS, json={"name": "Still", "source": {"type": "blank"}}, headers=headers(owner)
    )
    assert response.status_code == 201, response.text


async def test_an_import_requests_an_index(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker import platform_jobs
    from tests.conftest import make_test_env

    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    asked: list[tuple[uuid.UUID, uuid.UUID, str]] = []

    async def ready(*_: object, **__: object) -> str:
        return "ready"

    async def index_soon(env: object, project_id: uuid.UUID, user_id: uuid.UUID, *, trigger: str) -> None:
        asked.append((project_id, user_id, trigger))

    monkeypatch.setattr(platform_jobs, "run_import", ready)
    monkeypatch.setattr(platform_jobs, "index_soon", index_soon)
    project_id, user_id = uuid.uuid4(), uuid.uuid4()
    outcome = await platform_jobs.run_import_job(
        project_id=project_id, job_id=uuid.uuid4(), user_id=user_id, filename="a.zip"
    )
    assert outcome == "ready"
    assert asked == [(project_id, user_id, "import")]
