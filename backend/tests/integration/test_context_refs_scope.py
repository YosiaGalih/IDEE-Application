"""``validate_refs`` against a real project folder: scope, hiding, kinds and the reason per index."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AppError
from app.services.context_refs import ContextRef, validate_refs
from app.workspace.fs import Workspace
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task

ADAPTER: TypeAdapter[ContextRef] = TypeAdapter(ContextRef)


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("a = 1\nb = 2\nc = 3\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("x\n")
    (root / ".env").write_text("SECRET=1\n")
    (root / "private.txt").write_text("hidden\n")
    (root / ".agentignore").write_text("private.txt\n")
    (tmp_path / "outside.txt").write_text("outside\n")
    os.symlink(tmp_path / "outside.txt", root / "link.txt")
    os.symlink(tmp_path, root / "linkdir")
    return root


def refs(*raw: dict[str, object]) -> list[ContextRef]:
    return [ADAPTER.validate_python(item) for item in raw]


def file_ref(path: str, **extra: object) -> dict[str, object]:
    return {"type": "file", "path": path, **extra}


def folder_ref(path: str) -> dict[str, object]:
    return {"type": "folder", "path": path}


async def reason_of(
    session: AsyncSession, repo: Path, project_id: uuid.UUID, *raw: dict[str, object]
) -> dict[str, object]:
    with pytest.raises(AppError) as caught:
        await validate_refs(session, Workspace(repo), project_id, refs(*raw))
    error = caught.value
    assert (error.code, error.http_status) == ("VALIDATION_ERROR", 422)
    assert error.details is not None
    return error.details[0]


async def test_file_and_folder_refs_are_normalised(session: AsyncSession, repo: Path) -> None:
    project = await make_project(session)
    out = await validate_refs(
        session,
        Workspace(repo),
        project.id,
        refs(
            file_ref("src/app.py", line_start=1, line_end=3),
            folder_ref("src"),
            file_ref("src/app.py", line_start=1, line_end=3),
            file_ref("src/app.py"),
        ),
    )
    assert out == [
        {"type": "file", "path": "src/app.py", "line_start": 1, "line_end": 3},
        {"type": "folder", "path": "src"},
        {"type": "file", "path": "src/app.py"},
    ]


@pytest.mark.parametrize(
    "path",
    [
        "../x",
        "/etc/passwd",
        "a//b",
        "a/./b",
        "a\\b",
        "src/",
        "",
        ".",
        "link.txt",
        "linkdir/outside.txt",
        "a\x00b",
    ],
)
@pytest.mark.parametrize("kind", ["file", "folder"])
async def test_escaping_paths_are_refused(session: AsyncSession, repo: Path, kind: str, path: str) -> None:
    project = await make_project(session)
    detail = await reason_of(session, repo, project.id, {"type": kind, "path": path})
    assert detail["reason"] in {"context_ref_invalid", "context_ref_hidden"}


async def test_hidden_paths_are_refused(session: AsyncSession, repo: Path) -> None:
    project = await make_project(session)
    for path in ("private.txt", "node_modules/x.js", ".env"):
        detail = await reason_of(session, repo, project.id, file_ref(path))
        assert detail["reason"] == "context_ref_hidden"
    assert (await reason_of(session, repo, project.id, folder_ref("node_modules")))["reason"] == (
        "context_ref_hidden"
    )


async def test_missing_or_wrong_kind_is_not_found(session: AsyncSession, repo: Path) -> None:
    project = await make_project(session)
    for raw in (file_ref("nope.py"), file_ref("src"), folder_ref("src/app.py"), folder_ref("nope")):
        assert (await reason_of(session, repo, project.id, raw))["reason"] == "context_ref_not_found"


async def test_task_refs_must_be_in_the_project(session: AsyncSession, repo: Path) -> None:
    mine = await make_project(session)
    other = await make_project(session, slug="other")
    own_task = await make_task(session, mine)
    foreign = await make_task(session, other)
    out = await validate_refs(
        session,
        Workspace(repo),
        mine.id,
        refs({"type": "task", "id": str(own_task.id)}, {"type": "diff", "task_id": str(own_task.id)}),
    )
    assert out == [
        {"type": "task", "id": str(own_task.id)},
        {"type": "diff", "task_id": str(own_task.id)},
    ]
    for raw in (
        {"type": "task", "id": str(foreign.id)},
        {"type": "diff", "task_id": str(foreign.id)},
        {"type": "task", "id": str(uuid.uuid4())},
    ):
        assert (await reason_of(session, repo, mine.id, raw))["reason"] == "context_ref_not_found"


async def test_reversed_lines_are_invalid(session: AsyncSession, repo: Path) -> None:
    project = await make_project(session)
    detail = await reason_of(session, repo, project.id, file_ref("src/app.py", line_start=3, line_end=2))
    assert detail["reason"] == "context_ref_invalid"


async def test_unavailable_kinds(session: AsyncSession, repo: Path) -> None:
    project = await make_project(session)
    for kind in ("test_run", "finding"):
        detail = await reason_of(session, repo, project.id, {"type": kind, "id": str(uuid.uuid4())})
        assert detail["reason"] == "context_ref_unavailable"


async def test_reason_names_the_index(session: AsyncSession, repo: Path) -> None:
    project = await make_project(session)
    detail = await reason_of(
        session, repo, project.id, folder_ref("src"), file_ref("src/app.py"), file_ref("nope.py")
    )
    assert detail["loc"] == ["body", "context_refs", 2]
    assert isinstance(detail["msg"], str)
    assert "nope" not in detail["msg"]
