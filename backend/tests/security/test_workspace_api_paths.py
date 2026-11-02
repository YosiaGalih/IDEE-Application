"""T2 through the workspace API (WP-2.1c): a path from the URL never leaves ``repo/``.

The vectors are percent-encoded so they reach the route's ``{path:path}`` as written (an HTTP client would
resolve a literal ``..`` before sending). The sentinel files sit next to ``repo/`` and above the project.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    headers,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_workspace_api import blank, repo_of, url

VECTORS = [
    "%2e%2e%2fsecret.txt",
    "..%2fsecret.txt",
    "docs%2f..%2f..%2fsecret.txt",
    "%2e%2e%2f%2e%2e%2fabove.txt",
    "x%00y",
    ".git%2fconfig",
    ".git/config",
    "out/secret.txt",  # through a symlink that leaves the root
]


def refused(response: httpx.Response) -> bool:
    """Refused by the workspace's path check itself, not by routing or validation."""
    return (response.status_code, response.json()["error"]["code"]) == (400, "PATH_VIOLATION")


@pytest.mark.parametrize("vector", VECTORS)
async def test_paths_from_the_url_never_leave_the_repository(
    owner: httpx.AsyncClient,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    tmp_path: Path,
    vector: str,
) -> None:
    project: dict[str, Any] = await blank(owner)
    repo = repo_of(workspace_root, project)
    secret = repo.parent / "secret.txt"
    above = workspace_root / "above.txt"
    secret.write_text("project secret\n")
    above.write_text("root secret\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside secret\n")
    (repo / "out").symlink_to(outside)
    before = {p: p.read_bytes() for p in (secret, above, outside / "secret.txt")}
    git_config = (repo / ".git" / "config").read_bytes()
    target = url(project, f"files/{vector}")

    read = await owner.get(target)
    assert refused(read), read.text
    assert "secret" not in read.text
    write = await owner.put(
        target, json={"content": "pwned\n", "expected_sha256": None}, headers=headers(owner)
    )
    assert refused(write), write.text
    delete = await owner.delete(
        target,
        params={"expected_sha256": hashlib.sha256(b"project secret\n").hexdigest()},
        headers=headers(owner),
    )
    assert refused(delete), delete.text
    assert {p: p.read_bytes() for p in before} == before
    assert (repo / ".git" / "config").read_bytes() == git_config
