"""``/approvals`` and ``DELETE /projects/{id}`` (API §8, §11 #6/#14/#15; spec WP-3.1c Goal 6)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.approvals import Approval
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    create,
    grant,
    headers,
    make_user,
    owner,
    owner_id,
    signed_in,
    workspace_root,
)

APPROVALS = "/api/v1/approvals"
PASSWORD = "user-long-passphrase"  # make_user's


async def project(client: httpx.AsyncClient, name: str = "Doomed") -> str:
    response = await create(client, name)
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def request_delete(client: httpx.AsyncClient, project_id: str) -> dict[str, object]:
    response = await client.delete(f"{PROJECTS}/{project_id}", headers=headers(client))
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    return body["data"]


async def decide(
    client: httpx.AsyncClient, approval: dict[str, object], decision: str = "approve", **body: object
) -> httpx.Response:
    payload = {"decision": decision, "payload_hash": approval["payload_hash"], **body}
    return await client.post(f"{APPROVALS}/{approval['id']}/decision", json=payload, headers=headers(client))


async def set_status(engine: AsyncEngine, approval_id: object, **values: object) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            update(Approval).where(Approval.id == uuid.UUID(str(approval_id))).values(**values)
        )


async def test_delete_returns_202_with_approval_and_project(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await project(owner)
    data = await request_delete(owner, project_id)
    approval, shown = data["approval"], data["project"]
    assert isinstance(approval, dict) and isinstance(shown, dict)
    assert shown["status"] == "deleting_pending_approval"
    assert approval["action_type"] == "project_delete" and approval["status"] == "pending"
    assert approval["payload"] == {"project_id": project_id, "name": "Doomed", "previous_status": "active"}
    assert approval["can_decide"] is True  # the single owner may decide their own request (§11.1)
    assert approval["handler_status"] == "pending" and approval["consumed_at"] is None


async def test_approve_deletes_and_the_project_is_gone(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await project(owner)
    approval = (await request_delete(owner, project_id))["approval"]
    response = await decide(owner, approval)  # type: ignore[arg-type]
    assert response.status_code == 200, response.text
    decided = response.json()["data"]
    assert (decided["status"], decided["handler_status"]) == ("consumed", "succeeded")
    assert (await owner.get(f"{PROJECTS}/{project_id}")).status_code == 404
    listed = (await owner.get(PROJECTS, params={"status": "all"})).json()["data"]
    assert project_id not in {p["id"] for p in listed}


async def test_reject_restores(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await project(owner)
    approval = (await request_delete(owner, project_id))["approval"]
    response = await decide(owner, approval, "reject", note="keep it")  # type: ignore[arg-type]
    assert response.json()["data"]["status"] == "rejected"
    assert (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]["status"] == "active"


async def test_pending_list_and_history(owner: httpx.AsyncClient) -> None:  # noqa: F811
    first = await project(owner, "One")
    second = await project(owner, "Two")
    one = (await request_delete(owner, first))["approval"]
    await request_delete(owner, second)
    await decide(owner, one, "reject")  # type: ignore[arg-type]
    pending = (await owner.get(APPROVALS)).json()
    assert [a["payload"]["name"] for a in pending["data"]] == ["Two"] and pending["meta"]["total"] == 1
    history = (await owner.get(APPROVALS, params={"status": "all"})).json()["data"]
    assert {a["payload"]["name"] for a in history} == {"One", "Two"}
    rejected = (await owner.get(APPROVALS, params={"status": "rejected", "project_id": first})).json()["data"]
    assert [a["id"] for a in rejected] == [one["id"]]  # type: ignore[index]


async def test_superseded_filter(owner: httpx.AsyncClient) -> None:  # noqa: F811
    project_id = await project(owner)
    old = (await request_delete(owner, project_id))["approval"]
    renamed = await owner.patch(f"{PROJECTS}/{project_id}", json={"name": "Renamed"}, headers=headers(owner))
    assert renamed.status_code == 200, renamed.text
    superseded = (await owner.get(APPROVALS, params={"status": "superseded"})).json()["data"]
    assert [a["id"] for a in superseded] == [old["id"]]  # type: ignore[index]


@pytest.mark.parametrize("status", ["approved", "rejected", "consumed", "superseded", "expired"])
async def test_decision_reasons_are_409(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
    status: str,
) -> None:
    approval = (await request_delete(owner, await project(owner)))["approval"]
    await set_status(engine, approval["id"], status=status)  # type: ignore[index]
    response = await decide(owner, approval)  # type: ignore[arg-type]
    assert response.status_code == 409
    error = response.json()["error"]
    assert (error["code"], error["details"][0]["reason"]) == ("INVALID_TRANSITION", status)


async def test_get_out_of_scope_is_404(app: FastAPI, owner: httpx.AsyncClient, engine: AsyncEngine) -> None:  # noqa: F811
    approval = (await request_delete(owner, await project(owner)))["approval"]
    await make_user(engine, "stranger@example.test", ("owner", "global"))
    stranger = await signed_in(app, "stranger@example.test", PASSWORD)
    try:
        # a global owner role does not reach into another user's project (AC-01)
        assert (await stranger.get(f"{APPROVALS}/{approval['id']}")).status_code == 404  # type: ignore[index]
        assert (await decide(stranger, approval)).status_code == 404  # type: ignore[arg-type]
        assert (await stranger.get(APPROVALS, params={"status": "all"})).json()["data"] == []
    finally:
        await stranger.aclose()


async def test_developer_cannot_approve_delete(
    app: FastAPI,  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    approval = (await request_delete(owner, project_id))["approval"]
    dev = await make_user(engine, "dev@example.test")
    await grant(engine, dev, "developer", f"project:{project_id}")
    client = await signed_in(app, "dev@example.test", PASSWORD)
    try:
        assert (await client.get(APPROVALS)).json()["data"] == []  # nothing they can decide
        history = (await client.get(APPROVALS, params={"status": "all"})).json()["data"]
        assert [a["can_decide"] for a in history] == [False]
        response = await decide(client, approval)  # type: ignore[arg-type]
        assert response.status_code == 403
    finally:
        await client.aclose()
    assert (await owner.get(f"{APPROVALS}/{approval['id']}")).json()["data"]["status"] == "pending"  # type: ignore[index]


async def test_concurrent_decisions_one_wins(owner: httpx.AsyncClient) -> None:  # noqa: F811
    approval = (await request_delete(owner, await project(owner)))["approval"]
    responses = await asyncio.gather(
        decide(owner, approval),
        decide(owner, approval, "reject"),  # type: ignore[arg-type]
    )
    assert sorted(r.status_code for r in responses) == [200, 409]


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "approve", "payload_hash": "a" * 64, "note": "x" * 2001},
        {"decision": "maybe", "payload_hash": "a" * 64},
        {"decision": "approve", "payload_hash": "A" * 64},
        {"decision": "approve", "payload_hash": "a" * 64, "status": "approved"},
        {"decision": "approve", "payload_hash": "a" * 64, "note": "nul\u0000here"},
        {"decision": "approve", "payload_hash": "a" * 64, "note": "bidi \u202e txet"},
        {"decision": "approve", "payload_hash": "a" * 64, "note": "line\u2028separator"},
    ],
)
async def test_bad_decision_bodies_are_422(owner: httpx.AsyncClient, body: dict[str, object]) -> None:  # noqa: F811
    approval = (await request_delete(owner, await project(owner)))["approval"]
    response = await owner.post(f"{APPROVALS}/{approval['id']}/decision", json=body, headers=headers(owner))  # type: ignore[index]
    assert response.status_code == 422


async def test_delete_of_a_project_awaiting_deletion_returns_the_same_approval(
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    project_id = await project(owner)
    first = (await request_delete(owner, project_id))["approval"]
    second = (await request_delete(owner, project_id))["approval"]
    assert first["id"] == second["id"]  # type: ignore[index]


async def test_expired_pending_is_not_decidable(owner: httpx.AsyncClient, engine: AsyncEngine) -> None:  # noqa: F811
    approval = (await request_delete(owner, await project(owner)))["approval"]
    await set_status(engine, approval["id"], expires_at=datetime.now(UTC) - timedelta(minutes=1))  # type: ignore[index]
    assert (await owner.get(APPROVALS)).json()["data"] == []
    shown = (await owner.get(f"{APPROVALS}/{approval['id']}")).json()["data"]  # type: ignore[index]
    assert shown["can_decide"] is False


async def test_a_note_keeps_line_breaks(owner: httpx.AsyncClient) -> None:  # noqa: F811
    approval = (await request_delete(owner, await project(owner)))["approval"]
    response = await decide(owner, approval, "reject", note="line one\nline two\tend")  # type: ignore[arg-type]
    assert response.status_code == 200, response.text
    assert response.json()["data"]["decision_note"] == "line one\nline two\tend"


async def test_rename_racing_a_decision_never_deletes_the_renamed_project(
    owner: httpx.AsyncClient,  # noqa: F811
) -> None:
    # AG-13 LOW-7a: whichever wins, the old name is never what gets deleted under a new name.
    project_id = await project(owner, "Before")
    approval = (await request_delete(owner, project_id))["approval"]
    rename = owner.patch(f"{PROJECTS}/{project_id}", json={"name": "After"}, headers=headers(owner))
    decision = decide(owner, approval)  # type: ignore[arg-type]
    renamed, decided = await asyncio.gather(rename, decision)
    if decided.status_code == 200 and decided.json()["data"]["status"] == "consumed":
        assert renamed.status_code == 404  # deleted first: the rename found nothing
    else:
        assert renamed.status_code == 200
        pending = (await owner.get(APPROVALS)).json()["data"]
        assert [a["payload"]["name"] for a in pending] == ["After"]
        shown = (await owner.get(f"{PROJECTS}/{project_id}")).json()["data"]
        assert shown["status"] == "deleting_pending_approval"


async def waiting_on_a_lock(engine: AsyncEngine, timeout_s: float = 5.0) -> bool:
    """True once another backend of this database waits on a lock (AG-13 LOW-D: proof, not a sleep)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        async with engine.connect() as probe:
            waiting = await probe.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                )
            )
        if waiting:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_a_decision_waits_for_a_concurrent_change(
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    # Deterministic form of the race: another transaction holds the row and settles it; the decision must
    # wait for it and then see the new state, never overwrite it (Review Focus 3).
    approval = (await request_delete(owner, await project(owner)))["approval"]
    approval_id = uuid.UUID(str(approval["id"]))  # type: ignore[index]
    async with engine.connect() as conn:
        transaction = await conn.begin()
        await conn.execute(select(Approval.id).where(Approval.id == approval_id).with_for_update())
        decision = asyncio.create_task(decide(owner, approval))  # type: ignore[arg-type]
        assert await waiting_on_a_lock(engine), "the decision never waited on the row lock"
        assert not decision.done()
        await conn.execute(update(Approval).where(Approval.id == approval_id).values(status="rejected"))
        await transaction.commit()
    response = await decision
    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["reason"] == "rejected"


async def test_approvals_need_a_session(app: FastAPI, owner: httpx.AsyncClient) -> None:  # noqa: F811
    approval = (await request_delete(owner, await project(owner)))["approval"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as anonymous:
        assert (await anonymous.get(APPROVALS)).status_code == 401
        assert (await anonymous.get(f"{APPROVALS}/{approval['id']}")).status_code == 401  # type: ignore[index]


async def test_a_decision_needs_the_csrf_token(owner: httpx.AsyncClient) -> None:  # noqa: F811
    approval = (await request_delete(owner, await project(owner)))["approval"]
    body = {"decision": "approve", "payload_hash": approval["payload_hash"]}  # type: ignore[index]
    response = await owner.post(f"{APPROVALS}/{approval['id']}/decision", json=body)  # type: ignore[index]
    assert response.status_code == 403
    shown = (await owner.get(f"{APPROVALS}/{approval['id']}")).json()["data"]  # type: ignore[index]
    assert shown["status"] == "pending"


async def test_approvals_are_paged(owner: httpx.AsyncClient) -> None:  # noqa: F811
    for name in ("One", "Two", "Three"):
        await request_delete(owner, await project(owner, name))
    first = (await owner.get(APPROVALS, params={"page_size": 2})).json()
    second = (await owner.get(APPROVALS, params={"page_size": 2, "page": 2})).json()
    assert (len(first["data"]), len(second["data"])) == (2, 1)
    assert first["meta"]["total"] == second["meta"]["total"] == 3
    history = (await owner.get(APPROVALS, params={"status": "all", "page_size": 2, "page": 2})).json()
    assert (len(history["data"]), history["meta"]["total"]) == (1, 3)
    assert (await owner.get(APPROVALS, params={"page_size": 201})).status_code == 422
    assert (await owner.get(APPROVALS, params={"page": 0})).status_code == 422
