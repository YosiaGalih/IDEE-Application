"""``GET/PATCH /projects/{id}/budget`` (API_CONTRACTS §9, §11.3; spec WP-3.1f Keputusan Lead #2, #13; plan
3.1f1 Task 7): the project's default budget for its tasks, stored as decimal strings, audited, and read by
BudgetGuard. The cross-project matrix covers both routes in ``tests/security/test_cross_project.py``."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.llm.pricing import load_pricing
from app.models.audit import AuditEvent
from app.models.projects import Project
from app.workflow.budget import BudgetDefaults, BudgetGuard, CallBound
from tests.conftest import make_test_env
from tests.integration.test_dashboard_api import add_task
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    create,
    headers,
    owner,
    owner_id,
    workspace_root,
)

INHERIT_ALL = {
    "max_cost_usd": None,
    "max_tokens": None,
    "max_tool_iterations": None,
    "max_duration_minutes": None,
}
ENV_DEFAULTS = {
    "max_cost_usd": "2",
    "max_tokens": 400_000,
    "max_tool_iterations": None,
    "max_duration_minutes": 60,
}


PRICING = load_pricing(Path(__file__).resolve().parents[2] / "config" / "pricing.yaml")


def budget_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/budget"


async def new_project(client: httpx.AsyncClient) -> str:
    response = await create(client, "Anggaran")
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def patch(client: httpx.AsyncClient, project_id: str, body: dict[str, Any]) -> httpx.Response:
    return await client.patch(budget_url(project_id), json=body, headers=headers(client))


async def stored_settings(engine: AsyncEngine, project_id: str) -> dict[str, Any]:
    async with make_sessionmaker(engine)() as session:
        return (await session.execute(select(Project.settings).where(Project.id == project_id))).scalar_one()


async def budget_audits(engine: AsyncEngine, project_id: str) -> list[dict[str, Any]]:
    async with make_sessionmaker(engine)() as session:
        rows = await session.scalars(
            select(AuditEvent.metadata_)
            .where(AuditEvent.action == "project.budget_updated", AuditEvent.resource_id == project_id)
            .order_by(AuditEvent.id)
        )
        return list(rows)


async def test_budget_defaults_to_inherit_everything(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await new_project(owner)
    response = await owner.get(budget_url(project_id))
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {**INHERIT_ALL, "effective": ENV_DEFAULTS}


async def test_budget_patch_sets_and_clears_fields(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await new_project(owner)
    first = await patch(
        owner, project_id, {"max_cost_usd": "2.50", "max_tokens": 1_000, "max_tool_iterations": 0}
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"] == {
        "max_cost_usd": "2.5",
        "max_tokens": 1_000,
        "max_tool_iterations": 0,
        "max_duration_minutes": None,
        "effective": {
            "max_cost_usd": "2.5",
            "max_tokens": 1_000,
            "max_tool_iterations": 0,
            "max_duration_minutes": 60,
        },
    }
    # A key not sent stays; a key sent as null goes back to inheriting.
    second = await patch(owner, project_id, {"max_tokens": None, "max_duration_minutes": 30})
    assert second.status_code == 200, second.text
    data = second.json()["data"]
    assert (
        data["max_cost_usd"],
        data["max_tokens"],
        data["max_tool_iterations"],
        data["max_duration_minutes"],
    ) == (
        "2.5",
        None,
        0,
        30,
    )
    assert data["effective"]["max_tokens"] == 400_000
    assert (await owner.get(budget_url(project_id))).json()["data"] == data


async def test_budget_is_stored_as_a_decimal_string(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id = await new_project(owner)
    assert (await patch(owner, project_id, {"max_cost_usd": 2.5, "max_tokens": 7})).status_code == 200
    assert (await stored_settings(engine, project_id))["budget"] == {
        "max_cost_usd": "2.5",  # never a JSON number: JSONB would give it back as a float
        "max_tokens": 7,
        "max_tool_iterations": None,
        "max_duration_minutes": None,
    }
    async with make_sessionmaker(engine)() as session:
        kind = await session.scalar(
            text("SELECT jsonb_typeof(settings -> 'budget' -> 'max_cost_usd') FROM projects WHERE id = :id"),
            {"id": project_id},
        )
    assert kind == "string"
    assert (await patch(owner, project_id, {"max_cost_usd": "1e2"})).json()["data"]["max_cost_usd"] == "100"


@pytest.mark.parametrize(
    "body",
    [
        {"max_cost_usd": 0.1234567},  # more than six decimal places
        {"max_cost_usd": 0.12345678901234567890},  # a float that lost its precision
        {"max_cost_usd": "NaN"},
        {"max_cost_usd": "Infinity"},
        {"max_cost_usd": -1},
        {"max_cost_usd": "1000.000001"},
        {"max_cost_usd": True},
        {"max_tokens": 0},
        {"max_tokens": 100_000_001},
        {"max_tokens": "5"},
        {"max_tokens": 5.0},
        {"max_tokens": True},
        {"max_tool_iterations": -1},
        {"max_tool_iterations": 10_001},
        {"max_duration_minutes": 0},
        {"max_duration_minutes": 1_441},
        {"max_token": 5},  # an unknown key
        {"max_cost_usd": "2", "budget_period": "monthly"},
    ],
)
async def test_budget_patch_rejects(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
    body: dict[str, Any],
) -> None:
    project_id = await new_project(owner)
    assert (await patch(owner, project_id, {"max_cost_usd": "3", "max_tokens": 9})).status_code == 200
    before = await stored_settings(engine, project_id)
    response = await patch(owner, project_id, body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await stored_settings(engine, project_id) == before


async def test_budget_limits_themselves_are_accepted(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await new_project(owner)
    top = {
        "max_cost_usd": "1000",
        "max_tokens": 100_000_000,
        "max_tool_iterations": 10_000,
        "max_duration_minutes": 1440,
    }
    assert (await patch(owner, project_id, top)).status_code == 200
    bottom = {"max_cost_usd": "0", "max_tokens": 1, "max_tool_iterations": 0, "max_duration_minutes": 1}
    response = await patch(owner, project_id, bottom)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["max_cost_usd"] == "0"


async def test_budget_update_is_audited_once(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id = await new_project(owner)
    assert (await patch(owner, project_id, {"max_cost_usd": "1.5"})).status_code == 200
    assert (await patch(owner, project_id, {"max_cost_usd": "1.50", "max_tokens": 20})).status_code == 200
    audits = await budget_audits(engine, project_id)
    # "1.50" is the stored 1.5: only max_tokens changed in the second request, and only it is recorded.
    assert audits == [
        {"fields": ["max_cost_usd"], "budget": {"max_cost_usd": "1.5"}, "previous": {"max_cost_usd": None}},
        {"fields": ["max_tokens"], "budget": {"max_tokens": 20}, "previous": {"max_tokens": None}},
    ]
    # The same values again change nothing and leave no audit (the update_project pattern).
    same = await patch(owner, project_id, {"max_cost_usd": "1.5", "max_tokens": 20})
    assert same.status_code == 200
    assert len(await budget_audits(engine, project_id)) == 2


async def test_other_settings_keep_the_budget(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id = await new_project(owner)
    url = f"/api/v1/projects/{project_id}"
    stopped = await owner.patch(
        url, json={"settings": {"provider_calls_enabled": False}}, headers=headers(owner)
    )
    assert stopped.status_code == 200, stopped.text
    assert (await patch(owner, project_id, {"max_tokens": 1_234})).status_code == 200
    assert (await stored_settings(engine, project_id))[
        "provider_calls_enabled"
    ] is False  # the budget kept it
    other = await owner.patch(url, json={"settings": {"auto_remediation": True}}, headers=headers(owner))
    assert other.status_code == 200, other.text
    settings = await stored_settings(engine, project_id)
    assert settings["budget"]["max_tokens"] == 1_234  # another setting kept the budget
    assert settings["auto_remediation"] is True
    assert (await owner.get(budget_url(project_id))).json()["data"]["max_tokens"] == 1_234


async def test_patch_project_still_refuses_budget(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await new_project(owner)
    response = await owner.patch(
        f"/api/v1/projects/{project_id}",
        json={"settings": {"budget": {"max_tokens": 5}}},
        headers=headers(owner),
    )
    assert response.status_code == 422, response.text
    assert (await owner.get(budget_url(project_id))).json()["data"]["max_tokens"] is None


async def test_budget_update_waits_for_the_project_row_lock(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id = await new_project(owner)
    async with engine.connect() as holder:
        transaction = await holder.begin()
        await holder.execute(
            text("SELECT 1 FROM projects WHERE id = :id FOR NO KEY UPDATE"), {"id": project_id}
        )
        pending = asyncio.create_task(patch(owner, project_id, {"max_tokens": 77}))
        done, _ = await asyncio.wait({pending}, timeout=0.3)
        assert not done  # the update waits for the row, so no write is lost
        await transaction.commit()
    response = await asyncio.wait_for(pending, 10)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["max_tokens"] == 77


async def test_budget_update_never_loses_a_concurrent_settings_change(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """Why the row lock (U43): without it the PATCH reads the settings before another writer commits, then
    writes them back over that writer's change. With it, the PATCH reads after the commit and keeps both."""
    project_id = await new_project(owner)
    async with engine.connect() as holder:
        transaction = await holder.begin()
        await holder.execute(
            text("UPDATE projects SET settings = settings || CAST(:extra AS jsonb) WHERE id = :id"),
            {"extra": '{"auto_remediation": true}', "id": project_id},
        )
        pending = asyncio.create_task(patch(owner, project_id, {"max_tokens": 77}))
        done, _ = await asyncio.wait({pending}, timeout=0.3)
        assert not done
        await transaction.commit()
    response = await asyncio.wait_for(pending, 10)
    assert response.status_code == 200, response.text
    settings = await stored_settings(engine, project_id)
    assert settings["auto_remediation"] is True  # the other writer's change survived
    assert settings["budget"]["max_tokens"] == 77


