"""HttpSandboxClient against a mocked sandbox manager (respx; API_CONTRACTS §10)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
import respx

from app.sandbox.client import (
    FakeSandboxClient,
    HttpSandboxClient,
    SandboxAuthError,
    SandboxRequestError,
    SandboxRunRequest,
    SandboxUnavailable,
)

BASE = "http://sandbox-manager.test:8100"
TOKEN = "s" * 40
PROJECT = uuid.UUID("aaaaaaaa-1111-4111-8111-bbbbbbbbbbbb")
TASK = uuid.UUID("cccccccc-2222-4222-8222-dddddddddddd")
REQUEST = SandboxRunRequest(
    project_id=PROJECT,
    task_id=TASK,
    workspace_subpath=f"{PROJECT}/tasks/{TASK}",
    profile_id="python.pytest",
    args=["tests/test_a.py"],
    timeout_s=60,
)
FINAL: dict[str, Any] = {
    "run_id": "r1",
    "status": "failed",
    "exit_code": 1,
    "started_at": "2026-10-24T00:00:00+00:00",
    "finished_at": "2026-10-24T00:00:02+00:00",
    "duration_ms": 2000,
    "image": "kodekantor/sandbox-python:1",
    "image_digest": "sha256:abc",
    "stdout_tail": "1 failed\n",
    "stderr_tail": "",
    "truncated": False,
    "artifacts": {"junit": "<testsuites/>"},
    "error_code": None,
}


@pytest.fixture
async def client() -> Any:
    sandbox = HttpSandboxClient(BASE, TOKEN, poll_interval_s=0)
    yield sandbox
    await sandbox.aclose()


@respx.mock
async def test_run_follows_logs_until_done(client: HttpSandboxClient) -> None:
    submit = respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(200, json={"run_id": "r1", "status": "queued"})
    )
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "offset": 0,
                    "next_offset": 1,
                    "chunks": [{"stream": "stdout", "data": "a"}],
                    "done": False,
                },
            ),
            httpx.Response(200, json={"offset": 1, "next_offset": 1, "chunks": [], "done": False}),
            httpx.Response(
                200,
                json={
                    "offset": 1,
                    "next_offset": 2,
                    "chunks": [{"stream": "stderr", "data": "b"}],
                    "done": True,
                },
            ),
        ]
    )
    respx.get(f"{BASE}/v1/runs/r1").mock(return_value=httpx.Response(200, json=FINAL))
    seen: list[tuple[str, str]] = []

    async def on_log(stream: str, text: str) -> None:
        seen.append((stream, text))

    result = await client.run(REQUEST, on_log=on_log)
    assert seen == [("stdout", "a"), ("stderr", "b")]
    assert (result.status, result.exit_code, result.stdout) == ("failed", 1, "1 failed\n")
    assert result.artifacts == {"junit": "<testsuites/>"}
    assert result.image_digest == "sha256:abc"
    sent = submit.calls.last.request
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(sent.content) == {
        "project_id": str(PROJECT),
        "task_id": str(TASK),
        "workspace_subpath": f"{PROJECT}/tasks/{TASK}",
        "profile_id": "python.pytest",
        "args": ["tests/test_a.py"],
        "timeout_s": 60,
        "network": "none",
        "read_only": False,
        "wait": False,
    }
    offsets = [
        call.request.url.params["offset"] for call in respx.calls if call.request.url.path.endswith("/logs")
    ]
    assert offsets == ["0", "1", "1"]


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_errors(client: HttpSandboxClient, status: int) -> None:
    respx.post(f"{BASE}/v1/runs").mock(return_value=httpx.Response(status))
    with pytest.raises(SandboxAuthError) as info:
        await client.run(REQUEST)
    assert TOKEN not in str(info.value)


@respx.mock
async def test_request_errors_carry_the_manager_code(client: HttpSandboxClient) -> None:
    body = {"error": {"code": "INVALID_ARGS", "message": "An argument is not allowed by the profile"}}
    respx.post(f"{BASE}/v1/runs").mock(return_value=httpx.Response(422, json=body))
    with pytest.raises(SandboxRequestError) as info:
        await client.run(REQUEST)
    assert info.value.code == "INVALID_ARGS"


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(502, text="bad gateway"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"status": "queued"}),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_unavailable(client: HttpSandboxClient, response: Any) -> None:
    route = respx.post(f"{BASE}/v1/runs")
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    with pytest.raises(SandboxUnavailable) as info:
        await client.run(REQUEST)
    assert TOKEN not in str(info.value)


@respx.mock
async def test_run_lost_after_restart(client: HttpSandboxClient) -> None:
    respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(200, json={"run_id": "r1", "status": "queued"})
    )
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(return_value=httpx.Response(404))
    with pytest.raises(SandboxUnavailable):
        await client.run(REQUEST)


@respx.mock
async def test_done_without_final_status_is_not_trusted(client: HttpSandboxClient) -> None:
    respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(200, json={"run_id": "r1", "status": "queued"})
    )
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(
        return_value=httpx.Response(200, json={"offset": 0, "next_offset": 0, "chunks": [], "done": True})
    )
    respx.get(f"{BASE}/v1/runs/r1").mock(
        return_value=httpx.Response(200, json={**FINAL, "status": "running"})
    )
    with pytest.raises(SandboxUnavailable):
        await client.run(REQUEST)


@respx.mock
async def test_unknown_status_is_not_trusted(client: HttpSandboxClient) -> None:
    respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(200, json={"run_id": "r1", "status": "queued"})
    )
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(
        return_value=httpx.Response(200, json={"offset": 0, "next_offset": 0, "chunks": [], "done": True})
    )
    respx.get(f"{BASE}/v1/runs/r1").mock(return_value=httpx.Response(200, json={**FINAL, "status": "passed"}))
    with pytest.raises(SandboxUnavailable):
        await client.run(REQUEST)


@respx.mock
async def test_stuck_manager_is_killed_at_the_deadline(
    client: HttpSandboxClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.sandbox.client as client_module

    monkeypatch.setattr(client_module, "DEADLINE_SLACK_S", -60)  # deadline = now
    respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(200, json={"run_id": "r1", "status": "queued"})
    )
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(
        return_value=httpx.Response(200, json={"offset": 0, "next_offset": 0, "chunks": [], "done": False})
    )
    kill = respx.post(f"{BASE}/v1/runs/r1/kill").mock(
        return_value=httpx.Response(200, json={"status": "killed"})
    )
    with pytest.raises(SandboxUnavailable):
        await client.run(REQUEST)
    assert kill.called


@respx.mock
async def test_kill_and_kill_task(client: HttpSandboxClient) -> None:
    kill = respx.post(f"{BASE}/v1/runs/r1/kill").mock(
        return_value=httpx.Response(200, json={"status": "killed"})
    )
    respx.post(f"{BASE}/v1/tasks/{TASK}/kill").mock(return_value=httpx.Response(200, json={"killed": 2}))
    await client.kill("r1")
    assert kill.called
    assert await client.kill_task(TASK) == 2


@respx.mock
@pytest.mark.parametrize(
    ("response", "healthy"),
    [
        (
            httpx.Response(200, json={"status": "ok", "driver": "docker", "docker": "ok", "runtime": "runc"}),
            True,
        ),
        (httpx.Response(200, json={"status": "ok", "driver": "docker", "docker": "unavailable"}), False),
        (httpx.Response(401), False),
        (httpx.Response(200, text="[]"), False),
        (httpx.ConnectError("refused"), False),
    ],
)
async def test_health(client: HttpSandboxClient, response: Any, healthy: bool) -> None:
    route = respx.get(f"{BASE}/healthz")
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    assert await client.health() is healthy


async def test_fake_client() -> None:
    fake = FakeSandboxClient(logs=[("stdout", "hi")])
    seen: list[str] = []

    async def on_log(stream: str, text: str) -> None:
        seen.append(text)

    result = await fake.run(REQUEST, on_log=on_log)
    assert result.status == "succeeded" and seen == ["hi"]
    assert fake.requests == [REQUEST]
    assert await fake.kill_task(TASK) == 0 and fake.killed_tasks == [TASK]
