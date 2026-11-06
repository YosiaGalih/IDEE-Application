"""Security regressions for deleting a project (TEST_STRATEGY §1.5: every security-bug test lives here).

AG-13 C1: the action that runs is the action that was approved (the hash is derived from the project as it
is now). AG-13 C2: one project that cannot be purged does not keep the others' data past 7 days.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.approvals import Approval
from app.models.projects import Project
from app.services import project_delete, projects
from tests.integration.test_project_delete import (  # noqa: F401 - fixtures and helpers shared
    NOW,
    approve,
    audit_actions,
    blank,
    deleted_project,
    env,
    gone,
    handler,
    load,
)
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    owner_actor,
    owner_id,
    workspace_root,
)


async def test_tampered_payload_is_not_consumed(
    env: projects.ProjectEnv,  # noqa: F811
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,  # noqa: F811
) -> None:
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    approval, _ = await project_delete.request_delete(env, actor, project_id)
    await approve(engine, approval.id)
    async with engine.begin() as conn:  # edited after the decision: the hash no longer describes it
        await conn.execute(
            update(Approval)
            .where(Approval.id == approval.id)
            .values(payload={**approval.payload, "previous_status": "archived"})
        )
    with pytest.raises(RuntimeError, match="no longer matches what was approved"):
        await handler.approved(approval.id, now=NOW)
    assert (await load(engine, Project, project_id)).status == "deleting_pending_approval"
    assert (await load(engine, Approval, approval.id)).status == "approved"


async def test_a_project_renamed_behind_the_approval_is_not_deleted(
    env: projects.ProjectEnv,  # noqa: F811
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,  # noqa: F811
) -> None:
    # AG-13 C1: the hash is derived from the project as it is when the action runs, not read from the row.
    actor = owner_actor(owner_id)
    project_id = await blank(env, actor)
    approval, _ = await project_delete.request_delete(env, actor, project_id)
    await approve(engine, approval.id)
    async with engine.begin() as conn:  # a writer that forgot to supersede, or a hand edit
        await conn.execute(update(Project).where(Project.id == project_id).values(name="Something else"))
    with pytest.raises(RuntimeError, match="no longer matches what was approved"):
        await handler.approved(approval.id, now=NOW)
    assert (await load(engine, Project, project_id)).status == "deleting_pending_approval"
    assert (await load(engine, Approval, approval.id)).status == "approved"


async def test_purge_failure_is_recorded_and_others_continue(
    env: projects.ProjectEnv,  # noqa: F811
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
    workspace_root: Path,  # noqa: F811
    handler: project_delete.ProjectDeleteHandler,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stuck = await deleted_project(env, engine, owner_id, handler)
    actor = owner_actor(owner_id)
    other_id = await blank(env, actor, "Other")
    approval, _ = await project_delete.request_delete(env, actor, other_id)
    await approve(engine, approval.id)
    await handler.approved(approval.id, now=NOW + timedelta(seconds=1))
    real = project_delete._remove_folder

    def flaky(root: Path, project_id: uuid.UUID) -> str:
        if project_id == stuck:
            raise PermissionError("sandbox-owned file")
        return real(root, project_id)

    monkeypatch.setattr(project_delete, "_remove_folder", flaky)
    result = await project_delete.purge_deleted(
        sessions=env.sessions, audit=env.audit, workspace_root=workspace_root, now=NOW + timedelta(days=8)
    )
    assert (result.purged, result.failed) == ([other_id], [stuck])
    assert not await gone(engine, Project, stuck)  # kept, to be retried
    assert "project.purge_failed" in await audit_actions(engine, stuck)
