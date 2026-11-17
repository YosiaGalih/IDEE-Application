"""Conversations and chat messages over HTTP with real PostgreSQL (API_CONTRACTS §5; spec WP-3.2 §8).

A message may create a ``draft`` task in the same transaction; the workflow-engine gate is closed until
WP-3.2b, so most task-creating tests open it."""

# ruff: noqa: F811 - fixtures imported from the shared test modules are used as arguments
from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.projects import Conversation, Message
from app.models.tasks import Task
from app.services import conversations as conversations_service
from app.services.tasks import PROMPT_MAX, TaskView
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    grant,
    headers,
    make_user,
    owner,
    owner_id,
    signed_in,
    workspace_root,
)
from tests.integration.test_tasks_api import created_audits, open_gate, project, sql  # noqa: F401


async def new_conversation(client: httpx.AsyncClient, project_id: str, **body: object) -> str:
    response = await client.post(f"{PROJECTS}/{project_id}/conversations", json=body, headers=headers(client))
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def post_message(
    client: httpx.AsyncClient, conversation_id: str, *, key: str | None = None, **body: object
) -> httpx.Response:
    payload = {"content": "Tambah fitur login", **body}
    sent = headers(client)
    if key is not None:
        sent["Idempotency-Key"] = key
    return await client.post(f"/api/v1/conversations/{conversation_id}/messages", json=payload, headers=sent)


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608


async def test_conversations_belong_to_their_creator(
    app,
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id, title="Obrolan saya")
    other = await make_user(engine, "dev@example.test")
    await grant(engine, other, "developer", f"project:{project_id}")
    dev = await signed_in(app, "dev@example.test", "user-long-passphrase")
    try:
        listed = await dev.get(f"{PROJECTS}/{project_id}/conversations")
        assert listed.status_code == 200 and listed.json()["data"] == []
        assert listed.json()["meta"]["total"] == 0
        stranger = await dev.get(f"/api/v1/conversations/{cid}/messages")
        missing = await dev.get(f"/api/v1/conversations/{uuid.uuid4()}/messages")
        assert stranger.status_code == 404
        assert (stranger.status_code, stranger.json()["error"]) == (404, missing.json()["error"])
        posted = await post_message(dev, cid)
        assert posted.status_code == 404
    finally:
        await dev.aclose()
    mine = await owner.get(f"{PROJECTS}/{project_id}/conversations")
    assert [c["title"] for c in mine.json()["data"]] == ["Obrolan saya"]


async def test_conversation_title_is_redacted_and_defaults_empty(
    owner: httpx.AsyncClient,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    secret = "sk-ant-" + "api03-" + "x" * 40
    response = await owner.post(
        f"{PROJECTS}/{project_id}/conversations", json={"title": secret}, headers=headers(owner)
    )
    assert response.status_code == 201 and secret not in response.text
    blank = await owner.post(f"{PROJECTS}/{project_id}/conversations", json={}, headers=headers(owner))
    assert blank.json()["data"]["title"] == ""
    for bad in ("x" * 201, "a\nb"):
        refused = await owner.post(
            f"{PROJECTS}/{project_id}/conversations", json={"title": bad}, headers=headers(owner)
        )
        assert refused.status_code == 422, refused.text
    async with make_sessionmaker(engine)() as session:
        assert secret not in "".join(await session.scalars(select(Conversation.title)))


async def test_message_without_task_is_201(owner: httpx.AsyncClient, engine: AsyncEngine) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    secret = "sk-ant-" + "api03-" + "x" * 40
    response = await post_message(owner, cid, content=f"{secret}\r\nhalo", create_task=False)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed" and body["data"]["task"] is None
    message = body["data"]["message"]
    assert message["role"] == "user" and message["task_id"] is None and message["agent_id"] is None
    assert secret not in response.text and message["content"].endswith("\nhalo")
    assert await count(engine, "tasks") == 0 and await count(engine, "messages") == 1


async def test_message_with_task_is_202_and_linked(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    secret = "sk-ant-" + "api03-" + "x" * 40
    async with make_sessionmaker(engine)() as session:
        before = await session.scalar(
            select(Conversation.updated_at).where(Conversation.id == uuid.UUID(cid))
        )
    response = await post_message(
        owner,
        cid,
        content=f"Perbaiki {secret}",
        context_refs=[{"type": "file", "path": "README.md"}],
        workflow_type="debug",
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    message, task_out = body["data"]["message"], body["data"]["task"]
    assert task_out["status"] == "draft" and task_out["workflow_type"] == "debug"
    assert task_out["conversation_id"] == cid and message["task_id"] == task_out["id"]
    async with make_sessionmaker(engine)() as session:
        (task,) = await session.scalars(select(Task))
        (row,) = await session.scalars(select(Message))
        conversation = await session.get(Conversation, uuid.UUID(cid))
        # This task's row only: audit_events is append-only, other tests' rows may be there (AG-13 LOW).
        audit = await session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "task.created", AuditEvent.resource_id == str(task.id)
            )
        )
        events = list(audit)
    assert row.task_id == task.id and task.conversation_id == uuid.UUID(cid)
    assert secret not in task.prompt and task.prompt == row.content
    assert task.context_refs == row.context_refs and len(task.context_refs) == 1
    assert [e.metadata_ for e in events] == [
        {"task_id": str(task.id), "source": "chat", "workflow_type": "debug"}
    ]
    assert conversation is not None and before is not None and conversation.updated_at > before


async def test_gate_closed_writes_nothing(owner: httpx.AsyncClient, engine: AsyncEngine) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    key = str(uuid.uuid4())
    for _ in range(2):
        response = await post_message(owner, cid, key=key)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["details"] == [{"reason": "workflow_engine_unavailable"}]
    assert await count(engine, "messages") == 0 and await count(engine, "tasks") == 0
    assert await created_audits(engine, project_id) == []


async def test_message_and_task_are_one_transaction(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    response = await post_message(owner, cid, budget={"max_tokens": -1})
    assert response.status_code == 422, response.text
    assert await count(engine, "messages") == 0 and await count(engine, "tasks") == 0
    async with make_sessionmaker(engine)() as session:
        assert (
            list(
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.action == "task.created", AuditEvent.project_id == uuid.UUID(project_id)
                    )
                )
            )
            == []
        )


