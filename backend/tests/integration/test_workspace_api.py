"""Workspace API (WP-2.1c; API_CONTRACTS §3 tree, §4 files, patches) on real PostgreSQL, Redis and git."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import make_sessionmaker
from app.models.audit import AuditEvent
from app.models.projects import Repository
from app.workspace import fs
from app.workspace.errors import PatchRollbackFailed
from app.workspace.fs import Workspace
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
from tests.integration.test_projects import git as git_raw


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


# --- review round -------------------------------------------------------------------------------------------


async def test_post_on_an_existing_file_is_a_conflict(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    response = await owner.post(
        url(project, "files"), json={"path": "README.md", "type": "file"}, headers=headers(owner)
    )
    assert (response.status_code, response.json()["error"]["code"]) == (409, "CONFLICT")


async def test_tree_lists_more_than_500_entries(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    many = repo_of(workspace_root, project) / "many"
    many.mkdir()
    for n in range(600):
        (many / f"f{n:03d}.txt").write_text("x")
    body = (await owner.get(url(project, "tree"), params={"path": "many", "depth": 1})).json()
    assert (len(body["data"]), body["meta"]) == (600, {"truncated": False})


async def test_empty_ref_and_tree_path_mean_the_root(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    assert (await owner.get(url(project, "files/README.md"), params={"ref": ""})).status_code == 200
    assert (await owner.get(url(project, "tree"), params={"path": ""})).status_code == 200


async def test_unknown_body_fields_are_refused(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project = await blank(owner)
    sent = headers(owner)
    put = await owner.put(
        url(project, "files/README.md"),
        json={"content": "x", "expected_sha256": None, "force": True},
        headers=sent,
    )
    post = await owner.post(
        url(project, "files"), json={"path": "n", "type": "file", "mode": 777}, headers=headers(owner)
    )
    patch = await owner.post(
        url(project, "patches"), json={"diff": "x", "apply": "partial"}, headers=headers(owner)
    )
    assert [r.status_code for r in (put, post, patch)] == [422, 422, 422]


async def test_writes_need_a_ready_repository(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await create_upload(owner)
    response = await put(owner, project, "README.md", "# x\n", None)
    assert (response.status_code, reason(response)) == (409, "repository_not_ready")
    assert not (workspace_root / project["id"]).exists()  # nothing created for an unfinished import


async def test_a_failed_import_is_reported_as_an_error(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await create_upload(owner)
    async with make_sessionmaker(engine)() as session:
        repository = await session.scalar(
            select(Repository).where(Repository.project_id == uuid.UUID(project["id"]))
        )
        assert repository is not None
        repository.sync_state, repository.error = "error", "archive_rejected:git_dir"
        await session.commit()
    response = await owner.get(url(project, "tree"))
    assert response.status_code == 409
    assert response.json()["error"]["details"] == [
        {"reason": "repository_error", "error": "archive_rejected:git_dir"}
    ]


async def test_a_symlinked_git_dir_is_a_missing_repository(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    shutil.move(str(repo / ".git"), str(tmp_path / "elsewhere.git"))
    (repo / ".git").symlink_to(tmp_path / "elsewhere.git")
    response = await owner.get(url(project, "tree"))
    assert (response.status_code, reason(response)) == (409, "repository_missing")


async def test_a_busy_workspace_answer_is_not_kept_for_the_key(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await blank(owner)
    repo = repo_of(workspace_root, project)
    readme = sha((repo / "README.md").read_bytes())
    monkeypatch.setattr(fs, "LOCK_WAIT_S", 0.2)
    sent = headers(owner)
    body = {"content": "# Saved\n", "expected_sha256": readme}
    async with Workspace(repo).exclusive():  # a commit in progress
        busy = await owner.put(url(project, "files/README.md"), json=body, headers=sent)
    assert (busy.status_code, reason(busy), busy.headers.get("retry-after")) == (409, "busy", "1")
    retried = await owner.put(url(project, "files/README.md"), json=body, headers=sent)
    assert retried.status_code == 200, retried.text  # the same key runs again, not a replayed 409
    assert retried.headers.get("idempotent-replay") is None


async def test_a_patch_that_cannot_be_rolled_back_is_on_the_record(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await blank(owner)

    async def half_applied(*_: object, **__: object) -> list[object]:
        raise PatchRollbackFailed(["a.txt"])

    monkeypatch.setattr(Workspace, "apply_patch", half_applied)
    response = await owner.post(url(project, "patches"), json={"diff": "x"}, headers=headers(owner))
    assert response.status_code == 500
    async with make_sessionmaker(engine)() as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "workspace.patch_applied", AuditEvent.resource_id == project["id"]
                )
            )
        ).all()
    assert [(e.result, e.metadata_) for e in events] == [
        ("error", {"reason": "rollback_failed", "paths": ["a.txt"]})
    ]


async def test_a_retried_write_with_the_same_key_runs_once(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project = await blank(owner)
    sent = headers(owner)
    body = {"content": "# Once\n", "expected_sha256": None}
    first = await owner.put(url(project, "files/new.md"), json=body, headers=sent)
    again = await owner.put(url(project, "files/new.md"), json=body, headers=sent)
    assert (first.status_code, again.status_code) == (200, 200)
    assert again.headers.get("idempotent-replay") == "true"
    assert again.json()["data"] == first.json()["data"]


async def test_search_text_and_symbol(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    """TEST_STRATEGY AC-03: the IDE searches text (working tree) and symbols (index)."""
    from tests.integration.test_repo_index import index_env, project_with_code, run_job

    project_id, repo = await project_with_code(owner, workspace_root)
    (repo / "src" / "app.py").write_text("def greet():\n    return 'hi'\n")
    git_raw(repo, "add", "src/app.py")
    git_raw(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "greet")
    assert await run_job(index_env(app, workspace_root), project_id, owner_id) == "indexed"
    text = await owner.get(f"{PROJECTS}/{project_id}/search?q=greet")
    assert text.json()["data"] == [{"path": "src/app.py", "line": 1, "preview": "def greet():"}]
    symbol = await owner.get(f"{PROJECTS}/{project_id}/search?q=gre&mode=symbol")
    assert symbol.json()["data"] == [
        {"path": "src/app.py", "line": 1, "preview": "function greet", "symbol": "greet", "kind": "function"}
    ]
