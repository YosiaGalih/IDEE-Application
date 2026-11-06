"""AC-13, FR-063, FR-067, SECURITY_MODEL §5/T7 (TEST_STRATEGY §3): an R3 action runs only with a valid,
unexpired, unused approval for exactly its payload, decided by a signed-in person."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.registry import load_registry
from app.api.deps import current_actor
from app.core.db import make_sessionmaker
from app.models.approvals import Approval
from app.models.audit import AuditEvent
from app.tools.catalog import TOOL_CATALOG
from tests.integration.test_approvals_api import APPROVALS, decide, project, request_delete
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    headers,
    owner,
    owner_id,
    workspace_root,
)
from tests.security.test_cross_project import all_routes

REGISTRY = Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml"


async def status_of(owner: httpx.AsyncClient, project_id: str) -> int | str:  # noqa: F811
    response = await owner.get(f"{PROJECTS}/{project_id}")
    return response.json()["data"]["status"] if response.status_code == 200 else response.status_code


async def test_payload_hash_mismatch_409(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await project(owner)
    approval = (await request_delete(owner, project_id))["approval"]
    assert isinstance(approval, dict)
    response = await decide(owner, {**approval, "payload_hash": "f" * 64})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPROVAL_PAYLOAD_MISMATCH"
    shown = (await owner.get(f"{APPROVALS}/{approval['id']}")).json()["data"]
    assert shown["status"] == "pending"
    assert await status_of(owner, project_id) == "deleting_pending_approval"


async def test_replay_consumed_rejected(owner: httpx.AsyncClient, engine: AsyncEngine) -> None:  # noqa: F811
    project_id = await project(owner)
    approval = (await request_delete(owner, project_id))["approval"]
    assert isinstance(approval, dict)
    assert (await decide(owner, approval)).status_code == 200
    again = await decide(owner, approval)
    # A deleted project's approvals are gone with it (404, like the project); for an action whose project
    # stays, the same replay is 409 "consumed" (test_approvals_api.py::test_decision_reasons_are_409).
    assert again.status_code == 404
    async with make_sessionmaker(engine)() as session:
        deleted_events = await session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "project.deleted", AuditEvent.project_id == uuid.UUID(project_id))
        )
    assert deleted_events == 1  # the action ran once


async def test_expired_rejected(owner: httpx.AsyncClient, engine: AsyncEngine) -> None:  # noqa: F811
    project_id = await project(owner)
    approval = (await request_delete(owner, project_id))["approval"]
    assert isinstance(approval, dict)
    async with engine.begin() as conn:
        await conn.execute(
            update(Approval)
            .where(Approval.id == uuid.UUID(approval["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    response = await decide(owner, approval)
    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["reason"] == "expired"
    assert await status_of(owner, project_id) == "active"  # the lapsed request let the project go back


async def test_superseded_on_payload_change(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await project(owner)
    old = (await request_delete(owner, project_id))["approval"]
    assert isinstance(old, dict)
    renamed = await owner.patch(f"{PROJECTS}/{project_id}", json={"name": "Renamed"}, headers=headers(owner))
    assert renamed.status_code == 200
    response = await decide(owner, old)
    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["reason"] == "superseded"
    pending = (await owner.get(APPROVALS)).json()["data"]
    assert [a["payload"]["name"] for a in pending] == ["Renamed"]
    assert pending[0]["payload_hash"] != old["payload_hash"]
    assert await status_of(owner, project_id) == "deleting_pending_approval"


async def test_agent_cannot_decide(app: FastAPI, owner: httpx.AsyncClient) -> None:  # noqa: F811
    approval = (await request_delete(owner, await project(owner)))["approval"]
    assert isinstance(approval, dict)
    # (a) without a signed-in person there is no decision.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as anonymous:
        response = await anonymous.post(
            f"{APPROVALS}/{approval['id']}/decision",
            json={"decision": "approve", "payload_hash": approval["payload_hash"]},
        )
    assert response.status_code in (401, 403)
    # (b) no tool can approve (R4: never registered).
    assert [name for name in TOOL_CATALOG if "approv" in name] == []
    # (c) no agent profile may grant an approval (FR-043).
    assert {agent.permissions.approval_grant for agent in load_registry(REGISTRY).agents} == {"deny"}
    # (d) the only decision route takes a session-authenticated person (CurrentActor), nothing else.
    decision_routes = [
        (path, route) for path, route in all_routes(app.router.routes) if path.endswith("/decision")
    ]
    assert [path for path, _ in decision_routes] == ["/api/v1/approvals/{approval_id}/decision"]
    route = decision_routes[0][1]
    calls = {dependency.call for dependency in route.dependant.dependencies}
    assert current_actor in calls  # the session's person (SECURITY_MODEL §5), checked again in the service
    # The approval is untouched.
    shown = (await owner.get(f"{APPROVALS}/{approval['id']}")).json()["data"]
    assert shown["status"] == "pending"
