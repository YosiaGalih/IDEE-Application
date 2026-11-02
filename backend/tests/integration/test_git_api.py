"""Git API (WP-2.1c; API_CONTRACTS §4 git) on real PostgreSQL, Redis and git."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx

from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    git,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_workspace_api import blank, repo_of, url


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
