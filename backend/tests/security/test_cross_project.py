"""Cross-project access (AC-01, SECURITY_MODEL §8, T6): a project outside the user's scope does not exist.

Every endpoint with a project id is listed in ``ENDPOINTS``; a new one must be added here. Out of scope →
404 (never 403, which would confirm the id); in scope without the permission → 403.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from redis.asyncio import Redis
from sqlalchemy import func, text, update
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.routing import BaseRoute

from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.main import create_app
from app.models.projects import Project
from tests.conftest import make_test_env, reset_data
from tests.integration.test_projects import OWNER, create, grant, headers, make_user, signed_in

# (method, path template, json body) for every route under /projects/{project_id}. ``{path}`` stands for a
# file path inside the repository (the route's ``{path:path}``).
ENDPOINTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/api/v1/projects/{id}", None),
    ("PATCH", "/api/v1/projects/{id}", {"name": "Taken"}),
    ("POST", "/api/v1/projects/{id}/archive", None),
    ("POST", "/api/v1/projects/{id}/unarchive", None),
    ("GET", "/api/v1/projects/{id}/stack", None),
    ("POST", "/api/v1/projects/{id}/imports", None),  # the door answers before the body is read
    ("GET", "/api/v1/projects/{id}/tree", None),
    ("GET", "/api/v1/projects/{id}/files/{path}", None),
    ("PUT", "/api/v1/projects/{id}/files/{path}", {"content": "x\n", "expected_sha256": None}),
    ("POST", "/api/v1/projects/{id}/files", {"path": "new.txt", "type": "file"}),
    ("DELETE", "/api/v1/projects/{id}/files/{path}", None),  # the door answers before the query is checked
    ("POST", "/api/v1/projects/{id}/patches", {"diff": "x"}),
    ("GET", "/api/v1/projects/{id}/git/status", None),
    ("GET", "/api/v1/projects/{id}/git/diff", None),
    ("GET", "/api/v1/projects/{id}/git/branches", None),
    ("GET", "/api/v1/projects/{id}/git/log", None),
    ("POST", "/api/v1/projects/{id}/git/branch", {"name": "matrix"}),
    ("POST", "/api/v1/projects/{id}/git/commit", {"message": "matrix"}),
    ("POST", "/api/v1/projects/{id}/index", None),
    ("GET", "/api/v1/projects/{id}/search?q=x", None),
    ("GET", "/api/v1/projects/{id}/budget", None),
    ("PATCH", "/api/v1/projects/{id}/budget", {"max_tokens": 1000}),
    ("DELETE", "/api/v1/projects/{id}", None),  # R3: only asks for a project_delete approval
    ("GET", "/api/v1/projects/{id}/tasks", None),
    ("POST", "/api/v1/projects/{id}/tasks", {"prompt": "x"}),  # 404/403 answer before the engine gate
    ("GET", "/api/v1/projects/{id}/conversations", None),
    ("POST", "/api/v1/projects/{id}/conversations", {}),
]
FILE = "README.md"  # exists in every blank project
WRITES = {
    ("DELETE", "/api/v1/projects/{id}"),
    ("PATCH", "/api/v1/projects/{id}"),
    ("POST", "/api/v1/projects/{id}/archive"),
    ("POST", "/api/v1/projects/{id}/unarchive"),
    ("POST", "/api/v1/projects/{id}/imports"),
    ("PUT", "/api/v1/projects/{id}/files/{path}"),
    ("POST", "/api/v1/projects/{id}/files"),
    ("DELETE", "/api/v1/projects/{id}/files/{path}"),
    ("POST", "/api/v1/projects/{id}/patches"),
    ("POST", "/api/v1/projects/{id}/git/branch"),
    ("POST", "/api/v1/projects/{id}/git/commit"),
    ("POST", "/api/v1/projects/{id}/index"),
    ("PATCH", "/api/v1/projects/{id}/budget"),
    ("POST", "/api/v1/projects/{id}/tasks"),
    ("POST", "/api/v1/projects/{id}/conversations"),
}


@pytest.fixture(autouse=True)
async def clean_state(engine: AsyncEngine) -> AsyncIterator[None]:
    await reset_data(engine)
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    await redis.flushdb()
    yield
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture
async def app(tmp_path: Path) -> AsyncIterator[FastAPI]:
    app = create_app(load_settings(make_test_env(COOKIE_SECURE="false", WORKSPACE_ROOT=str(tmp_path))))
    yield app
    await app.state.dependencies.aclose()


@pytest.fixture
async def world(app: FastAPI, engine: AsyncEngine) -> AsyncIterator[dict[str, object]]:
    """The owner's project, a stranger, a global admin and a project-scoped readonly user."""
    from app.core.audit import AuditWriter
    from app.core.redaction import Redactor
    from app.services.users import create_owner

    await create_owner(
        make_sessionmaker(engine),
        AuditWriter(Redactor()),
        email=OWNER[0],
        display_name="Tira",
        password=OWNER[1],
    )
    owner = await signed_in(app, *OWNER)
    project = (await create(owner, "Secret Plan")).json()["data"]
    await make_user(engine, "eve@example.test")
    await make_user(engine, "adi@example.test", ("admin", "global"))
    reader_id = await make_user(engine, "rui@example.test")
    await grant(engine, reader_id, "readonly", f"project:{project['id']}")
    clients = {
        "owner": owner,
        "stranger": await signed_in(app, "eve@example.test", "user-long-passphrase"),
        "admin": await signed_in(app, "adi@example.test", "user-long-passphrase"),
        "reader": await signed_in(app, "rui@example.test", "user-long-passphrase"),
    }
    yield {"project": project, **clients}
    for client in clients.values():
        await client.aclose()


