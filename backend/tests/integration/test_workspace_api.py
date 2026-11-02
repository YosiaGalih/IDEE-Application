"""Workspace API (WP-2.1c; API_CONTRACTS §3 tree, §4 files, patches) on real PostgreSQL, Redis and git."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import make_sessionmaker
from app.models.audit import AuditEvent
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


async def workspace_events(engine: AsyncEngine, project: dict[str, Any], action: str) -> list[dict[str, Any]]:
    """The metadata of the project's ``action`` events, oldest first."""
    async with make_sessionmaker(engine)() as session:
        events = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == action, AuditEvent.resource_id == project["id"])
                .order_by(AuditEvent.id)
            )
        ).all()
    return [event.metadata_ for event in events]


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


# --- writes (Task 3) ----------------------------------------------------------------------------------------


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def put(
    client: httpx.AsyncClient, project: dict[str, Any], path: str, content: str, expected: str | None
) -> httpx.Response:
    return await client.put(
        url(project, f"files/{path}"),
        json={"content": content, "expected_sha256": expected},
        headers=headers(client),
    )


async def test_put_with_the_current_sha_saves_and_audits(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await blank(owner)
    readme = repo_of(workspace_root, project) / "README.md"
    old = sha(readme.read_bytes())
    response = await put(owner, project, "README.md", "# New\n", old)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"path": "README.md", "sha256": sha(b"# New\n")}
    assert readme.read_text() == "# New\n"
    assert await workspace_events(engine, project, "workspace.file_written") == [
        {"path": "README.md", "sha256": sha(b"# New\n"), "old_sha256": old}
    ]


async def test_put_with_a_stale_sha_is_a_version_conflict(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await blank(owner)
    readme = repo_of(workspace_root, project) / "README.md"
    before = readme.read_bytes()
    response = await put(owner, project, "README.md", "# Lost\n", "0" * 64)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "VERSION_CONFLICT")
    assert readme.read_bytes() == before
    assert await workspace_events(engine, project, "workspace.file_written") == []


async def test_put_null_sha_creates_only_new_files(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    assert (await put(owner, project, "src/new.py", "x = 1\n", None)).status_code == 200
    assert (await put(owner, project, "README.md", "# Overwrite\n", None)).status_code == 409


async def test_put_without_the_version_field_is_refused(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    response = await owner.put(
        url(project, "files/README.md"), json={"content": "# x\n"}, headers=headers(owner)
    )
    assert response.status_code == 422


async def test_post_creates_file_and_dir_and_refuses_existing(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await blank(owner)

    async def post(body: dict[str, Any]) -> httpx.Response:
        return await owner.post(url(project, "files"), json=body, headers=headers(owner))

    made = await post({"path": "docs/notes.md", "type": "file", "content": "hi\n"})
    assert (made.status_code, made.json()["data"]) == (201, {"path": "docs/notes.md"})
    assert (await post({"path": "assets", "type": "dir"})).status_code == 201
    assert (repo_of(workspace_root, project) / "assets").is_dir()
    again = await post({"path": "assets", "type": "dir"})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "CONFLICT")
    assert (await post({"path": "x", "type": "dir", "content": "no"})).status_code == 422
    assert [e["type"] for e in await workspace_events(engine, project, "workspace.file_created")] == [
        "file",
        "dir",
    ]


async def test_delete_needs_the_current_sha_and_refuses_folders(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "gone.txt").write_text("bye\n")
    (repo / "folder").mkdir()

    async def delete(path: str, expected: str | None) -> httpx.Response:
        params = {"expected_sha256": expected} if expected is not None else {}
        return await owner.delete(url(project, f"files/{path}"), params=params, headers=headers(owner))

    assert (await delete("gone.txt", None)).status_code == 422
    assert (await delete("gone.txt", "0" * 64)).status_code == 409
    folder = await delete("folder", "0" * 64)
    assert (folder.status_code, folder.json()["error"]["code"]) == (400, "PATH_VIOLATION")
    done = await delete("gone.txt", sha(b"bye\n"))
    assert (done.status_code, done.json()["data"]) == (200, {})
    assert not (repo / "gone.txt").exists() and (repo / "folder").is_dir()
    assert await workspace_events(engine, project, "workspace.file_deleted") == [
        {"path": "gone.txt", "old_sha256": sha(b"bye\n")}
    ]


TWO_FILE_DIFF = """--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
 one
-two
+TWO
--- a/b.txt
+++ b/b.txt
@@ -1,2 +1,2 @@
 {first}
-{second}
+changed
"""


async def test_patch_is_all_or_nothing(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "b.txt").write_text("alpha\nbeta\n")
    diff = TWO_FILE_DIFF.format(first="alpha", second="NOT-BETA")  # the second hunk does not apply
    response = await owner.post(url(project, "patches"), json={"diff": diff}, headers=headers(owner))
    assert (response.status_code, response.json()["error"]["code"]) == (409, "CONFLICT")
    assert response.json()["error"]["details"]  # the rejected hunks
    assert ((repo / "a.txt").read_text(), (repo / "b.txt").read_text()) == ("one\ntwo\n", "alpha\nbeta\n")


async def test_patch_applies_and_audits(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "b.txt").write_text("alpha\nbeta\n")
    diff = TWO_FILE_DIFF.format(first="alpha", second="beta")
    response = await owner.post(url(project, "patches"), json={"diff": diff}, headers=headers(owner))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["rejected"] == []
    assert [(c["path"], c["change"]) for c in data["applied"]] == [
        ("a.txt", "modified"),
        ("b.txt", "modified"),
    ]
    assert (repo / "b.txt").read_text() == "alpha\nchanged\n"
    assert await workspace_events(engine, project, "workspace.patch_applied") == [
        {"files": 2, "paths": ["a.txt", "b.txt"]}
    ]


async def test_patch_target_other_than_main_is_422(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    response = await owner.post(
        url(project, "patches"), json={"diff": "x", "target": "task:1"}, headers=headers(owner)
    )
    assert response.status_code == 422


async def test_an_archived_project_is_read_only(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    assert (await owner.post(url(project, "archive"), headers=headers(owner))).status_code == 200
    assert (await owner.get(url(project, "files/README.md"))).status_code == 200
    writes = [
        await put(owner, project, "README.md", "# x\n", None),
        await owner.post(
            url(project, "files"), json={"path": "n.txt", "type": "file"}, headers=headers(owner)
        ),
        await owner.delete(
            url(project, "files/README.md"), params={"expected_sha256": "0" * 64}, headers=headers(owner)
        ),
        await owner.post(url(project, "patches"), json={"diff": "x"}, headers=headers(owner)),
    ]
    assert [(r.status_code, reason(r)) for r in writes] == [(409, "project_archived")] * 4