async def test_messages_page_in_order(owner: httpx.AsyncClient) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    for word in ("satu", "dua", "tiga"):
        assert (await post_message(owner, cid, content=word, create_task=False)).status_code == 201
    first = await owner.get(f"/api/v1/conversations/{cid}/messages", params={"page_size": 2})
    assert [m["content"] for m in first.json()["data"]] == ["satu", "dua"]
    assert first.json()["meta"] == {"total": 3, "page": 1, "page_size": 2}
    second = await owner.get(f"/api/v1/conversations/{cid}/messages", params={"page_size": 2, "page": 2})
    assert [m["content"] for m in second.json()["data"]] == ["tiga"]


async def test_post_needs_task_create(
    app,
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    viewer = await make_user(engine, "rui@example.test")
    await grant(engine, viewer, "readonly", f"project:{project_id}")
    client = await signed_in(app, "rui@example.test", "user-long-passphrase")
    try:
        denied = await client.post(f"{PROJECTS}/{project_id}/conversations", json={}, headers=headers(client))
        assert denied.status_code == 403, denied.text
        assert (await client.get(f"{PROJECTS}/{project_id}/conversations")).status_code == 200
        # Someone else's conversation answers 404, not 403.
        assert (await post_message(client, cid)).status_code == 404
    finally:
        await client.aclose()


async def test_message_without_task_needs_an_active_project_but_not_a_ready_repository(
    owner: httpx.AsyncClient,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    await sql(
        engine,
        "UPDATE repositories SET sync_state = 'error' WHERE project_id = :id",
        id=uuid.UUID(project_id),
    )
    assert (await post_message(owner, cid, create_task=False)).status_code == 201
    await sql(engine, "UPDATE projects SET status = 'archived' WHERE id = :id", id=uuid.UUID(project_id))
    response = await post_message(owner, cid, create_task=False)
    assert response.status_code == 409 and response.json()["error"]["details"] == [
        {"reason": "project_archived"}
    ]


# --- final review wave (WP-3.2a F1, F2, F4, F5, F6) ---------------------------------------------------------


async def test_a_failed_view_stores_no_message_and_the_retry_stores_one(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BE M1 / SF M1: views are built before the commit; a failure there stores nothing and the retry with the
    same key (released on 5xx) stores one message and one task."""
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    real = conversations_service.task_view
    calls: list[int] = []

    async def fails_once(*args: Any, **kwargs: Any) -> TaskView:
        calls.append(1)
        if len(calls) == 1:
            raise AppError("INTERNAL_ERROR", "Internal error", http_status=500)
        return await real(*args, **kwargs)

    monkeypatch.setattr(conversations_service, "task_view", fails_once)
    key = str(uuid.uuid4())
    failed = await post_message(owner, cid, key=key)
    assert failed.status_code == 500, failed.text
    assert await count(engine, "messages") == 0 and await count(engine, "tasks") == 0
    assert await created_audits(engine, project_id) == []
    retried = await post_message(owner, cid, key=key)
    assert retried.status_code == 202, retried.text
    assert await count(engine, "messages") == 1 and await count(engine, "tasks") == 1
    assert len(await created_audits(engine, project_id)) == 1


async def test_path_refs_in_chat_need_a_ready_repository(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    """Ruling R5: without a task, a file or folder ref still needs the repository ``ready`` (409, never a 500
    for a root an import has not written yet); a message without path refs does not."""
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    task_id = (await post_message(owner, cid)).json()["data"]["task"]["id"]
    await sql(
        engine,
        "UPDATE repositories SET sync_state = 'importing' WHERE project_id = :id",
        id=uuid.UUID(project_id),
    )
    for ref in ({"type": "file", "path": "README.md"}, {"type": "folder", "path": "src"}):
        refused = await post_message(owner, cid, create_task=False, context_refs=[ref])
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["details"] == [{"reason": "repository_not_ready"}]
    assert (await post_message(owner, cid, create_task=False)).status_code == 201
    task_ref = await post_message(
        owner, cid, create_task=False, context_refs=[{"type": "task", "id": task_id}]
    )
    assert task_ref.status_code == 201, task_ref.text
    assert await count(engine, "messages") == 3


@pytest.mark.parametrize("field", ["workflow_type", "budget"])
async def test_task_fields_without_a_task_are_refused(
    owner: httpx.AsyncClient,
    engine: AsyncEngine,
    field: str,
) -> None:
    """Ruling R6: dropping a cost cap silently would fail open."""
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    value = {"workflow_type": "debug", "budget": {"max_tokens": 1000}}[field]
    response = await post_message(owner, cid, create_task=False, **{field: value})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["loc"] == ["body", field]
    assert error["details"][0]["reason"] == "only_with_task"
    assert await count(engine, "messages") == 0


@pytest.mark.parametrize(
    ("status", "reason"),
    [("archived", "project_archived"), ("deleting_pending_approval", "project_not_active")],
)
async def test_a_conversation_needs_an_active_project(
    owner: httpx.AsyncClient,
    engine: AsyncEngine,
    status: str,
    reason: str,
) -> None:
    """Ruling R8: like a chat message (R1), a new conversation is a write."""
    project_id = await project(owner)
    await sql(engine, "UPDATE projects SET status = :s WHERE id = :id", s=status, id=uuid.UUID(project_id))
    response = await owner.post(f"{PROJECTS}/{project_id}/conversations", json={}, headers=headers(owner))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == [{"reason": reason}]
    assert await count(engine, "conversations") == 0


async def test_message_input_is_bounded(
    owner: httpx.AsyncClient, open_gate: None, engine: AsyncEngine
) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    twenty_one = [{"type": "file", "path": f"f{n}.txt"} for n in range(21)]
    over = await post_message(owner, cid, context_refs=twenty_one)
    assert over.status_code == 422 and over.json()["error"]["details"][0]["loc"][:2] == [
        "body",
        "context_refs",
    ]
    for content in ("   ", "\r\n\t", "x" * (PROMPT_MAX + 1), "a\x00b", "a‮b"):
        refused = await post_message(owner, cid, content=content, create_task=False)
        assert refused.status_code == 422, (content[:8], refused.text)
        assert refused.json()["error"]["details"][0]["loc"] == ["body", "content"]
    padded = "x" + " " * (2 * PROMPT_MAX - 1)
    assert (await post_message(owner, cid, content=padded, create_task=False)).status_code == 201
    assert (await post_message(owner, cid, content=padded + " ", create_task=False)).status_code == 422
    missing = await post_message(owner, cid, context_refs=[{"type": "file", "path": "nope.py"}])
    assert missing.status_code == 422
    assert missing.json()["error"]["details"][0] == {
        "loc": ["body", "context_refs", 0],
        "msg": "The referenced item does not exist in this project",
        "reason": "context_ref_not_found",
    }
    assert await count(engine, "messages") == 1 and await count(engine, "tasks") == 0


async def test_an_ex_member_loses_their_own_conversation(
    app,
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    """A conversation is its creator's only while they can see the project: after their role is revoked it
    answers 404, the same as a random id; demoted to readonly they may read it but not post (403)."""
    project_id = await project(owner)
    dev_id = await make_user(engine, "dev@example.test")
    await grant(engine, dev_id, "developer", f"project:{project_id}")
    dev = await signed_in(app, "dev@example.test", "user-long-passphrase")
    try:
        cid = await new_conversation(dev, project_id)
        assert (await post_message(dev, cid, create_task=False)).status_code == 201
        await sql(engine, "DELETE FROM user_roles WHERE user_id = :id", id=dev_id)
        gone = await dev.get(f"/api/v1/conversations/{cid}/messages")
        random = await dev.get(f"/api/v1/conversations/{uuid.uuid4()}/messages")
        assert (gone.status_code, gone.json()["error"]) == (404, random.json()["error"])
        posted = await post_message(dev, cid, create_task=False)
        assert (posted.status_code, posted.json()["error"]["code"]) == (404, "NOT_FOUND")
        await grant(engine, dev_id, "readonly", f"project:{project_id}")
        assert (await dev.get(f"/api/v1/conversations/{cid}/messages")).status_code == 200
        demoted = await post_message(dev, cid, create_task=False)
        assert (demoted.status_code, demoted.json()["error"]["code"]) == (403, "FORBIDDEN")
    finally:
        await dev.aclose()
    assert await count(engine, "messages") == 1


async def test_idempotent_replay_returns_the_first_message_and_task(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    cid = await new_conversation(owner, project_id)
    key = str(uuid.uuid4())
    first = await post_message(owner, cid, key=key)
    again = await post_message(owner, cid, key=key)
    assert first.status_code == again.status_code == 202, again.text
    assert again.headers.get("idempotent-replay") is not None
    assert again.json()["data"] == first.json()["data"]
    assert await count(engine, "messages") == 1 and await count(engine, "tasks") == 1