async def call(
    client: httpx.AsyncClient, method: str, path: str, body: dict[str, object] | None
) -> httpx.Response:
    return await client.request(method, path, json=body, headers=headers(client))


def all_routes(routes: Iterable[BaseRoute], prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    """(full path, route) for every route the app serves, hidden ones too (``include_in_schema=False``
    is not in OpenAPI)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        nested = getattr(route, "original_router", None)  # an included router in this FastAPI
        if nested is not None:
            context = getattr(route, "include_context", None)
            yield from all_routes(nested.routes, prefix + getattr(context, "prefix", ""))


def project_routes(app: FastAPI) -> dict[tuple[str, str], APIRoute]:
    return {
        (method, path): route
        for path, route in all_routes(app.router.routes)
        if "{project_id}" in path
        for method in (route.methods or set()) - {"HEAD"}
    }


def test_every_project_route_is_in_the_matrix(app: FastAPI) -> None:
    listed = {
        (method, path.split("?")[0].replace("{id}", "{project_id}").replace("{path}", "{path:path}"))
        for method, path, _ in ENDPOINTS
    }
    assert set(project_routes(app)) == listed


def test_every_project_route_has_the_project_door(app: FastAPI) -> None:
    """The first door (SECURITY_MODEL §8) is a dependency of the route, not a habit of its body."""
    for key, route in project_routes(app).items():
        guards = [
            dependency.call
            for dependency in route.dependant.dependencies
            if getattr(dependency.call, "__qualname__", "").startswith("require_project_permission.")
        ]
        assert guards, f"{key} has no require_project_permission dependency"


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
@pytest.mark.parametrize("who", ["stranger", "admin"])
async def test_endpoint_denies_other_users_project(
    world: dict[str, object], method: str, path: str, body: dict[str, object] | None, who: str
) -> None:
    project = world["project"]
    assert isinstance(project, dict)
    client = world[who]
    assert isinstance(client, httpx.AsyncClient)
    response = await call(client, method, path.format(id=project["id"], path=FILE), body)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "Secret Plan" not in response.text
    # Same answer as an id that does not exist at all: nothing tells them apart.
    missing = await call(client, method, path.format(id=uuid.uuid4(), path=FILE), body)
    assert (missing.status_code, missing.json()["error"]) == (404, response.json()["error"])


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
async def test_project_scoped_reader_reads_but_cannot_write(
    world: dict[str, object], method: str, path: str, body: dict[str, object] | None
) -> None:
    project = world["project"]
    reader = world["reader"]
    assert isinstance(project, dict) and isinstance(reader, httpx.AsyncClient)
    response = await call(reader, method, path.format(id=project["id"], path=FILE), body)
    if (method, path) in WRITES:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "FORBIDDEN"
    else:
        assert response.status_code == 200, response.text


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
async def test_deleted_project_is_gone_even_for_its_owner(
    world: dict[str, object], engine: AsyncEngine, method: str, path: str, body: dict[str, object] | None
) -> None:
    project = world["project"]
    owner = world["owner"]
    assert isinstance(project, dict) and isinstance(owner, httpx.AsyncClient)
    async with make_sessionmaker(engine)() as session:
        await session.execute(
            update(Project).where(Project.id == project["id"]).values(status="deleted", deleted_at=func.now())
        )
        await session.commit()
    response = await call(owner, method, path.format(id=project["id"], path=FILE), body)
    assert response.status_code == 404
    listed = (await owner.get("/api/v1/projects", params={"status": "all"})).json()["data"]
    assert listed == []


async def test_a_malformed_id_is_a_plain_404_or_422(world: dict[str, object]) -> None:
    owner = world["owner"]
    assert isinstance(owner, httpx.AsyncClient)
    response = await owner.get("/api/v1/projects/not-a-uuid")
    assert response.status_code in {404, 422}


@pytest.mark.parametrize(
    ("method", "path", "body"), [*ENDPOINTS, ("POST", "/api/v1/projects", {"name": "x"})]
)
async def test_signed_out_is_401_on_every_route(
    app: FastAPI, method: str, path: str, body: dict[str, object] | None
) -> None:
    transport = httpx.ASGITransport(app=app)
    # A matching double-submit pair without a session: past the CSRF middleware, to the auth door.
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", cookies={"kk_csrf": "t" * 43}
    ) as anonymous:
        sent = {"X-CSRF-Token": "t" * 43, "Idempotency-Key": str(uuid.uuid4())}
        response = await anonymous.request(
            method, path.format(id=uuid.uuid4(), path=FILE), json=body, headers=sent
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        *[(m, p, b) for m, p, b in ENDPOINTS if (m, p) in WRITES],
        ("POST", "/api/v1/projects", {"name": "x", "source": {"type": "blank"}}),
    ],
)
async def test_a_write_without_the_csrf_token_is_refused(
    world: dict[str, object], method: str, path: str, body: dict[str, object] | None
) -> None:
    project = world["project"]
    owner = world["owner"]
    assert isinstance(project, dict) and isinstance(owner, httpx.AsyncClient)
    sent = {"Idempotency-Key": str(uuid.uuid4())}
    response = await owner.request(method, path.format(id=project["id"], path=FILE), json=body, headers=sent)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    after = (await owner.get(f"/api/v1/projects/{project['id']}")).json()["data"]
    assert (after["name"], after["status"]) == ("Secret Plan", "active")


async def test_a_global_role_counts_inside_a_project_it_was_granted(
    world: dict[str, object], app: FastAPI, engine: AsyncEngine
) -> None:
    """Pins API_CONTRACTS §2 as written: a global role applies in every project the user holds a role in.

    A global admin given ``readonly`` on a project can therefore write it. AG-13 flagged this as a surprise
    for an owner who granted "readonly"; the PO decides (tracker, WP-2.1a). Until then this test pins it.
    """
    project = world["project"]
    assert isinstance(project, dict)
    admin = world["admin"]
    assert isinstance(admin, httpx.AsyncClient)
    async with make_sessionmaker(engine)() as session:
        admin_id = (
            await session.execute(text("SELECT id FROM users WHERE email = 'adi@example.test'"))
        ).scalar_one()
    await grant(engine, admin_id, "readonly", f"project:{project['id']}")
    response = await admin.patch(
        f"/api/v1/projects/{project['id']}", json={"description": "x"}, headers=headers(admin)
    )
    assert response.status_code == 200, response.text


async def test_approvals_of_other_projects_do_not_exist(world: dict[str, object]) -> None:
    """SECURITY_MODEL §11: approvals are scoped like their project (AC-01)."""
    project, owner = world["project"], world["owner"]
    assert isinstance(project, dict) and isinstance(owner, httpx.AsyncClient)
    asked = await owner.delete(f"/api/v1/projects/{project['id']}", headers=headers(owner))
    assert asked.status_code == 202, asked.text
    approval = asked.json()["data"]["approval"]
    for who in ("stranger", "admin"):
        client = world[who]
        assert isinstance(client, httpx.AsyncClient)
        hidden = await client.get(f"/api/v1/approvals/{approval['id']}")
        missing = await client.get(f"/api/v1/approvals/{uuid.uuid4()}")
        assert (hidden.status_code, hidden.json()["error"]) == (
            404,
            missing.json()["error"],
        )  # indistinguishable
        decision = await client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={"decision": "approve", "payload_hash": approval["payload_hash"]},
            headers=headers(client),
        )
        assert decision.status_code == 404
        assert (await client.get("/api/v1/approvals", params={"status": "all"})).json()["data"] == []


# --- routes addressed by a task or conversation id (AG-13 WP-3.2a LOW) --------------------------------------

# Their project comes from the row, so the door is in the service (no ``require_project_permission``); these
# tests pin the same answers as for the project routes. ``{task_id}``/``{conversation_id}`` are filled in.
ID_ENDPOINTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/api/v1/tasks/{task_id}", None),
    ("GET", "/api/v1/conversations/{conversation_id}/messages", None),
    ("POST", "/api/v1/conversations/{conversation_id}/messages", {"content": "x", "create_task": False}),
]
ID_WRITES = {("POST", "/api/v1/conversations/{conversation_id}/messages")}


def id_routes(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, path)
        for path, route in all_routes(app.router.routes)
        if "{task_id}" in path or "{conversation_id}" in path
        for method in (route.methods or set()) - {"HEAD"}
    }


def test_every_task_and_conversation_route_is_in_the_matrix(app: FastAPI) -> None:
    assert id_routes(app) == {(method, path) for method, path, _ in ID_ENDPOINTS}


@pytest.fixture
async def addressed(world: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A task and a conversation of the owner's project (the engine gate opened to create the task)."""
    from app.agents import readiness

    monkeypatch.setattr(readiness, "RUNTIMES_AVAILABLE", {"agent_runtime": False, "workflow_engine": True})
    project, owner = world["project"], world["owner"]
    assert isinstance(project, dict) and isinstance(owner, httpx.AsyncClient)
    task = await call(owner, "POST", f"/api/v1/projects/{project['id']}/tasks", {"prompt": "Rencana rahasia"})
    assert task.status_code == 202, task.text
    conversation = await call(owner, "POST", f"/api/v1/projects/{project['id']}/conversations", {})
    assert conversation.status_code == 201, conversation.text
    return {"task_id": task.json()["data"]["id"], "conversation_id": conversation.json()["data"]["id"]}


def fill(path: str, ids: dict[str, str]) -> str:
    return path.format(task_id=ids["task_id"], conversation_id=ids["conversation_id"])


def random_ids() -> dict[str, str]:
    return {"task_id": str(uuid.uuid4()), "conversation_id": str(uuid.uuid4())}


@pytest.mark.parametrize(("method", "path", "body"), ID_ENDPOINTS)
@pytest.mark.parametrize("who", ["stranger", "admin"])
async def test_id_routes_deny_other_users(
    world: dict[str, object],
    addressed: dict[str, str],
    method: str,
    path: str,
    body: dict[str, object] | None,
    who: str,
) -> None:
    owner, client = world["owner"], world[who]
    assert isinstance(owner, httpx.AsyncClient) and isinstance(client, httpx.AsyncClient)
    assert (await call(owner, method, fill(path, addressed), body)).status_code in (200, 201)  # it answers
    response = await call(client, method, fill(path, addressed), body)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "Secret Plan" not in response.text and "rahasia" not in response.text
    missing = await call(client, method, fill(path, random_ids()), body)
    assert (missing.status_code, missing.json()["error"]) == (404, response.json()["error"])


@pytest.mark.parametrize(("method", "path", "body"), ID_ENDPOINTS)
async def test_id_routes_are_gone_with_a_deleted_project(
    world: dict[str, object],
    addressed: dict[str, str],
    engine: AsyncEngine,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    project, owner = world["project"], world["owner"]
    assert isinstance(project, dict) and isinstance(owner, httpx.AsyncClient)
    async with make_sessionmaker(engine)() as session:
        await session.execute(
            update(Project).where(Project.id == project["id"]).values(status="deleted", deleted_at=func.now())
        )
        await session.commit()
    response = await call(owner, method, fill(path, addressed), body)
    assert response.status_code == 404, response.text
    missing = await call(owner, method, fill(path, random_ids()), body)
    assert response.json()["error"] == missing.json()["error"]


@pytest.mark.parametrize(("method", "path", "body"), ID_ENDPOINTS)
async def test_id_routes_deny_an_ex_member(
    world: dict[str, object],
    addressed: dict[str, str],
    app: FastAPI,
    engine: AsyncEngine,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    """A former developer of the project: its task and even the conversation they started answer 404."""
    project = world["project"]
    assert isinstance(project, dict)
    dev_id = await make_user(engine, "dev@example.test")
    await grant(engine, dev_id, "developer", f"project:{project['id']}")
    dev = await signed_in(app, "dev@example.test", "user-long-passphrase")
    try:
        own = await call(dev, "POST", f"/api/v1/projects/{project['id']}/conversations", {})
        assert own.status_code == 201, own.text
        ids = {**addressed, "conversation_id": own.json()["data"]["id"]}
        assert (await call(dev, method, fill(path, ids), body)).status_code in (200, 201)  # while a member
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM user_roles WHERE user_id = :id"), {"id": dev_id})
        response = await call(dev, method, fill(path, ids), body)
        assert response.status_code == 404, response.text
        missing = await call(dev, method, fill(path, random_ids()), body)
        assert response.json()["error"] == missing.json()["error"]
    finally:
        await dev.aclose()


@pytest.mark.parametrize(("method", "path", "body"), [e for e in ID_ENDPOINTS if (e[0], e[1]) in ID_WRITES])
async def test_an_id_route_write_without_the_csrf_token_is_refused(
    world: dict[str, object],
    addressed: dict[str, str],
    engine: AsyncEngine,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    owner = world["owner"]
    assert isinstance(owner, httpx.AsyncClient)
    sent = {"Idempotency-Key": str(uuid.uuid4())}
    response = await owner.request(method, fill(path, addressed), json=body, headers=sent)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM messages"))).scalar_one() == 0
