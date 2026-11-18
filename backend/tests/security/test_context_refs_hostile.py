"""Context refs against slow or hostile trees (AG-13 WP-3.2a MEDIUM T4 and INFO-1; Rulings R7, R11, R12).

A ref check never reads file content, never runs while the request holds a database transaction or the
project row, probes each distinct ref once, and refuses path names carrying hidden characters.
"""

# ruff: noqa: F811 - fixtures imported from the shared test modules are used as arguments
from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.workspace import fs
from app.workspace.fs import Workspace
from tests.integration.test_conversations_api import new_conversation, post_message
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_tasks_api import (  # noqa: F401
    open_gate,
    post_task,
    project,
    repo_of,
    spy_disk,
    task_rows,
)

FILE_REF = {"type": "file", "path": "README.md"}


async def test_twenty_identical_refs_are_probed_once(
    owner: httpx.AsyncClient, open_gate: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await project(owner)
    touched = spy_disk(monkeypatch)
    response = await post_task(owner, project_id, context_refs=[FILE_REF] * 20)
    assert response.status_code == 202, response.text
    assert touched == ["probe"]
    assert response.json()["data"]["context_refs"] == [FILE_REF]


async def test_a_ref_never_reads_the_file(
    owner: httpx.AsyncClient,
    open_gate: None,
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 64 MiB file (sparse here) costs one ``fstat``: the read/hash helper is never reached."""
    project_id = await project(owner)
    with (repo_of(workspace_root, project_id) / "big.bin").open("wb") as handle:
        handle.truncate(fs.HASH_LIMIT)

    def no_read(*_: object) -> NoReturn:
        raise AssertionError("a context ref read file content")

    monkeypatch.setattr(fs, "_read_fd", no_read)
    response = await post_task(owner, project_id, context_refs=[{"type": "file", "path": "big.bin"}])
    assert response.status_code == 202, response.text


async def _held_by_others(engine: AsyncEngine, project_id: str) -> tuple[int, bool]:
    """(transactions open on other connections, whether the project row is locked)."""
    async with engine.connect() as conn:
        open_transactions = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
                )
            )
        ).scalar_one()
        try:
            await conn.execute(
                text("SELECT id FROM projects WHERE id = :id FOR UPDATE NOWAIT"), {"id": project_id}
            )
            locked = False
        except DBAPIError:
            locked = True
        await conn.rollback()
    return open_transactions, locked


@pytest.mark.parametrize("create_task", [True, False])
async def test_paths_are_probed_with_no_transaction_open_and_the_project_unlocked(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    create_task: bool,
) -> None:
    """R11: a slow disk must not hold a pooled connection or the project row (16 parallel posts slowed an
    unrelated request about 385 times in the AG-13 run)."""
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    seen: list[tuple[int, bool]] = []
    for name in ("probe", "read_file", "tree"):
        original = getattr(Workspace, name)

        async def watched(self: Workspace, *args: Any, _original: Any = original, **kwargs: Any) -> Any:
            seen.append(await _held_by_others(engine, project_id))
            return await _original(self, *args, **kwargs)

        monkeypatch.setattr(Workspace, name, watched)
    via_api = await post_task(owner, project_id, context_refs=[FILE_REF])
    assert via_api.status_code == 202, via_api.text
    via_chat = await post_message(owner, cid, create_task=create_task, context_refs=[FILE_REF])
    assert via_chat.status_code in (201, 202), via_chat.text
    assert seen == [(0, False), (0, False)]


@pytest.mark.parametrize("path", ["a\nb.py", "a\u202eb.py", "a\u200bb.py", "src/a\tb"])
async def test_paths_with_hidden_characters_are_invalid(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
    workspace_root: Path,
    path: str,
) -> None:
    """R12 (AG-13 INFO-1): such names would reach A04 prompts and the UI; refused even if the file exists."""
    project_id = await project(owner)
    target = repo_of(workspace_root, project_id) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n")
    cid = await new_conversation(owner, project_id)
    for response in (
        await post_task(owner, project_id, context_refs=[{"type": "file", "path": path}]),
        await post_message(owner, cid, create_task=False, context_refs=[{"type": "file", "path": path}]),
    ):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"][0]["reason"] == "context_ref_invalid"
        assert path not in response.text
    assert await task_rows(engine) == []
