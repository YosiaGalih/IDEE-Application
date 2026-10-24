"""HTTP API with the fake driver (API_CONTRACTS §10)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import httpx
import pytest

from sandbox_manager.app import create_app
from sandbox_manager.config import ConfigError, Settings, load_settings
from sandbox_manager.fake_driver import FakeDriver
from sandbox_manager.profiles import Profile

from .conftest import PROJECT, TASK, TOKEN, task_workspace

AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
async def client(settings: Settings, profiles: dict[str, Profile]) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(replace(settings, max_output_bytes=10_000), driver=FakeDriver(), profiles=profiles)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://manager") as http:
            yield http


def run_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "project_id": str(PROJECT),
        "task_id": str(TASK),
        "workspace_subpath": f"{PROJECT}/tasks/{TASK}",
        "profile_id": "python.pytest",
    }
    body.update(overrides)
    return body


async def wait_final(client: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    for _ in range(200):
        data = (await client.get(f"/v1/runs/{run_id}", headers=AUTH)).json()
        if data["status"] not in ("queued", "running"):
            return data
        await asyncio.sleep(0.02)
    raise AssertionError("run did not finish")


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer ", f"bearer {TOKEN}", TOKEN, f"Bearer {TOKEN}x", f"Basic {TOKEN}"],
)
async def test_auth_required(client: httpx.AsyncClient, header: str | None) -> None:
    headers = {} if header is None else {"Authorization": header}
    for method, path in (("GET", "/healthz"), ("GET", "/v1/profiles"), ("POST", "/v1/runs")):
        response = await client.request(method, path, headers=headers, json=run_body())
        assert response.status_code == 401
        assert response.json() == {"error": {"code": "UNAUTHENTICATED", "message": "Request refused"}}


def test_short_token_refused_at_start() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"SANDBOX_MANAGER_TOKEN": "x" * 31})
    assert "x" * 31 not in str(info.value)


def test_fake_driver_refused_in_production() -> None:
    for env in ({}, {"APP_ENV": "production"}):
        with pytest.raises(ConfigError):
            load_settings({"SANDBOX_MANAGER_TOKEN": TOKEN, "SANDBOX_DRIVER": "fake", **env})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SANDBOX_DRIVER", "podman"),
        ("SANDBOX_EGRESS", "on"),
        ("SANDBOX_RUNTIME", "kata"),
        ("SANDBOX_MEMORY_LIMIT", "1t"),
        ("SANDBOX_PIDS_LIMIT", "many"),
        ("WORKSPACE_ROOT", "relative/path"),
    ],
)
def test_bad_settings_refused(name: str, value: str) -> None:
    with pytest.raises(ConfigError):
        load_settings({"SANDBOX_MANAGER_TOKEN": TOKEN, name: value})


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers=AUTH)
    assert response.json() == {"status": "ok", "driver": "fake", "docker": "ok", "runtime": "runc"}


async def test_profiles(client: httpx.AsyncClient) -> None:
    profiles = (await client.get("/v1/profiles", headers=AUTH)).json()
    assert [p["profile_id"] for p in profiles] == ["python.pytest", "node.npm_test"]
    assert profiles[0]["image"] == "kodekantor/sandbox-python:1"


async def test_run_wait(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    data = (await client.post("/v1/runs", headers=AUTH, json=run_body(wait=True))).json()
    assert data["status"] == "succeeded"
    assert data["exit_code"] == 0
    assert data["stdout_tail"] == "ran python.pytest\n"
    assert data["artifacts"] == {"junit": '<testsuite tests="1" failures="0"/>'}
    assert data["truncated"] is False
    assert data["duration_ms"] is not None and data["started_at"] and data["finished_at"]


async def test_run_poll_and_logs(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    started = (await client.post("/v1/runs", headers=AUTH, json=run_body(args=["fail"]))).json()
    assert set(started) == {"run_id", "status"}
    final = await wait_final(client, started["run_id"])
    assert final["status"] == "failed" and final["exit_code"] == 1
    assert final["stderr_tail"] == "assertion failed\n"
    logs = (await client.get(f"/v1/runs/{started['run_id']}/logs", headers=AUTH)).json()
    assert logs["done"] is True and logs["offset"] == 0 and logs["next_offset"] == 2
    assert [c["stream"] for c in logs["chunks"]] == ["stdout", "stderr"]
    rest = (await client.get(f"/v1/runs/{started['run_id']}/logs?offset=1", headers=AUTH)).json()
    assert rest["chunks"] == [{"stream": "stderr", "data": "assertion failed\n"}]
    beyond = (await client.get(f"/v1/runs/{started['run_id']}/logs?offset=-5", headers=AUTH)).json()
    assert beyond["offset"] == 0


async def test_output_is_capped(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    data = (await client.post("/v1/runs", headers=AUTH, json=run_body(args=["spam"], wait=True))).json()
    assert data["truncated"] is True
    assert len(data["stdout_tail"]) == 10_000


async def test_kill_run(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    started = (await client.post("/v1/runs", headers=AUTH, json=run_body(args=["sleep"]))).json()
    await asyncio.sleep(0.05)
    await client.post(f"/v1/runs/{started['run_id']}/kill", headers=AUTH)
    assert (await wait_final(client, started["run_id"]))["status"] == "killed"


async def test_kill_task(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    ids = [
        (await client.post("/v1/runs", headers=AUTH, json=run_body(args=["sleep"]))).json()["run_id"]
        for _ in range(2)
    ]
    await asyncio.sleep(0.05)
    killed = (await client.post(f"/v1/tasks/{TASK}/kill", headers=AUTH)).json()
    assert killed == {"killed": 2}
    for run_id in ids:
        assert (await wait_final(client, run_id))["status"] == "killed"
    again = (await client.post(f"/v1/tasks/{TASK}/kill", headers=AUTH)).json()
    assert again == {"killed": 0}


async def test_unknown_run(client: httpx.AsyncClient) -> None:
    for path in ("/v1/runs/nope", "/v1/runs/nope/logs"):
        response = await client.get(path, headers=AUTH)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_invalid_requests(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    cases = [
        (run_body(HostConfig={"Privileged": True}), "VALIDATION_ERROR"),
        (run_body(image="alpine"), "VALIDATION_ERROR"),
        (run_body(profile_id="python.shell"), "UNKNOWN_PROFILE"),
        (run_body(workspace_subpath="../../etc"), "INVALID_WORKSPACE"),
        (run_body(args=["-p", "evil"]), "INVALID_ARGS"),
        (run_body(timeout_s=10_000), "INVALID_TIMEOUT"),
        (run_body(network="egress"), "EGRESS_NOT_ALLOWED"),
    ]
    for body, code in cases:
        response = await client.post("/v1/runs", headers=AUTH, json=body)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == code, body
        assert "Traceback" not in response.text and str(settings.workspace_root) not in response.text