# --- a malformed stored budget (AG-13 L3: only a direct DB edit can make one) -----------------------------

BIG_JUNK = '{"max_cost_usd": "' + "x" * 5_000 + '"}'
ALL_FOUR = {
    "max_cost_usd": "1.25",
    "max_tokens": 9_000,
    "max_tool_iterations": None,
    "max_duration_minutes": 30,
}


async def corrupt(engine: AsyncEngine, project_id: str, raw: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE projects SET settings = settings "
                "|| jsonb_build_object('budget', CAST(:raw AS jsonb)) WHERE id = :id"
            ),
            {"raw": raw, "id": project_id},
        )


def assert_budget_invalid(response: httpx.Response) -> None:
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "CONFLICT"
    assert error["details"][0]["reason"] == "budget_invalid"


@pytest.mark.parametrize(
    "raw",
    ['{"max_cost_usd": 2.5}', '{"max_tokens": -1}', '"not a mapping"', BIG_JUNK],
    ids=["float cost", "negative tokens", "not a mapping", "5000 characters of junk"],
)
async def test_a_malformed_stored_budget_is_a_conflict(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
    raw: str,
) -> None:
    project_id = await new_project(owner)
    await corrupt(engine, project_id, raw)
    response = await owner.get(budget_url(project_id))
    assert_budget_invalid(response)
    assert "xxxx" not in response.text


