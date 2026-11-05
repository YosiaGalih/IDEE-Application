"""``GET /agents`` and ``GET /agents/{id}`` (FR-044 to FR-046 read side, spec WP-3.1b goal 7)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.registry import load_registry
from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.models.agents import AgentRun
from app.services.agents import sync_registry
from tests.integration.test_dashboard_api import add_project, add_task
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    owner,
    owner_id,
    workspace_root,
)

AGENTS = "/api/v1/agents"
REGISTRY = Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml"


@pytest.fixture(autouse=True)
async def synced(engine: AsyncEngine, clean_state: None) -> AsyncIterator[None]:  # noqa: F811
    # httpx's ASGITransport does not run the lifespan, so the sync the API does at start is done here.
    async with make_sessionmaker(engine)() as session:
        await sync_registry(session, load_registry(REGISTRY), AuditWriter(Redactor()))
        await session.commit()
    yield


async def add_run(
    engine: AsyncEngine, task_id: uuid.UUID, agent_id: str = "A02", **values: object
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with make_sessionmaker(engine)() as session:
        session.add(
            AgentRun(id=run_id, task_id=task_id, agent_id=agent_id, agent_version=1, step="intake", **values)
        )
        await session.commit()
    return run_id


async def test_list_returns_every_agent_with_counts(owner: httpx.AsyncClient) -> None:  # noqa: F811
    response = await owner.get(AGENTS)
    assert response.status_code == 200, response.text
    body = response.json()
    agents = body["data"]
    assert len(agents) == 53 == body["meta"]["total"]
    assert body["meta"]["counts"]["mode"] == {"llm": 6, "profile": 7, "tool": 7, "planned": 33}
    assert body["meta"]["counts"]["status"] == {"planned": 33, "disabled": 0, "not_ready": 20, "ready": 0}
    a02 = next(a for a in agents if a["agent_id"] == "A02")
    assert a02["permission_profile"] == {
        "filesystem": "read_only",
        "sandbox": "deny",
        "git": "none",
        "network": "deny",
        "deployment": "deny",
        "portfolio": "none",
        "data_sensitivity_max": "confidential",
        "approval_grant": "deny",
    }
    assert a02["status"] == "not_ready"
    assert a02["status_reasons"] == ["runtime_unavailable", "provider_readiness_unknown", "tools_unavailable"]
    assert "submit_result" not in a02["unavailable_tools"]
    assert (a02["input_contract"], a02["output_contract"]) == ("IntakeInput", "IntakeOutput")
    assert a02["purpose"] and a02["triggers"]
    # Grouped as the catalog shows them, by id within a group.
    groups = [a["group"] for a in agents]
    assert groups.index("planning_intent") < groups.index("orchestration")


async def test_no_enabled_agent_is_ready(owner: httpx.AsyncClient) -> None:  # noqa: F811
    agents = (await owner.get(AGENTS)).json()["data"]
    assert [a["agent_id"] for a in agents if a["status"] == "ready"] == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("group=orchestration", {"ORCH"}),
        ("mode=llm", {"A02", "A03", "B14", "C20", "D22", "A07"}),
        ("status=disabled", set()),
        ("q=a02", {"A02"}),
        ("q=requirement analyst", {"A02"}),
        ("q=git.commit_task", None),  # by tool name: every agent that allowlists it
    ],
)
async def test_filters(owner: httpx.AsyncClient, query: str, expected: set[str] | None) -> None:  # noqa: F811
    response = await owner.get(f"{AGENTS}?{query}")
    assert response.status_code == 200, response.text
    found = {a["agent_id"] for a in response.json()["data"]}
    if expected is None:
        assert found and all("git.commit_task" in a["tools"] for a in response.json()["data"])
    else:
        assert found == expected
    assert response.json()["meta"]["counts"]["mode"]["llm"] == 6  # chip counts ignore the filters


@pytest.mark.parametrize(
    "query", ["group=marketing", "mode=agentic", "status=busy", "q=" + "x" * 101, "q=a%00b"]
)
async def test_bad_filters_are_422(owner: httpx.AsyncClient, query: str) -> None:  # noqa: F811
    assert (await owner.get(f"{AGENTS}?{query}")).status_code == 422


async def test_detail(owner: httpx.AsyncClient) -> None:  # noqa: F811
    response = await owner.get(f"{AGENTS}/B14")
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["agent"]["agent_id"] == "B14"
    assert body["recent_runs"] == []
    assert body["stats"] == {"runs": 0, "success_rate": None, "avg_duration_ms": None}


@pytest.mark.parametrize("agent_id", ["Z99", "a02", "ORCH2", "../A02"])
async def test_detail_unknown_is_404(owner: httpx.AsyncClient, agent_id: str) -> None:  # noqa: F811
    assert (await owner.get(f"{AGENTS}/{agent_id}")).status_code == 404


async def test_detail_lists_runs_newest_first_with_stats(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
    owner_id: uuid.UUID,  # noqa: F811
) -> None:
    project = await add_project(engine, owner_id, "Runs")
    task = await add_task(engine, project, owner_id, "running", "Run task")
    await add_run(engine, task, status="succeeded", duration_ms=1000)
    await add_run(engine, task, status="failed", duration_ms=3000)
    await add_run(engine, task, status="running")
    body = (await owner.get(f"{AGENTS}/A02")).json()["data"]
    assert [run["status"] for run in body["recent_runs"]] == ["running", "failed", "succeeded"]
    assert body["recent_runs"][0]["project_id"] == str(project)
    assert body["recent_runs"][0]["agent_name"] == "Requirement Analyst"
    assert body["stats"] == {"runs": 3, "success_rate": 0.5, "avg_duration_ms": 2000}


async def test_requires_sign_in(app: FastAPI) -> None:  # noqa: F811
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as anonymous:
        assert (await anonymous.get(AGENTS)).status_code == 401
        assert (await anonymous.get(f"{AGENTS}/A02")).status_code == 401
