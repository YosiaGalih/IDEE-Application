"""AC-02 through the whole path (WP-2.1b): every hostile archive of WP-1.2b, uploaded over HTTP and run by the
import job, ends as a visible error and one ``project.import`` error event, with nothing left behind (no
repository, no work folder, no upload, no lock).

The archives and their expected reasons come from ``test_archive_import.REJECT_CASES``; each carries its own
limits (small ones keep the bombs fast), passed to the job as the worker would pass the defaults.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services import project_imports
from tests.integration.test_project_imports import (  # noqa: F401 - fixtures and helpers
    RecordingQueue,
    create_upload,
    held,
    import_env,
    import_events,
    queue,
    upload,
)
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    owner,
    owner_id,
    workspace_root,
)
from tests.security.test_archive_import import REJECT_CASES


@pytest.mark.parametrize("case", list(REJECT_CASES))
async def test_rejects_over_http(
    case: str,
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    queue: RecordingQueue,  # noqa: F811
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    builder, reasons = REJECT_CASES[case]
    archive, limits = builder(tmp_path)
    project = await create_upload(owner)
    project_id = uuid.UUID(str(project["id"]))
    uploads = workspace_root / str(project_id) / "uploads"
    response = await upload(owner, project_id, archive.read_bytes(), name=archive.name)
    if response.status_code == 400:
        # Not an archive at all: refused at the door by its first bytes, before any job.
        assert reasons == {"format"}
        assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
        assert queue.calls == []
        assert await import_events(engine, project_id) == []  # no import was ever requested
    else:
        assert response.status_code == 202, response.text
        job_id = uuid.UUID(response.json()["data"]["job_id"])
        env = import_env(app, workspace_root, RecordingQueue())
        outcome = await project_imports.run_import(
            env, project_id=project_id, job_id=job_id, user_id=owner_id, filename=archive.name, limits=limits
        )
        assert outcome == "error"
        data = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
        kind, _, reason = (data["repository"]["error"] or "").partition(":")
        assert (data["repository"]["sync_state"], kind) == ("error", "archive_rejected")
        assert reason in reasons
        # T10: one outcome event, with the same reason the user sees.
        assert await import_events(engine, project_id) == [("error", data["repository"]["error"])]
    assert not (workspace_root / str(project_id) / "repo").exists()
    assert sorted(p.name for p in (workspace_root / str(project_id)).iterdir()) == ["uploads"]
    assert list(uploads.iterdir()) == []
    assert await held(project_id) is None
