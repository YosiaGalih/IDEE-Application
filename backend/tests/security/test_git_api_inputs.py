"""T3 through the git API (WP-2.1c): branch names, refs and paths from a request are never git options or
pathspec magic (FR-117), and nothing destructive is reachable (FR-118)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.integration.test_git_api import post_json
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    git,
    headers,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_workspace_api import blank, repo_of, url

OPTION_LIKE = ["-x", "--output={pwned}", "--upload-pack=touch {pwned}", "-f"]


@pytest.mark.parametrize("value", OPTION_LIKE)
async def test_git_inputs_are_never_options(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
    value: str,
) -> None:
    pwned = tmp_path / "pwned"
    value = value.format(pwned=pwned)
    project: dict[str, Any] = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "README.md").write_text("# changed\n")
    head = git(repo, "rev-parse", "HEAD")
    responses = [
        await owner.get(url(project, "git/diff"), params={"base": value}),
        await post_json(owner, project, "git/branch", {"name": value}),
        await post_json(owner, project, "git/branch", {"name": "ok", "from": value}),
        await post_json(owner, project, "git/commit", {"message": "x", "paths": [value]}),
    ]
    for response in responses:
        assert response.status_code in {400, 409, 422}, response.text
    assert git(repo, "rev-parse", "HEAD") == head  # nothing was committed
    assert "ok" not in git(repo, "branch", "--format=%(refname:short)").split()
    assert not pwned.exists()


async def test_magic_pathspec_is_literal(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    project: dict[str, Any] = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / "README.md").write_text("# changed\n")
    (repo / "other.txt").write_text("other\n")
    head = git(repo, "rev-parse", "HEAD")
    for magic in (":(glob)**", ":/", ":!README.md", "*"):
        response = await post_json(owner, project, "git/commit", {"message": "magic", "paths": [magic]})
        assert response.status_code in {400, 409}, response.text
    assert git(repo, "rev-parse", "HEAD") == head  # never "commit everything"


async def test_a_failed_add_leaves_nothing_for_the_next_commit(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
) -> None:
    """AG-13 P11: ``add`` stages some paths, then fails on an ignored one; the next commit holds its own."""
    project: dict[str, Any] = await blank(owner)
    repo = repo_of(workspace_root, project)
    (repo / ".gitignore").write_text("ignored.txt\n")
    git(repo, "add", ".gitignore")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-q", "-m", "ignore")
    for name in ("a.txt", "ignored.txt", "c.txt"):
        (repo / name).write_text(f"{name}\n")
    failed = await post_json(
        owner, project, "git/commit", {"message": "x", "paths": ["a.txt", "ignored.txt"]}
    )
    assert failed.status_code == 409
    assert (
        await post_json(owner, project, "git/commit", {"message": "only c", "paths": ["c.txt"]})
    ).status_code == 201
    assert git(repo, "show", "--name-only", "--format=", "HEAD") == "c.txt"
