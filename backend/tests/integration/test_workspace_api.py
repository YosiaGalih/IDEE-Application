"""Workspace API (WP-2.1c; API_CONTRACTS §3 tree, §4 files, patches) on real PostgreSQL, Redis and git."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.workspace import fs
from tests.integration.test_project_imports import create_upload
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    create,
    headers,
    owner,
    owner_id,
    workspace_root,
)


async def blank(client: httpx.AsyncClient, name: str = "Kode") -> dict[str, Any]:
    response = await create(client, name)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def repo_of(root: Path, project: dict[str, Any]) -> Path:
    return root / project["id"] / "repo"


def url(project: dict[str, Any], tail: str) -> str:
    return f"{PROJECTS}/{project['id']}/{tail}"


def reason(response: httpx.Response) -> str:
    return response.json()["error"]["details"][0]["reason"]


# --- reads (Task 2) -----------------------------------------------------------------------------------------


async def test_tree_lists_the_repository(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    response = await owner.get(url(project, "tree"))
    assert response.status_code == 200, response.text
    body = response.json()
    readme = next(entry for entry in body["data"] if entry["path"] == "README.md")
    assert readme["type"] == "file" and "children" not in readme
    assert body["meta"] == {"truncated": False}


async def test_tree_reports_truncation(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await blank(owner)
    for n in range(6):
        (repo_of(workspace_root, project) / f"f{n}.txt").write_text("x\n")
    monkeypatch.setattr(fs, "MAX_TREE_ENTRIES", 3)
    body = (await owner.get(url(project, "tree"))).json()
    assert body["meta"] == {"truncated": True}


async def test_read_returns_content_sha_and_language(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    raw = (repo_of(workspace_root, project) / "README.md").read_bytes()
    response = await owner.get(url(project, "files/README.md"))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data == {
        "path": "README.md",
        "content": raw.decode(),
        "encoding": "utf-8",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "language": "markdown",
        "too_large": False,
    }


async def test_binary_and_large_files_are_metadata_only(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "logo.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    (repo / "big.txt").write_text("y" * 2000)
    binary = (await owner.get(url(project, "files/logo.bin"))).json()["data"]
    assert (binary["content"], binary["encoding"]) == (None, "binary")
    monkeypatch.setattr(fs, "USER_READ_LIMIT", 1000)
    large = (await owner.get(url(project, "files/big.txt"))).json()["data"]
    assert (large["content"], large["too_large"], large["size"]) == (None, True, 2000)


async def test_ref_task_is_not_found_yet(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    response = await owner.get(url(project, "files/README.md"), params={"ref": f"task:{uuid.uuid4()}"})
    assert response.status_code == 404


async def test_unknown_ref_is_422(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    response = await owner.get(url(project, "files/README.md"), params={"ref": "develop"})
    assert response.status_code == 422


async def test_an_unfinished_upload_has_no_repository_yet(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await create_upload(owner)
    response = await owner.get(url(project, "tree"))
    assert (response.status_code, reason(response)) == (409, "repository_not_ready")


async def test_a_missing_repository_is_reported(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    shutil.rmtree(repo_of(workspace_root, project))
    response = await owner.get(url(project, "files/README.md"))
    assert (response.status_code, reason(response)) == (409, "repository_missing")
