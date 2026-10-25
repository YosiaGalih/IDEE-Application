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
from sandbox_manager.runs import DriverError

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
        ("SANDBOX_PIDS_LIMIT", "0"),
        ("SANDBOX_PIDS_LIMIT", "-1"),
        ("SANDBOX_MEMORY_LIMIT", "0"),
        ("SANDBOX_MEMORY_LIMIT", "1m"),
        ("SANDBOX_CPU_LIMIT", "0"),
        ("SANDBOX_CPU_LIMIT", "-1"),
        ("SANDBOX_CPU_LIMIT", "nan"),
        ("SANDBOX_CPU_LIMIT", "inf"),
        ("SANDBOX_MAX_CONCURRENT_RUNS", "0"),
        ("SANDBOX_MAX_OUTPUT_BYTES", "-5"),
        ("SANDBOX_MAX_WORKSPACE_GROWTH_MB", "0"),
        ("SANDBOX_TIMEOUT_SECONDS", "0"),
        ("DOCKER_HOST", "ssh://host"),
    ],
)
def test_bad_settings_refused(name: str, value: str) -> None:
    with pytest.raises(ConfigError):
        load_settings({"SANDBOX_MANAGER_TOKEN": TOKEN, name: value})


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers=AUTH)
    assert response.json() == {
        "status": "ok",
        "driver": "fake",
        "docker": "ok",
        "images": {"python": True, "node": True},
        "runtime": "runc",
    }


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
    assert data["artifacts"] == {
        "junit": '<testsuite tests="1" failures="0"><testcase name="t"/></testsuite>'
    }
    assert data["artifact_status"] == {"junit": "ok"} and data["log_incomplete"] is False
    assert data["truncated"] is False
    assert data["duration_ms"] is not None and data["started_at"] and data["finished_at"]


async def test_run_poll_and_logs(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    started = (await client.post("/v1/runs", headers=AUTH, json=run_body(args=["fail"]))).json()
    assert set(started) == {"run_id", "status", "timeout_s"} and started["timeout_s"] == 60
    final = await wait_final(client, started["run_id"])
    assert final["status"] == "failed" and final["exit_code"] == 1
    assert final["stderr_tail"] == "assertion failed\n"
    logs = (await client.get(f"/v1/runs/{started['run_id']}/logs", headers=AUTH)).json()
    assert logs["done"] is True and logs["offset"] == 0 and logs["next_offset"] == 2
    assert logs["status"] == "failed"
    assert [c["stream"] for c in logs["chunks"]] == ["stdout", "stderr"]
    rest = (await client.get(f"/v1/runs/{started['run_id']}/logs?offset=1", headers=AUTH)).json()
    assert rest["chunks"] == [{"stream": "stderr", "data": "assertion failed\n"}]
    beyond = (await client.get(f"/v1/runs/{started['run_id']}/logs?offset=-5", headers=AUTH)).json()
    assert beyond["offset"] == 0


async def test_output_is_capped(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    data = (await client.post("/v1/runs", headers=AUTH, json=run_body(args=["spam"], wait=True))).json()
    assert data["truncated"] is True
    assert len(data["stdout_tail"]) == 64 * 1024
    assert data["stdout_tail"].endswith("ran python.pytest\n")  # the real end of the output survives


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
    assert killed == {"killed": 2, "failed": 0}
    for run_id in ids:
        assert (await wait_final(client, run_id))["status"] == "killed"
    again = (await client.post(f"/v1/tasks/{TASK}/kill", headers=AUTH)).json()
    assert again == {"killed": 0, "failed": 0}


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


async def test_kill_of_unreachable_container_is_503(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    started = (await client.post("/v1/runs", headers=AUTH, json=run_body(args=["sleep"]))).json()
    await asyncio.sleep(0.05)
    app_driver = client._transport.app.state.registry.driver  # type: ignore[attr-defined]

    async def broken(record: object) -> None:
        raise DriverError("docker unreachable")

    original = app_driver.kill
    app_driver.kill = broken
    response = await client.post(f"/v1/runs/{started['run_id']}/kill", headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "DOCKER_UNAVAILABLE", "message": "The container could not be reached"}
    }
    app_driver.kill = original
    await client.post(f"/v1/runs/{started['run_id']}/kill", headers=AUTH)


async def test_retry_with_client_request_id(client: httpx.AsyncClient, settings: Settings) -> None:
    task_workspace(settings)
    first = (await client.post("/v1/runs", headers=AUTH, json=run_body(client_request_id="t-1"))).json()
    again = (await client.post("/v1/runs", headers=AUTH, json=run_body(client_request_id="t-1"))).json()
    assert again["run_id"] == first["run_id"]
    other = await client.post(
        "/v1/runs", headers=AUTH, json=run_body(profile_id="node.npm_test", client_request_id="t-1")
    )
    assert other.status_code == 422 and other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    bad = await client.post("/v1/runs", headers=AUTH, json=run_body(client_request_id="a b"))
    assert bad.status_code == 422


def test_docker_socket_refused_in_production() -> None:
    """ADR-009 (1): production reaches Docker only through the socket proxy."""
    env = {"SANDBOX_MANAGER_TOKEN": TOKEN}
    assert load_settings(env).docker_host == "tcp://docker-socket-proxy:2375"
    with pytest.raises(ConfigError):
        load_settings({**env, "DOCKER_HOST": "unix:///var/run/docker.sock"})
    dev = load_settings({**env, "DOCKER_HOST": "unix:///var/run/docker.sock", "APP_ENV": "development"})
    assert dev.docker_host.startswith("unix://")


def test_token_is_not_in_repr(settings: Settings) -> None:
    assert TOKEN not in repr(settings)


def test_process_user_checked_in_production(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from sandbox_manager.config import check_process_user

    production = replace(settings, app_env="production")
    for uid, allowed in ((0, True), (10001, True), (1001, False)):
        monkeypatch.setattr("os.geteuid", lambda uid=uid: uid)
        if allowed:
            check_process_user(production)
        else:
            with pytest.raises(ConfigError):
                check_process_user(production)
    check_process_user(settings)  # outside production any uid may run it (tests, CI)


async def test_openapi_is_not_served(client: httpx.AsyncClient) -> None:
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert (await client.get(path)).status_code in (401, 404)


async def test_profile_without_timeout_uses_the_setting(
    settings: Settings, profiles: dict[str, Profile]
) -> None:
    from sandbox_manager.validation import RunRequest, validate

    task_workspace(settings)
    loose = {**profiles, "python.pytest": profiles["python.pytest"].model_copy(update={"timeout_s": None})}
    run = validate(RunRequest.model_validate(run_body()), loose, replace(settings, default_timeout_s=90))
    assert run.timeout_s == 90
    capped = validate(RunRequest.model_validate(run_body()), loose, replace(settings, default_timeout_s=999))
    assert capped.timeout_s == 120  # never above the profile's maximum
