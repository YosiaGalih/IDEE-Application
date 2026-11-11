"""``GET/PATCH /projects/{project_id}/budget`` (API_CONTRACTS §9, §11.3; spec WP-3.1f Keputusan Lead #2).
Thin.

The project door answers first (404 out of scope, 403 without the permission); the service authorises again
and locks the row for a change. Money is a decimal string in and out: a JSON number is accepted only when it
fits six decimal places exactly, so a float that lost precision is refused, never rounded.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.api.routes.projects import EnvDep, ProjectReader, ProjectWriter, _respond
from app.core.money import Money
from app.services import project_budget
from app.services.project_budget import BudgetChanges, ProjectBudgetView
from app.workflow.budget import (
    BUDGET_MAX_COST_USD,
    BUDGET_MAX_DURATION_MINUTES,
    BUDGET_MAX_TOKENS,
    BUDGET_MAX_TOOL_ITERATIONS,
    BudgetDefaults,
    BudgetFields,
)

router = APIRouter(prefix="/projects", tags=["budget"])

Cost = Annotated[
    Decimal, Field(ge=0, le=BUDGET_MAX_COST_USD, max_digits=10, decimal_places=6, allow_inf_nan=False)
]


class BudgetPatchIn(BaseModel):
    """Only the keys sent change; a key sent as null goes back to inheriting the env default."""

    model_config = ConfigDict(extra="forbid")

    max_cost_usd: Cost | None = None
    max_tokens: Annotated[StrictInt, Field(ge=1, le=BUDGET_MAX_TOKENS)] | None = None
    max_tool_iterations: Annotated[StrictInt, Field(ge=0, le=BUDGET_MAX_TOOL_ITERATIONS)] | None = None
    max_duration_minutes: Annotated[StrictInt, Field(ge=1, le=BUDGET_MAX_DURATION_MINUTES)] | None = None


class EffectiveBudgetOut(BaseModel):
    max_cost_usd: Money
    max_tokens: int
    max_tool_iterations: int | None  # null: no task cap on tool iterations
    max_duration_minutes: int


class BudgetOut(BaseModel):
    max_cost_usd: Money | None
    max_tokens: int | None
    max_tool_iterations: int | None
    max_duration_minutes: int | None
    effective: EffectiveBudgetOut  # additive: the values in force after the env defaults

    @classmethod
    def of(cls, view: ProjectBudgetView) -> BudgetOut:
        return cls(
            max_cost_usd=view.stored.max_cost_usd,
            max_tokens=view.stored.max_tokens,
            max_tool_iterations=view.stored.max_tool_iterations,
            max_duration_minutes=view.stored.max_duration_minutes,
            effective=EffectiveBudgetOut(
                max_cost_usd=view.effective.max_cost_usd,
                max_tokens=view.effective.max_tokens,
                max_tool_iterations=view.effective.max_tool_iterations,
                max_duration_minutes=view.effective.max_duration_minutes,
            ),
        )


def _defaults(request: Request) -> BudgetDefaults:
    return BudgetDefaults.from_settings(request.app.state.settings)


@router.get("/{project_id}/budget")
async def get_budget(
    request: Request, project_id: uuid.UUID, actor: ProjectReader, env: EnvDep
) -> JSONResponse:
    view = await project_budget.get_budget(env, actor, project_id, _defaults(request))
    return _respond(request, BudgetOut.of(view).model_dump(mode="json"))


@router.patch("/{project_id}/budget")
async def update_budget(
    request: Request, project_id: uuid.UUID, body: BudgetPatchIn, actor: ProjectWriter, env: EnvDep
) -> JSONResponse:
    changes = BudgetChanges(
        fields=frozenset(body.model_fields_set),
        values=BudgetFields(
            max_cost_usd=body.max_cost_usd,
            max_tokens=body.max_tokens,
            max_tool_iterations=body.max_tool_iterations,
            max_duration_minutes=body.max_duration_minutes,
        ),
    )
    view = await project_budget.update_budget(env, actor, project_id, changes, _defaults(request))
    return _respond(request, BudgetOut.of(view).model_dump(mode="json"))
