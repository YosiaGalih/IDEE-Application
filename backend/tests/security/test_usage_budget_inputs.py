"""Inputs that once answered 500 on the usage and budget endpoints (AG-13 WP-3.1f1 L2, L3). Regression tests
for security findings live here (.claude/rules/backend.md).

- L2: a ``from``/``to`` at the edge of time overflowed in ``_window``: now a 422 that names the field, decided
  before the scope.
- L3: a malformed stored ``projects.settings.budget`` (only a direct DB edit makes one) broke GET and PATCH
  ``/projects/{id}/budget``: now a 409 ``budget_invalid``; only a PATCH with all four keys replaces it, and
  the audit never echoes the malformed value.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.test_project_budget import (
    budget_audits,
    budget_url,
    new_project,
    patch,
    stored_settings,
)
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    owner,
    owner_id,
    workspace_root,
)
from tests.integration.test_usage_api import USAGE

# --- L2: from/to at the edge of time -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"to": "0001-01-01T00:00:00Z"}, "to"),  # the default 30 days would start before year 1
        ({"to": "0001-01-20T00:00:00Z"}, "to"),
        ({"from": "0001-01-01T00:30:00+01:00"}, "from"),  # before year 1 once in UTC
        ({"to": "9999-12-31T23:59:59-01:00"}, "to"),  # after year 9999 once in UTC
    ],
)
async def test_usage_range_at_the_edge_of_time_is_a_422(
    owner: httpx.AsyncClient,  # noqa: F811
    params: dict[str, str],
    field: str,
) -> None:
    """AG-13 L2: these overflowed in _window and answered 500. A 422 that names the field, decided before
    the scope: the project here does not exist, and still the answer is about the range."""
    response = await owner.get(USAGE, params={**params, "project_id": str(uuid.uuid4())})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["loc"] == ["query", field]


# --- L3: a malformed stored budget (only a direct DB edit can make one) -----------------------------------

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
