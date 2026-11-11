"""``GET /usage`` (API_CONTRACTS §9, §11 #9; spec WP-3.1f Keputusan Lead #5, #7, #13; plan 3.1f1 Task 6) on
real PostgreSQL, with rows written by the real ledger writer. The scope matrix is in
``tests/security/test_usage_scope.py``."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.money import MONEY_PATTERN
from app.llm.base import Usage
from tests.integration.test_dashboard_api import add_project, add_task
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    owner,
    owner_id,
    workspace_root,
)
from tests.usage_fakes import HAIKU, record

USAGE = "/api/v1/usage"
SONNET = "claude-sonnet-5-5"


async def report(client: httpx.AsyncClient, **params: object) -> dict[str, Any]:
    response = await client.get(USAGE, params={k: str(v) for k, v in params.items()})
    assert response.status_code == 200, response.text
    return response.json()


def iso(moment: datetime) -> str:
    return moment.isoformat()


async def test_task_usage_report(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """TEST_STRATEGY §5 AC-16: estimated, partial and unavailable rows add up to a partial total."""
    project = await add_project(engine, owner_id, "Kalkulator")
    task = await add_task(engine, project, owner_id, "draft", "Hitung")
    other = await add_task(engine, project, owner_id, "draft", "Lain")
    await record(engine, project, task_id=task, agent_id="A02")  # 100 x 0.10 + 20 x 0.50 per million
    await record(
        engine, project, task_id=task, status="error", error_kind="transient", usage=Usage(50, 0)
    )  # partial: 50 x 0.10 per million
    await record(engine, project, task_id=task, model="claude-unpriced", usage=Usage(10, 5))  # unavailable
    await record(engine, project, task_id=other, usage=Usage(9_999, 9_999))  # another task: not counted
    body = await report(owner, task_id=task)
    totals = body["data"]["totals"]
    assert totals == {
        "input_tokens": 160,
        "output_tokens": 25,
        "estimated_cost": "0.000025",  # the known sum, a lower bound
        "actual_cost": None,
        "cost_status": "partial",
    }
    records = body["data"]["records"]
    assert len(records) == 3
    assert {r["cost_status"] for r in records} == {"estimated", "partial", "unavailable"}
    for item in records:
        assert item["task_id"] == str(task) and item["project_id"] == str(project)
        assert item["actual_cost"] is None
        assert (item["attempt"], item["retry_count"], item["request_id"], item["duration_ms"]) == (
            1,
            0,
            "req_usage",
            7,
        )
        assert {
            "call_id",
            "status",
            "error_kind",
            "agent_id",
            "agent_run_id",
            "pricing_version",
            "created_at",
        } <= set(item)
    failed = next(r for r in records if r["cost_status"] == "partial")
    assert (failed["status"], failed["error_kind"], failed["estimated_cost"]) == (
        "error",
        "transient",
        "0.000005",
    )
    assert body["meta"]["total"] == 3


async def test_usage_series_by_day_model_and_agent(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await add_project(engine, owner_id, "Seri")
    day = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=3)
    await record(engine, project, agent_id="A02", at=day)
    await record(engine, project, agent_id="B14", model=SONNET, at=day + timedelta(days=1))
    await record(engine, project, at=day + timedelta(days=1, hours=1))
    window = {"project_id": project, "from": iso(day - timedelta(days=1)), "to": iso(day + timedelta(days=2))}

    by_day = (await report(owner, **window))["data"]["series"]
    assert [point["key"] for point in by_day] == [
        day.strftime("%Y-%m-%d"),
        (day + timedelta(days=1)).strftime("%Y-%m-%d"),
    ]
    assert [point["input_tokens"] for point in by_day] == [100, 200]

    by_model = {p["key"]: p for p in (await report(owner, group_by="model", **window))["data"]["series"]}
    assert set(by_model) == {HAIKU, SONNET}
    assert by_model[HAIKU]["estimated_cost"] == "0.00004"  # two rows of 0.00002
    assert by_model[SONNET]["estimated_cost"] == "0.0004"  # 100 x 2 + 20 x 10 per million
    assert by_model[SONNET]["cost_status"] == "estimated"

    by_agent = {p["key"]: p for p in (await report(owner, group_by="agent", **window))["data"]["series"]}
    assert set(by_agent) == {"A02", "B14", None}  # a row without an agent keeps its own group
    assert by_agent[None]["input_tokens"] == 100
    meta = (await report(owner, group_by="agent", **window))["meta"]
    assert meta["group_by"] == "agent"
    assert datetime.fromisoformat(meta["from"]) == day - timedelta(days=1)


async def test_usage_range_is_half_open_and_defaults_to_30_days(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await add_project(engine, owner_id, "Rentang")
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 9, 2, tzinfo=UTC)
    await record(engine, project, usage=Usage(1, 0), at=start)  # in: [from
    await record(engine, project, usage=Usage(2, 0), at=end - timedelta(microseconds=1))  # in
    await record(engine, project, usage=Usage(4, 0), at=end)  # out: to)
    fixed = await report(owner, project_id=project, **{"from": iso(start), "to": iso(end)})
    assert fixed["data"]["totals"]["input_tokens"] == 3

    other = await add_project(engine, owner_id, "Bawaan")
    now = datetime.now(UTC)
    await record(engine, other, usage=Usage(8, 0), at=now - timedelta(days=29))
    await record(engine, other, usage=Usage(16, 0), at=now - timedelta(days=31))
    default = await report(owner, project_id=other)
    assert default["data"]["totals"]["input_tokens"] == 8
    window = datetime.fromisoformat(default["meta"]["to"]) - datetime.fromisoformat(default["meta"]["from"])
    assert window == timedelta(days=30)


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"from": "2026-09-02T00:00:00+00:00", "to": "2026-09-02T00:00:00+00:00"}, "from"),
        ({"from": "2026-09-03T00:00:00+00:00", "to": "2026-09-02T00:00:00+00:00"}, "from"),
        ({"from": "2025-08-30T00:00:00+00:00", "to": "2026-09-01T00:00:00+00:00"}, "from"),  # 367 days
        ({"from": "2026-09-01T00:00:00", "to": "2026-09-02T00:00:00+00:00"}, "from"),  # no time zone
        ({"to": "2026-09-02T00:00:00"}, "to"),
    ],
)
async def test_usage_range_limits(
    owner: httpx.AsyncClient,  # noqa: F811
    params: dict[str, str],
    field: str,
) -> None:
    response = await owner.get(USAGE, params=params)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["loc"] == ["query", field]


async def test_usage_range_of_366_days_is_allowed(owner: httpx.AsyncClient) -> None:  # noqa: F811
    await report(owner, **{"from": "2025-09-01T00:00:00+00:00", "to": "2026-09-02T00:00:00+00:00"})


async def test_usage_records_are_paginated(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await add_project(engine, owner_id, "Halaman")
    now = datetime.now(UTC)
    ids = [await record(engine, project, at=now - timedelta(hours=hours)) for hours in (3, 2, 1)]
    first = await report(owner, project_id=project, page_size=2)
    assert [r["id"] for r in first["data"]["records"]] == [str(ids[2]), str(ids[1])]  # newest first
    assert (first["meta"]["total"], first["meta"]["page"], first["meta"]["page_size"]) == (3, 1, 2)
    second = await report(owner, project_id=project, page_size=2, page=2)
    assert [r["id"] for r in second["data"]["records"]] == [str(ids[0])]
    assert second["data"]["totals"]["input_tokens"] == 300  # totals ignore the page
    for bad in ({"page_size": 201}, {"page_size": 0}, {"page": 0}):
        assert (await owner.get(USAGE, params=bad)).status_code == 422


async def test_money_is_a_plain_decimal_string(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    tiny = await add_project(engine, owner_id, "Kecil")
    await record(engine, tiny, usage=Usage(0, 1))  # 1 x 0.50 per million
    body = await report(owner, project_id=tiny)
    assert body["data"]["totals"]["estimated_cost"] == "0.0000005"
    assert body["data"]["records"][0]["estimated_cost"] == "0.0000005"
    zero = await add_project(engine, owner_id, "Nol")
    await record(engine, zero, usage=Usage(0, 0))
    assert (await report(owner, project_id=zero))["data"]["totals"]["estimated_cost"] == "0"
    everything = await report(owner, group_by="model")
    costs = [everything["data"]["totals"]["estimated_cost"]]
    costs += [point["estimated_cost"] for point in everything["data"]["series"]]
    costs += [item["estimated_cost"] for item in everything["data"]["records"]]
    assert costs and all(isinstance(cost, str) and re.fullmatch(MONEY_PATTERN, cost) for cost in costs)


async def test_usage_without_rows_is_estimated_zero(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await add_project(engine, owner_id, "Kosong")
    body = await report(owner, project_id=project)
    assert body["data"] == {
        "totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": "0",
            "actual_cost": None,
            "cost_status": "estimated",
        },
        "series": [],
        "records": [],
    }
    assert body["meta"]["total"] == 0


async def test_usage_all_unavailable_has_null_cost(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project = await add_project(engine, owner_id, "Tak Tersedia")
    await record(engine, project, status="error", error_kind="rate_limited", usage=None)
    await record(engine, project, model="claude-unpriced")
    totals = (await report(owner, project_id=project))["data"]["totals"]
    assert (totals["cost_status"], totals["estimated_cost"]) == ("unavailable", None)  # never "0"
    assert totals["input_tokens"] == 100  # the tokens that are known


async def test_usage_tokens_are_unknown_when_no_row_reported_them(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """Silent-failure M1: attempts that never reported usage are not zero tokens; the sum is unknown."""
    project = await add_project(engine, owner_id, "Tanpa Token")
    await record(engine, project, status="error", error_kind="rate_limited", usage=None)
    await record(engine, project, status="cancelled", error_kind="cancelled", usage=None)
    body = await report(owner, project_id=project, group_by="model")
    unknown = {
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost": None,
        "cost_status": "unavailable",
    }
    assert body["data"]["totals"] == {**unknown, "actual_cost": None}
    assert body["data"]["series"] == [{"key": HAIKU, **unknown}]


async def test_usage_without_a_filter_adds_up_the_visible_projects(
    owner: httpx.AsyncClient,  # noqa: F811
    owner_id: uuid.UUID,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    first = await add_project(engine, owner_id, "Satu")
    second = await add_project(engine, owner_id, "Dua")
    await record(engine, first)
    await record(engine, second)
    body = await report(owner)
    assert body["data"]["totals"]["input_tokens"] == 200
    assert {r["project_id"] for r in body["data"]["records"]} == {str(first), str(second)}


async def test_usage_needs_a_session(app: FastAPI) -> None:  # noqa: F811
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as anonymous:
        response = await anonymous.get(USAGE)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
