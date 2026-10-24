"""Isolation against a real Docker daemon (marker ``docker``; AC-07, SECURITY_MODEL §4, ADR-026 (1), ADR-033).

Needs the images ``kodekantor/sandbox-python:1`` and ``kodekantor/sandbox-node:1`` (sandbox-images/) and a
daemon at ``DOCKER_HOST`` (default the local socket). Without them the tests skip, unless
``KK_REQUIRE_DOCKER=1`` (CI), where a missing daemon or image is a failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from sandbox_manager.app import create_app
from sandbox_manager.config import Settings
from sandbox_manager.docker_driver import DockerDriver, DockerEngine
from sandbox_manager.profiles import Profile, load_profiles

from .conftest import PROJECT, TASK, TOKEN, repo_workspace, task_workspace

pytestmark = pytest.mark.docker

AUTH = {"Authorization": f"Bearer {TOKEN}"}
REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_FILE = REPO_ROOT / "sandbox-images" / "sandbox-profiles.yaml"
PROBE = Profile(
    profile_id="probe.python",
    image="python",
    suite="test",
    argv=("python", "/workspace/probe.py"),
    args_pattern="[a-z]{1,20}",
    timeout_s=60,
    max_timeout_s=120,
)


def docker_host() -> str:
    return os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")


async def require_docker(images: list[str]) -> None:
    engine = DockerEngine(docker_host())
    try:
        missing = "daemon" if not await engine.ping() else None
        for image in images:
            if missing is None and not await engine.image_exists(image):
                missing = image
    finally:
        await engine.aclose()
    if missing is not None:
        if os.environ.get("KK_REQUIRE_DOCKER") == "1":
            pytest.fail(f"docker isolation tests need {missing}")
        pytest.skip(f"docker isolation tests need {missing}")


def open_to_sandbox(path: Path) -> Path:
    """The worker creates workspaces as uid 10001; as root we hand them over, otherwise open them up."""
    if os.geteuid() == 0:
        os.chown(path, 10001, 10001)
    else:
        os.chmod(path, 0o777)  # noqa: S103 - a test workspace only
    return path


@pytest.fixture
def docker_settings(settings: Settings) -> Settings:
    return replace(settings, driver="docker", docker_host=docker_host(), pids_limit=64)


async def start_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    await require_docker([settings.images["python"], settings.images["node"]])
    profiles = load_profiles(PROFILES_FILE, set(settings.images))
    profiles[PROBE.profile_id] = PROBE
    app = create_app(settings, profiles=profiles)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://manager", timeout=180) as http:
            yield http


@pytest.fixture
async def client(docker_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for http in start_client(docker_settings):
        yield http


async def run(
    client: httpx.AsyncClient, profile_id: str = "probe.python", *, repo: bool = False, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "project_id": str(PROJECT),
        "task_id": None if repo else str(TASK),
        "workspace_subpath": f"{PROJECT}/repo" if repo else f"{PROJECT}/tasks/{TASK}",
        "profile_id": profile_id,
        "wait": True,
        **extra,
    }
    response = await client.post("/v1/runs", headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def probe(workspace: Path, source: str) -> Path:
    (workspace / "probe.py").write_text(source)
    return open_to_sandbox(workspace)


def report(result: dict[str, Any]) -> dict[str, Any]:
    assert result["status"] == "succeeded", result
    return json.loads(result["stdout_tail"].strip().splitlines()[-1])


async def managed_containers() -> list[str]:
    engine = DockerEngine(docker_host())
    try:
        return await engine.managed_containers()
    finally:
        await engine.aclose()


async def test_runs_as_10001(client: httpx.AsyncClient, settings: Settings) -> None:
    probe(
        task_workspace(settings),
        "import json, os\n"
        "status = dict(line.split(':\\t', 1) for line in open('/proc/self/status').read().splitlines()"
        " if ':\\t' in line)\n"
        "print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(), 'groups': os.getgroups(),"
        " 'cap_eff': status['CapEff'], 'cap_bnd': status['CapBnd'], 'nnp': status['NoNewPrivs']}))\n",
    )
    data = report(await run(client))
    assert (data["uid"], data["gid"]) == (10001, 10001)
    assert data["groups"] in ([], [10001])
    assert int(data["cap_eff"], 16) == 0 and int(data["cap_bnd"], 16) == 0
    assert data["nnp"] == "1"


async def test_readonly_rootfs(client: httpx.AsyncClient, settings: Settings) -> None:
    """EROFS, not EACCES: the rootfs is read-only even where uid 10001 could write (/var/tmp is 1777)."""
    probe(
        task_workspace(settings),
        "import errno, json\n"
        "def write(path):\n"
        "    try:\n"
        "        open(path, 'w').write('x')\n"
        "        return 'ok'\n"
        "    except OSError as exc:\n"
        "        return errno.errorcode[exc.errno]\n"
        "print(json.dumps({p: write(p) for p in"
        " ['/etc/kk', '/usr/kk', '/kk', '/var/tmp/kk', '/tmp/kk', '/home/sandbox/kk', '/workspace/kk']}))\n",
    )
    data = report(await run(client))
    assert data == {
        "/etc/kk": "EROFS",
        "/usr/kk": "EROFS",
        "/kk": "EROFS",
        "/var/tmp/kk": "EROFS",
        "/tmp/kk": "ok",
        "/home/sandbox/kk": "ok",
        "/workspace/kk": "ok",
    }


async def test_no_network_by_default(client: httpx.AsyncClient, settings: Settings) -> None:
    probe(
        task_workspace(settings),
        "import json, os, socket\n"
        "def reach(host, port):\n"
        "    try:\n"
        "        socket.create_connection((host, port), timeout=3).close()\n"
        "        return True\n"
        "    except OSError:\n"
        "        return False\n"
        "def resolve(name):\n"
        "    try:\n"
        "        socket.getaddrinfo(name, 443)\n"
        "        return True\n"
        "    except OSError:\n"
        "        return False\n"
        "print(json.dumps({'ifaces': sorted(os.listdir('/sys/class/net')),"
        " 'ip': reach('1.1.1.1', 53), 'dns': resolve('pypi.org'), 'gateway': reach('172.17.0.1', 2375)}))\n",
    )
    data = report(await run(client))
    assert data == {"ifaces": ["lo"], "ip": False, "dns": False, "gateway": False}


async def test_env_has_no_secrets(
    client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-leak")
    probe(task_workspace(settings), "import json, os\nprint(json.dumps(dict(os.environ)))\n")
    env = report(await run(client))
    text = json.dumps(env)
    assert TOKEN not in text and "sk-ant-should-never-leak" not in text
    assert {"HOME", "PATH", "LANG", "CI"} <= set(env)
    allowed_from_image = {"GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256", "HOSTNAME"}
    extra = {k for k in env if not k.startswith(("PYTHON", "PIP_"))} - {"HOME", "PATH", "LANG", "CI"}
    assert extra <= allowed_from_image


async def test_fork_bomb_contained(client: httpx.AsyncClient, settings: Settings) -> None:
    probe(
        task_workspace(settings),
        "import json, os, signal, time\n"
        "children = []\n"
        "try:\n"
        "    for _ in range(1000):\n"
        "        pid = os.fork()\n"
        "        if pid == 0:\n"
        "            time.sleep(60)\n"
        "            os._exit(0)\n"
        "        children.append(pid)\n"
        "except OSError:\n"
        "    pass\n"
        "for pid in children:\n"
        "    os.kill(pid, signal.SIGKILL)\n"
        "print(json.dumps({'forked': len(children)}))\n",
    )
    data = report(await run(client))
    assert 0 < data["forked"] < 64


async def test_timeout_kills(client: httpx.AsyncClient, settings: Settings) -> None:
    probe(task_workspace(settings), "import time\nprint('started', flush=True)\ntime.sleep(300)\n")
    started = time.monotonic()
    result = await run(client, timeout_s=2)
    assert result["status"] == "timeout"
    assert result["stdout_tail"] == "started\n"
    assert time.monotonic() - started < 30
    assert await managed_containers() == []


async def test_kill_task(client: httpx.AsyncClient, settings: Settings) -> None:
    probe(task_workspace(settings), "import time\ntime.sleep(300)\n")
    body = {
        "project_id": str(PROJECT),
        "task_id": str(TASK),
        "workspace_subpath": f"{PROJECT}/tasks/{TASK}",
        "profile_id": "probe.python",
    }
    run_id = (await client.post("/v1/runs", headers=AUTH, json=body)).json()["run_id"]
    for _ in range(100):
        if (await client.get(f"/v1/runs/{run_id}", headers=AUTH)).json()["status"] == "running":
            break
        await asyncio.sleep(0.05)
    assert (await client.post(f"/v1/tasks/{TASK}/kill", headers=AUTH)).json() == {"killed": 1, "failed": 0}
    for _ in range(200):
        status = (await client.get(f"/v1/runs/{run_id}", headers=AUTH)).json()["status"]
        if status not in ("queued", "running"):
            break
        await asyncio.sleep(0.05)
    assert status == "killed"
    assert await managed_containers() == []


async def test_git_hidden(client: httpx.AsyncClient, settings: Settings) -> None:
    workspace = task_workspace(settings)
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("[credential]\n\thelper = store-secret\n")
    probe(
        workspace,
        "import json, os\n"
        "try:\n"
        "    open('/workspace/.git/hooks', 'w')\n"
        "    wrote = True\n"
        "except OSError:\n"
        "    wrote = False\n"
        "print(json.dumps({'listing': os.listdir('/workspace/.git'), 'wrote': wrote}))\n",
    )
    assert report(await run(client)) == {"listing": [], "wrote": False}
    assert (workspace / ".git" / "config").read_text().endswith("store-secret\n")


async def test_git_worktree_file_hidden(client: httpx.AsyncClient, settings: Settings) -> None:
    workspace = task_workspace(settings)
    (workspace / ".git").write_text("gitdir: /srv/kodekantor/workspaces/p/repo/.git/worktrees/t\n")
    probe(workspace, "import json\nprint(json.dumps({'git': open('/workspace/.git').read()}))\n")
    assert report(await run(client)) == {"git": ""}


async def test_output_truncated(docker_settings: Settings, settings: Settings) -> None:
    probe(task_workspace(settings), "import sys\nsys.stdout.write('x' * 2_000_000)\n")
    async for client in start_client(replace(docker_settings, max_output_bytes=100_000)):
        result = await run(client)
        assert result["status"] == "succeeded"
        assert result["truncated"] is True
        assert len(result["stdout_tail"]) == 64 * 1024


async def test_read_only_main_run(client: httpx.AsyncClient, settings: Settings) -> None:
    repo = repo_workspace(settings)
    (repo / "keep.txt").write_text("original")
    probe(
        repo,
        "import json\n"
        "def can_write(path):\n"
        "    try:\n"
        "        open(path, 'w').write('changed')\n"
        "        return True\n"
        "    except OSError:\n"
        "        return False\n"
        "open('/workspace/.kk/junit.xml', 'w').write('<testsuite tests=\"1\"/>')\n"
        "print(json.dumps({'keep': can_write('/workspace/keep.txt'), 'new': can_write('/workspace/new')}))\n",
    )
    result = await run(client, repo=True)
    assert report(result) == {"keep": False, "new": False}
    assert result["artifacts"] == {"junit": '<testsuite tests="1"/>'}
    assert (repo / "keep.txt").read_text() == "original"
    assert sorted(p.name for p in repo.iterdir()) == [".kk", "keep.txt", "probe.py"]
    assert list((repo / ".kk").iterdir()) == []


async def test_disk_growth_limit(docker_settings: Settings, settings: Settings) -> None:
    probe(task_workspace(settings), "open('/workspace/big.bin', 'wb').write(b'x' * 3 * 1024 * 1024)\n")
    async for client in start_client(replace(docker_settings, max_workspace_growth_mb=1)):
        result = await run(client)
        assert (result["status"], result["error_code"]) == ("error", "DISK_LIMIT")


async def test_memory_limit(docker_settings: Settings, settings: Settings) -> None:
    probe(task_workspace(settings), "blocks = [bytearray(16 * 1024 * 1024) for _ in range(64)]\n")
    async for client in start_client(replace(docker_settings, memory_bytes=128 * 1024 * 1024)):
        result = await run(client)
        # killed by the OOM killer: reported as such, never as a failing test
        assert (result["status"], result["exit_code"], result["error_code"]) == ("error", 137, "OOM_KILLED")


async def test_python_pytest_profile(client: httpx.AsyncClient, settings: Settings) -> None:
    workspace = task_workspace(settings)
    (workspace / "app.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
        "@app.get('/')\ndef root():\n    return {'ok': True}\n"
    )
    (workspace / "test_app.py").write_text(
        "from fastapi.testclient import TestClient\n\nfrom app import app\n\n\n"
        "def test_root():\n    assert TestClient(app).get('/').json() == {'ok': True}\n\n\n"
        "def test_fails():\n    assert 1 == 2\n"
    )
    open_to_sandbox(workspace)
    result = await run(client, "python.pytest", args=["test_app.py::test_root"])
    assert result["status"] == "succeeded", result
    assert 'tests="1"' in result["artifacts"]["junit"]
    result = await run(client, "python.pytest")
    assert (result["status"], result["exit_code"]) == ("failed", 1)
    assert 'failures="1"' in result["artifacts"]["junit"]
    assert not (workspace / ".pytest_cache").exists()


async def test_python_ruff_profile(client: httpx.AsyncClient, settings: Settings) -> None:
    workspace = task_workspace(settings)
    (workspace / "ok.py").write_text("print('ok')\n")
    (workspace / "bad.py").write_text("import os\n")
    open_to_sandbox(workspace)
    assert (await run(client, "python.ruff", args=["ok.py"]))["status"] == "succeeded"
    result = await run(client, "python.ruff")
    assert result["status"] == "failed"
    assert "F401" in result["stdout_tail"]


async def test_node_npm_test_profile(client: httpx.AsyncClient, settings: Settings) -> None:
    workspace = task_workspace(settings)
    (workspace / "package.json").write_text(
        json.dumps({"name": "t", "private": True, "type": "module", "scripts": {"test": "node --test"}})
    )
    (workspace / "sum.test.js").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "test('sum', () => assert.equal(1 + 1, 2));\n"
    )
    open_to_sandbox(workspace)
    result = await run(client, "node.npm_test")
    assert result["status"] == "succeeded", result


async def test_no_container_left(client: httpx.AsyncClient, settings: Settings) -> None:
    probe(task_workspace(settings), "print('{}')\n")
    for _ in range(3):
        assert (await run(client))["status"] == "succeeded"
    assert await managed_containers() == []
    assert list((settings.workspace_root / str(PROJECT) / ".kk-runs").iterdir()) == []


async def test_orphans_removed_at_start(docker_settings: Settings, settings: Settings) -> None:
    """A container left by a crashed manager (label kk.managed=1) is removed when the manager starts."""
    await require_docker([settings.images["python"]])
    engine = DockerEngine(docker_host())
    try:
        spec = {"Image": settings.images["python"], "Cmd": ["sleep", "300"], "Labels": {"kk.managed": "1"}}
        orphan = await engine.create(spec, "kk-orphan-test")
        await engine.start(orphan)
        assert orphan in await engine.managed_containers()
        removed = await DockerDriver(docker_settings, engine).cleanup_orphans()
        assert removed >= 1
        assert orphan not in await engine.managed_containers()
    finally:
        await engine.aclose()


async def test_read_only_run_cannot_flood_the_artifact_folder(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    """Security review F1: /workspace/.kk is the one writable path of a read-only run, and it is bounded."""
    repo = repo_workspace(settings)
    probe(
        repo,
        "import time\n"
        "with open('/workspace/.kk/flood.bin', 'wb') as out:\n"
        "    for _ in range(400):\n"
        "        out.write(b'x' * 1024 * 1024)\n"
        "        out.flush()\n"
        "        time.sleep(0.05)\n",
    )
    started = time.monotonic()
    result = await run(client, repo=True, timeout_s=60)
    assert (result["status"], result["error_code"]) == ("error", "DISK_LIMIT"), result
    assert time.monotonic() - started < 20  # stopped by the watchdog, not by the run's timeout
    assert list((settings.workspace_root / str(PROJECT) / ".kk-runs").iterdir()) == []
    assert await managed_containers() == []


async def test_hostile_artifact_tree_is_removed(client: httpx.AsyncClient, settings: Settings) -> None:
    """A locked folder and a 1500-deep tree in /workspace/.kk do not survive the run (security review F1)."""
    probe(
        task_workspace(settings),
        "import os\n"
        "os.makedirs('/workspace/.kk/locked/inner')\n"
        "open('/workspace/.kk/locked/inner/f', 'w').write('x')\n"
        "os.chmod('/workspace/.kk/locked/inner', 0)\n"
        "os.chmod('/workspace/.kk/locked', 0o500)\n"
        "os.mkdir('/workspace/.kk/deep'); os.chdir('/workspace/.kk/deep')\n"
        "for _ in range(1500):  # os.makedirs recurses\n"
        "    os.mkdir('d'); os.chdir('d')\n"
        "print('{}')\n",
    )
    result = await run(client)
    assert result["status"] == "succeeded", result
    assert list((settings.workspace_root / str(PROJECT) / ".kk-runs").iterdir()) == []


async def test_disk_helpers_as_sandbox_uid(settings: Settings) -> None:
    """The measurement and removal code itself, run as uid 10001 like the manager in compose."""
    await require_docker([settings.images["python"]])
    disk_py = REPO_ROOT / "sandbox_manager" / "sandbox_manager" / "disk.py"
    script = (
        "import importlib.util, json, os\n"
        "spec = importlib.util.spec_from_file_location('disk', '/kk/disk.py')\n"
        "disk = importlib.util.module_from_spec(spec); spec.loader.exec_module(disk)\n"
        "os.makedirs('/tmp/t/locked'); open('/tmp/t/locked/big', 'wb').write(b'x' * 5_000_000)\n"
        "os.chmod('/tmp/t/locked', 0)\n"
        "try:\n"
        "    disk.tree_bytes('/tmp/t'); measured = 'counted'\n"
        "except disk.Unmeasurable:\n"
        "    measured = 'unmeasurable'\n"
        "removed = disk.remove_tree('/tmp/t')\n"
        "print(json.dumps({'uid': os.getuid(), 'measured': measured, 'removed': removed,"
        " 'gone': not os.path.exists('/tmp/t')}))\n"
    )
    engine = DockerEngine(docker_host())
    spec = {
        "Image": settings.images["python"],
        "Cmd": ["python", "-c", script],
        "User": "10001:10001",
        "Labels": {"kk.managed": "1"},
        "HostConfig": {
            "NetworkMode": "none",
            "Mounts": [{"Type": "bind", "Source": str(disk_py), "Target": "/kk/disk.py", "ReadOnly": True}],
            "Tmpfs": {"/tmp": "rw,size=67108864,mode=1777"},
        },
    }
    try:
        container = await engine.create(spec, "kk-disk-uid-test")
        try:
            await engine.start(container)
            assert await engine.wait(container, 60) == 0
            output = b"".join([data async for _, data in engine.logs(container)])
        finally:
            await engine.remove(container)
    finally:
        await engine.aclose()
    assert json.loads(output) == {"uid": 10001, "measured": "unmeasurable", "removed": True, "gone": True}


async def test_container_log_cap_is_accepted_by_docker(settings: Settings, docker_settings: Settings) -> None:
    """Docker keeps stdout in its own log file; the spec's cap must be one the daemon applies."""
    await require_docker([settings.images["python"]])
    from sandbox_manager.hostconfig import build_container_spec
    from sandbox_manager.validation import RunRequest, validate

    task_workspace(settings)
    profiles = load_profiles(PROFILES_FILE, set(settings.images))
    body = {
        "project_id": str(PROJECT),
        "task_id": str(TASK),
        "workspace_subpath": f"{PROJECT}/tasks/{TASK}",
        "profile_id": "python.pytest",
    }
    spec = build_container_spec(
        validate(RunRequest.model_validate(body), profiles, docker_settings), "logcap", docker_settings
    )
    spec["HostConfig"]["Mounts"] = []  # only the log settings matter here
    engine = DockerEngine(docker_host())
    try:
        container = await engine.create(spec, "kk-logcap-test")
        try:
            state = await engine.inspect(container)
        finally:
            await engine.remove(container)
    finally:
        await engine.aclose()
    assert state is not None
    assert state["HostConfig"]["LogConfig"] == {
        "Type": "json-file",
        "Config": {"max-file": "2", "max-size": str(16 * 1024 * 1024)},
    }
