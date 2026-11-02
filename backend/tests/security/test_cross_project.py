"""Cross-project access (AC-01, SECURITY_MODEL §8, T6): a project outside the user's scope does not exist.

Every endpoint with a project id is listed in ``ENDPOINTS``; a new one must be added here. Out of scope →
404 (never 403, which would confirm the id); in scope without the permission → 403.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.main import create_app
from app.models.projects import Project
from tests.conftest import make_test_env, reset_data
from tests.integration.test_projects import OWNER, create, grant, headers, make_user, signed_in

# (method, path template, json body) for every route under /projects/{project_id}.
ENDPOINTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/api/v1/projects/{id}", None),
    ("PATCH", "/api/v1/projects/{id}", {"name": "Taken"}),
    ("POST", "/api/v1/projects/{id}/archive", None),
    ("POST", "/api/v1/projects/{id}/unarchive", None),
    ("GET", "/api/v1/projects/{id}/stack", None),
]
WRITES = {
    ("PATCH", "/api/v1/projects/{id}"),
    ("POST", "/api/v1/projects/{id}/archive"),
    ("POST", "/api/v1/projects/{id}/unarchive"),
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


def test_every_project_route_is_in_the_matrix(app: FastAPI) -> None:
    # From the OpenAPI schema: included routers are not flattened into app.routes in this FastAPI.
    routes = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        if "{project_id}" in path
        for method in item
    }
    listed = {(method, path.replace("{id}", "{project_id}")) for method, path, _ in ENDPOINTS}
    assert routes == listed


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
@pytest.mark.parametrize("who", ["stranger", "admin"])
async def test_endpoint_denies_other_users_project(
    world: dict[str, object], method: str, path: str, body: dict[str, object] | None, who: str
) -> None:
    project = world["project"]
    assert isinstance(project, dict)
    client = world[who]
    assert isinstance(client, httpx.AsyncClient)
    response = await call(client, method, path.format(id=project["id"]), body)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "Secret Plan" not in response.text
    # Same answer as an id that does not exist at all: nothing tells them apart.
    missing = await call(client, method, path.format(id=uuid.uuid4()), body)
    assert (missing.status_code, missing.json()["error"]) == (404, response.json()["error"])


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
async def test_project_scoped_reader_reads_but_cannot_write(
    world: dict[str, object], method: str, path: str, body: dict[str, object] | None
) -> None:
    project = world["project"]
    reader = world["reader"]
    assert isinstance(project, dict) and isinstance(reader, httpx.AsyncClient)
    response = await call(reader, method, path.format(id=project["id"]), body)
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
        await session.execute(update(Project).where(Project.id == project["id"]).values(status="deleted"))
        await session.commit()
    response = await call(owner, method, path.format(id=project["id"]), body)
    assert response.status_code == 404
    listed = (await owner.get("/api/v1/projects", params={"status": "all"})).json()["data"]
    assert listed == []


async def test_a_malformed_id_is_a_plain_404_or_422(world: dict[str, object]) -> None:
    owner = world["owner"]
    assert isinstance(owner, httpx.AsyncClient)
    response = await owner.get("/api/v1/projects/not-a-uuid")
    assert response.status_code in {404, 422}
