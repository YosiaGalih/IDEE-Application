"""A project's default budget for its tasks (API_CONTRACTS §9, §11.3; spec WP-3.1f Keputusan Lead #2, #13).

Stored in ``projects.settings["budget"]`` with all four keys, the cost as a decimal string (JSONB reads a
number back as a float), ``null`` meaning "inherit the env default". Only this module writes that key:
``PATCH /projects`` refuses it, and its settings merge keeps it. A change locks the project row like every
other project update, and is audited as ``project.budget_updated`` with what changed; a request that changes
nothing writes nothing. This is a default for each task, never a cumulative cap on the project.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from typing import Any, Final

from app.core.errors import AppError
from app.services import projects
from app.services.projects import Actor, ProjectEnv
from app.workflow.budget import (
    BUDGET_KEYS,
    BudgetDefaults,
    BudgetFields,
    EffectiveBudget,
    dump_budget,
    effective_budget,
    parse_budget,
)

BUDGET_SETTINGS_KEY: Final = "budget"
AUDIT_ACTION: Final = "project.budget_updated"


@dataclass(frozen=True, slots=True)
class ProjectBudgetView:
    project_id: uuid.UUID
    stored: BudgetFields
    effective: EffectiveBudget  # effective_budget(BudgetFields(), stored, defaults): what a new task gets


@dataclass(frozen=True, slots=True)
class BudgetChanges:
    fields: frozenset[str]  # keys present in the request (absent = unchanged; null = inherit)
    values: BudgetFields


def _view(project_id: uuid.UUID, settings: dict[str, Any], defaults: BudgetDefaults) -> ProjectBudgetView:
    stored = parse_budget(settings.get(BUDGET_SETTINGS_KEY))
    return ProjectBudgetView(project_id, stored, effective_budget(BudgetFields(), stored, defaults))


async def get_budget(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, defaults: BudgetDefaults
) -> ProjectBudgetView:
    async with env.sessions() as session:
        project, _ = await projects.authorize(session, actor, project_id, "project:read")
        settings = dict(project.settings)
    return _view(project_id, settings, defaults)


async def update_budget(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, changes: BudgetChanges, defaults: BudgetDefaults
) -> ProjectBudgetView:
    unknown = changes.fields - set(BUDGET_KEYS)
    if unknown:
        raise AppError(
            "VALIDATION_ERROR",
            "Input did not pass validation",
            http_status=422,
            details=[{"loc": ["body", sorted(unknown)[0]], "msg": "unknown budget field"}],
        )
    async with env.sessions() as session:
        project, _ = await projects.authorize(session, actor, project_id, "project:write", lock=True)
        current = parse_budget(project.settings.get(BUDGET_SETTINGS_KEY))
        updated = dataclasses.replace(
            current, **{name: getattr(changes.values, name) for name in changes.fields}
        )
        changed = [name for name in BUDGET_KEYS if getattr(updated, name) != getattr(current, name)]
        if changed:
            before, after = dump_budget(current), dump_budget(updated)
            project.settings = {**project.settings, BUDGET_SETTINGS_KEY: after}
            await projects.audit_project(
                env,
                session,
                actor,
                project,
                AUDIT_ACTION,
                {
                    "fields": changed,
                    "budget": {name: after[name] for name in changed},
                    "previous": {name: before[name] for name in changed},
                },
            )
        settings = dict(project.settings)
        await session.commit()
    return _view(project_id, settings, defaults)
