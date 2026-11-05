"""Approval routes (API_CONTRACTS §8, §11; FR-063, FR-067; spec WP-3.1c Goal 6). Thin.

Decisions come only from a signed-in person over HTTP with a session (CSRF-protected); the service checks
``approval:decide`` and the action's own permission on the approval's project.
"""

from __future__ import annotations

import unicodedata
import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.deps import CurrentActor
from app.api.routes.approval_out import ApprovalOut
from app.api.routes.projects import EnvDep, _respond
from app.models.approvals import NOTE_MAX
from app.services import approvals
from app.services.approval_registry import default_handlers
from app.services.approvals import Decision, ListStatus

router = APIRouter(prefix="/approvals", tags=["approvals"])
PAGE_SIZE_MAX = 200


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    payload_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    note: Annotated[str | None, Field(max_length=NOTE_MAX)] = None

    @field_validator("note")
    @classmethod
    def _plain_text(cls, note: str | None) -> str | None:
        # PostgreSQL text refuses NUL (a 500 otherwise); control and format characters (bidi) would reach
        # the screen that shows the note. Line breaks and tabs stay.
        if note is not None and any(
            unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\t" for c in note
        ):
            raise ValueError("note must be plain text")
        return note


@router.get("")
async def list_approvals(
    request: Request,
    actor: CurrentActor,
    env: EnvDep,
    status: ListStatus = "pending",
    project_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = 50,
) -> JSONResponse:
    views, total = await approvals.list_approvals(
        env, actor, status=status, project_id=project_id, page=page, page_size=page_size
    )
    return _respond(
        request,
        [ApprovalOut.of(view).model_dump(mode="json") for view in views],
        meta={"total": total, "page": page, "page_size": page_size},
    )


@router.get("/{approval_id}")
async def get_approval(
    request: Request, approval_id: uuid.UUID, actor: CurrentActor, env: EnvDep
) -> JSONResponse:
    view = await approvals.get_approval(env, actor, approval_id)
    return _respond(request, ApprovalOut.of(view).model_dump(mode="json"))


@router.post("/{approval_id}/decision")
async def decide(
    request: Request, approval_id: uuid.UUID, body: DecisionIn, actor: CurrentActor, env: EnvDep
) -> JSONResponse:
    view = await approvals.decide(
        env,
        default_handlers(sessions=env.sessions, audit=env.audit),
        actor,
        approval_id,
        decision=body.decision,
        payload_hash=body.payload_hash,
        note=body.note,
    )
    return _respond(request, ApprovalOut.of(view).model_dump(mode="json"))
