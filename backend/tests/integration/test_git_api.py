"""Git API (WP-2.1c; API_CONTRACTS §4 git) on real PostgreSQL, Redis and git."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.workspace.fs import Workspace
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    git,
    headers,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_workspace_api import blank, repo_of, url, workspace_events


def raw(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, test helper
        ["git", "--git-dir", str(repo / ".git"), "--work-tree", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.test",
            "PATH": "/usr/bin:/bin",
        },
    ).stdout.strip()


async def project_with_repo(client: httpx.AsyncClient, root: Path) -> tuple[dict[str, Any], Path]:
    project = await blank(client)
    return project, repo_of(root, project)


# --- reads (Task 4) -----------------------------------------------------------------------------------------


async def test_status_shows_a_modified_file(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project, repo = await project_with_repo(owner, workspace_root)
    (repo / "README.md").write_text("# changed\n")
    response = await owner.get(url(project, "git/status"))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["branch"], data["clean"]) == ("main", False)
    assert data["head"] == git(repo, "rev-parse", "HEAD")
    assert data["changes"] == [{"path": "README.md", "status": "modified"}]


async def test_diff_against_head_includes_untracked(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project, repo = await project_with_repo(owner, workspace_root)
    (repo / "README.md").write_text("# changed\n")
    (repo / "new.py").write_text("print(1)\n")
    data = (await owner.get(url(project, "git/diff"))).json()["data"]
    assert data["head"] is None and data["truncated"] is False
    statuses = {file["path"]: file["status"] for file in data["files"]}
    assert statuses == {"README.md": "modified", "new.py": "added"}
    assert data["stats"]["files"] == 2
    only = (await owner.get(url(project, "git/diff"), params={"path": "new.py"})).json()["data"]
    assert [file["path"] for file in only["files"]] == ["new.py"]


async def test_diff_is_truncated_past_one_mebibyte(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project, repo = await project_with_repo(owner, workspace_root)
    (repo / "big.txt").write_text("".join(f"line {n}\n" for n in range(200_000)))
    data = (await owner.get(url(project, "git/diff"))).json()["data"]
    assert data["truncated"] is True


async def test_branches_mark_task_branches(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project, repo = await project_with_repo(owner, workspace_root)
    raw(repo, "branch", "kk/task-x")
    data = (await owner.get(url(project, "git/branches"))).json()["data"]
    assert {(b["name"], b["is_task_branch"]) for b in data} == {("main", False), ("kk/task-x", True)}


async def test_log_is_newest_first_and_limited(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project, repo = await project_with_repo(owner, workspace_root)
    for n in (1, 2):
        (repo / f"f{n}.txt").write_text(f"{n}\n")
        raw(repo, "add", "-A")
        raw(repo, "commit", "-q", "-m", f"commit {n}")
    data = (await owner.get(url(project, "git/log"), params={"limit": 2})).json()["data"]
    assert [entry["message"] for entry in data] == ["commit 2", "commit 1"]
    assert (await owner.get(url(project, "git/log"), params={"limit": 500})).status_code == 422


# --- writes (Task 5) ----------------------------------------------------------------------------------------


async def post_json(
    client: httpx.AsyncClient, project: dict[str, Any], tail: str, body: dict[str, Any]
) -> httpx.Response:
    return await client.post(url(project, tail), json=body, headers=headers(client))


async def test_branch_is_created_without_checkout(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project, repo = await project_with_repo(owner, workspace_root)
    response = await post_json(owner, project, "git/branch", {"name": "feature/login"})
    assert response.status_code == 201, response.text
    head = git(repo, "rev-parse", "HEAD")
    assert response.json()["data"] == {"name": "feature/login", "head": head}
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"  # no checkout
    assert await workspace_events(engine, project, "git.branch_created") == [
        {"name": "feature/login", "head": head}
    ]


async def test_branch_names_under_kk_are_reserved(owner: httpx.AsyncClient, workspace_root: Path) -> None:  # noqa: F811
    project, _ = await project_with_repo(owner, workspace_root)
    assert (await post_json(owner, project, "git/branch", {"name": "kk/mine"})).status_code == 422


async def test_existing_branch_is_a_conflict(owner: httpx.AsyncClient, workspace_root: Path) -> None:  # noqa: F811
    project, _ = await project_with_repo(owner, workspace_root)
    assert (await post_json(owner, project, "git/branch", {"name": "dev"})).status_code == 201
    again = await post_json(owner, project, "git/branch", {"name": "dev"})
    assert (again.status_code, again.json()["error"]["details"]) == (409, [{"reason": "branch_exists"}])


async def test_commit_records_the_user_as_author_and_audits(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project, repo = await project_with_repo(owner, workspace_root)
    (repo / "README.md").write_text("# Fixed\n")
    (repo / "other.txt").write_text("later\n")
    response = await post_json(
        owner, project, "git/commit", {"message": "Fix readme", "paths": ["README.md"]}
    )
    assert response.status_code == 201, response.text
    commit = git(repo, "rev-parse", "HEAD")
    assert response.json()["data"] == {"commit": commit}
    assert git(repo, "log", "-1", "--format=%an|%s") == "Tira|Fix readme"
    assert git(repo, "show", "--name-only", "--format=", "HEAD") == "README.md"
    assert await workspace_events(engine, project, "git.commit") == [{"commit": commit, "paths": 1}]


async def test_commit_with_nothing_is_409(owner: httpx.AsyncClient, workspace_root: Path) -> None:  # noqa: F811
    project, _ = await project_with_repo(owner, workspace_root)
    response = await post_json(owner, project, "git/commit", {"message": "Nothing"})
    assert (response.status_code, response.json()["error"]["details"]) == (
        409,
        [{"reason": "nothing_to_commit"}],
    )


async def test_a_write_during_a_commit_is_either_in_it_or_left_for_the_next(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project, repo = await project_with_repo(owner, workspace_root)
    (repo / "a.txt").write_text("before\n")
    readme = (repo / "README.md").read_bytes()
    commit, write = await asyncio.gather(
        post_json(owner, project, "git/commit", {"message": "Snapshot"}),
        owner.put(
            url(project, "files/README.md"),
            json={"content": "# During\n", "expected_sha256": hashlib.sha256(readme).hexdigest()},
            headers=headers(owner),
        ),
    )
    assert (commit.status_code, write.status_code) == (201, 200)
    committed = git(repo, "show", "HEAD:README.md")
    assert committed in ("# During", readme.decode().rstrip("\n"))
    if committed != "# During":  # the write came after the snapshot: it waits for the next commit
        assert "README.md" in git(repo, "status", "--porcelain")
    assert (repo / "README.md").read_text() == "# During\n"


async def test_a_commit_waits_for_a_write_in_progress(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    """The commit takes the lock every save takes: it never stages a tree a save is still changing."""
    project, repo = await project_with_repo(owner, workspace_root)
    (repo / "a.txt").write_text("staged\n")
    async with Workspace(repo).exclusive():  # a save in progress
        committing = asyncio.create_task(post_json(owner, project, "git/commit", {"message": "Waits"}))
        await asyncio.sleep(0.5)
        assert not committing.done()
    assert (await committing).status_code == 201