@pytest.mark.parametrize("repair", [ALL_FOUR, dict.fromkeys(ALL_FOUR)], ids=["values", "all inherit"])
async def test_a_patch_with_all_four_keys_repairs_a_malformed_budget(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
    repair: dict[str, Any],
) -> None:
    project_id = await new_project(owner)
    await corrupt(engine, project_id, BIG_JUNK)
    response = await patch(owner, project_id, repair)
    assert response.status_code == 200, response.text
    assert (await stored_settings(engine, project_id))["budget"] == repair
    assert (await owner.get(budget_url(project_id))).status_code == 200
    audits = await budget_audits(engine, project_id)
    assert audits == [{"fields": list(ALL_FOUR), "budget": repair, "previous": "<invalid>"}]
    assert "xxxx" not in str(audits)  # the malformed value is never echoed into the audit trail


async def test_a_partial_patch_of_a_malformed_budget_is_a_conflict(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id = await new_project(owner)
    await corrupt(engine, project_id, '{"max_cost_usd": 2.5}')
    response = await patch(owner, project_id, {"max_tokens": 5})
    assert_budget_invalid(response)
    assert (await stored_settings(engine, project_id))["budget"] == {"max_cost_usd": 2.5}  # untouched
    assert await budget_audits(engine, project_id) == []


async def test_guard_uses_the_patched_project_budget(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id = await new_project(owner)
    assert (await patch(owner, project_id, {"max_cost_usd": "0.75", "max_tokens": 5_000})).status_code == 200
    task = await add_task(engine, uuid.UUID(project_id), owner_id, "draft", "Dijaga")
    guard = BudgetGuard(
        make_sessionmaker(engine),
        PRICING,
        BudgetDefaults.from_settings(load_settings(make_test_env())),
    )
    snapshot = await guard.check_before_attempt(task, CallBound("claude-haiku-5-5", 10, 10))
    assert (snapshot.budget.max_cost_usd, snapshot.budget.max_tokens) == (Decimal("0.75"), 5_000)
    assert snapshot.budget.max_duration_minutes == 60
