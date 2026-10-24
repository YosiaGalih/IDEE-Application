"""Docker driver: the Engine API over ``httpx`` (unix socket or the socket proxy; ADR-009).

**Endpoints used.** Only these, all allowed by the proxy flags `CONTAINERS`, `IMAGES`, `POST`, `PING`,
`VERSION` (ADR-026 (3)):
- `_ping`;
- `images/{ref}/json`;
- `containers/create|start|wait|logs|kill|json`, `DELETE containers/{id or name}`;
- `containers/json` (orphan sweep).

**Images.** An image is never pulled: a missing image ends the run with `IMAGE_MISSING` (ADR-026 (2)).

**Outcome.** A run's final status is written only after its container is removed and its artefact folder
is gone. Every way the evidence can be incomplete has its own error code or flag:
- `DISK_LIMIT`, `DISK_UNMEASURABLE`;
- `OOM_KILLED`;
- `ARTIFACT_MISSING`, `ARTIFACT_REJECTED`;
- `CONTAINER_NOT_REMOVED`;
- `log_incomplete`.

**Containers the manager lost.**
- One manager per Docker daemon (tier S, one node). At start it removes every container labelled
  `kk.managed=1` and every leftover run folder.
- While running, a reaper removes, every `REAP_INTERVAL_S`, this instance's containers that belong to no
  active run (e.g. after `CONTAINER_NOT_REMOVED`).

**Artefact folder watchdog.** During a run the run's artefact folder is measured every `WATCH_INTERVAL_S`.
Past `MAX_ARTIFACT_DIR_BYTES` the container is killed and the run ends `DISK_LIMIT`. The workspace itself is
bounded by the per-project quota in production (ARCHITECTURE §8) and measured after the run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from sandbox_manager.config import Settings
from sandbox_manager.disk import Unmeasurable, remove_tree, tree_bytes
from sandbox_manager.hostconfig import (
    RUNS_DIR,
    SANDBOX_UID,
    build_container_spec,
    empty_file,
    run_artifacts_dir,
)
from sandbox_manager.runs import DriverError, RunRecord, RunStatus
from sandbox_manager.validation import RequestRejected, ValidatedRun

ARTIFACT_FILES = {"junit": "junit.xml", "coverage": "coverage.xml"}
MAX_ARTIFACT_BYTES = 1024 * 1024  # [Menebak]; same as the backend parser's MAX_XML_BYTES
MAX_ARTIFACT_DIR_BYTES = 8 * 1024 * 1024  # [Menebak] junit + coverage (1 MiB each) with ample room
REMOVE_ATTEMPTS = 3
WATCH_INTERVAL_S = 1.0
REAP_INTERVAL_S = 60.0
_STREAMS = {1: "stdout", 2: "stderr"}
_UUID_DIR = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
log = logging.getLogger(__name__)
Outcome = tuple[RunStatus, int | None, str | None]  # status, exit code, error code


class DockerUnavailable(Exception):
    """The daemon (or the proxy) could not be reached."""


class DockerError(Exception):
    """The daemon answered with an error status; ``message`` is its own text, shortened."""

    def __init__(self, what: str, status: int, message: str) -> None:
        super().__init__(f"{what}: {status} {message}")
        self.what = what
        self.status = status
        self.message = message


def _check(response: httpx.Response, what: str, *ok: int) -> httpx.Response:
    if response.status_code in ok or (not ok and response.is_success):
        return response
    try:
        message = str(response.json().get("message", ""))[:200]
    except (ValueError, AttributeError):
        message = response.text[:200]
    raise DockerError(what, response.status_code, message)


class DockerEngine:
    """A minimal async Docker Engine API client."""

    def __init__(self, docker_host: str, *, timeout_s: float = 30.0) -> None:
        if docker_host.startswith("unix://"):
            transport = httpx.AsyncHTTPTransport(uds=docker_host.removeprefix("unix://"))
            base_url = "http://docker"
        elif docker_host.startswith("tcp://"):
            transport = httpx.AsyncHTTPTransport()
            base_url = "http://" + docker_host.removeprefix("tcp://")
        else:
            raise ValueError("DOCKER_HOST must be unix:// or tcp://")
        self.client = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=timeout_s)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise DockerUnavailable(type(exc).__name__) from exc

    async def ping(self) -> bool:
        try:
            return (await self._request("GET", "/_ping")).status_code == 200
        except DockerUnavailable:
            return False

    async def image_exists(self, ref: str) -> bool:
        response = await self._request("GET", f"/images/{quote(ref, safe='')}/json")
        if response.status_code == 404:
            return False
        _check(response, "image")
        return True

    async def create(self, spec: dict[str, Any], name: str) -> str:
        response = await self._request("POST", "/containers/create", params={"name": name}, json=spec)
        return str(_check(response, "create").json()["Id"])

    async def start(self, container_id: str) -> None:
        _check(await self._request("POST", f"/containers/{container_id}/start"), "start")

    async def wait(self, container_id: str, timeout_s: float) -> int:
        response = await self._request(
            "POST", f"/containers/{container_id}/wait", timeout=httpx.Timeout(timeout_s, connect=10.0)
        )
        return int(_check(response, "wait").json()["StatusCode"])

    async def logs(self, container_id: str) -> AsyncIterator[tuple[str, bytes]]:
        """Demultiplexed stdout/stderr frames until the container stops."""
        params = {"follow": "1", "stdout": "1", "stderr": "1"}
        try:
            async with self.client.stream(
                "GET", f"/containers/{container_id}/logs", params=params, timeout=None
            ) as response:
                if not response.is_success:
                    raise DockerError("logs", response.status_code, "")
                buffer = b""
                async for chunk in response.aiter_bytes():
                    buffer += chunk
                    while len(buffer) >= 8:
                        size = int.from_bytes(buffer[4:8], "big")
                        if len(buffer) < 8 + size:
                            break
                        stream = _STREAMS.get(buffer[0], "stdout")
                        yield stream, buffer[8 : 8 + size]
                        buffer = buffer[8 + size :]
        except httpx.TransportError as exc:
            raise DockerUnavailable(type(exc).__name__) from exc

    async def inspect(self, container_id: str) -> dict[str, Any] | None:
        response = await self._request("GET", f"/containers/{container_id}/json")
        if response.status_code != 200:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None

    async def kill(self, container_id: str) -> None:
        response = await self._request("POST", f"/containers/{container_id}/kill")
        _check(response, "kill", 204, 404, 409)  # already gone or not running: fine

    async def remove(self, container: str) -> None:
        """Remove by id or name, with its anonymous volumes; already gone is fine."""
        response = await self._request("DELETE", f"/containers/{container}", params={"force": "1", "v": "1"})
        _check(response, "remove", 204, 404)

    async def list_containers(self, labels: list[str]) -> list[dict[str, Any]]:
        filters = json.dumps({"label": labels})
        response = await self._request("GET", "/containers/json", params={"all": "1", "filters": filters})
        return list(_check(response, "list").json())

    async def managed_containers(self) -> list[str]:
        return [str(item["Id"]) for item in await self.list_containers(["kk.managed=1"])]


def _read_artifact(folder: Path, name: str) -> tuple[str | None, str]:
    """A regular file directly in the run's artefact folder, never through a link, ≤ ``MAX_ARTIFACT_BYTES``.

    Returns the text and why it was or was not read: ok, missing, not_regular, too_large, unreadable.
    """
    try:
        fd = os.open(folder / name, _READ_FLAGS)
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, "not_regular" if exc.errno == 40 else "unreadable"  # ELOOP: a symlink
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, "not_regular"
        data = b""
        while chunk := os.read(fd, MAX_ARTIFACT_BYTES + 1 - len(data)):
            data += chunk
            if len(data) > MAX_ARTIFACT_BYTES:
                return None, "too_large"
        return data.decode("utf-8", "replace"), "ok"
    except OSError:
        return None, "unreadable"
    finally:
        os.close(fd)


def prepare_artifacts(settings: Settings, project_id: object, run_id: str) -> Path:
    """``<project>/.kk-runs/<run_id>``, fresh, created without following links; writable by the sandbox user.

    In compose the manager runs as the sandbox user (uid 10001, like api/worker), so 0700 is enough.
    """
    project = os.open(settings.workspace_root / str(project_id), _DIR_FLAGS)
    try:
        try:
            os.mkdir(RUNS_DIR, 0o700, dir_fd=project)
        except FileExistsError:
            pass
        runs = os.open(RUNS_DIR, _DIR_FLAGS, dir_fd=project)  # ELOOP/ENOTDIR if not a real folder
    except OSError as exc:
        raise RequestRejected("WORKSPACE_UNSAFE", "The project run folder is not a real folder") from exc
    finally:
        os.close(project)
    try:
        os.mkdir(run_id, 0o700, dir_fd=runs)  # fails if it exists: a run id is never reused
        uid, gid = (int(part) for part in SANDBOX_UID.split(":"))
        if os.geteuid() == 0:
            os.chown(run_id, uid, gid, dir_fd=runs, follow_symlinks=False)
        elif os.geteuid() != uid:
            os.chmod(run_id, 0o777, dir_fd=runs)  # noqa: S103 - [Menebak] only where the manager is another uid
    finally:
        os.close(runs)
    return run_artifacts_dir(settings, project_id, run_id)


def sweep_run_folders(settings: Settings) -> int:
    """Remove every ``<project>/.kk-runs/*`` left by a previous manager process."""
    swept = 0
    try:
        projects = [e for e in os.scandir(settings.workspace_root) if _UUID_DIR.fullmatch(e.name)]
    except FileNotFoundError:
        return 0
    for project in projects:
        runs = Path(project.path) / RUNS_DIR
        if not project.is_dir(follow_symlinks=False) or not runs.is_dir() or runs.is_symlink():
            continue
        for entry in os.scandir(runs):
            remove_tree(Path(entry.path))
            swept += 1
    return swept


def prepare_private_dirs(settings: Settings) -> None:
    settings.private_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    empty = empty_file(settings)
    if not empty.exists():
        empty.write_bytes(b"")
    os.chmod(empty, 0o444)


def ensure_mountpoint(workspace: Path) -> None:
    """``<workspace>/.kk`` must exist as a real folder for the artefact mount (needed for read-only binds).

    It stays empty on the host (the run's folder is mounted over it), so git never sees it.
    """
    target = workspace / ".kk"
    try:
        mode = os.lstat(target).st_mode
    except FileNotFoundError:
        try:
            os.mkdir(target, 0o755)  # never follows a link: mkdir fails if the name exists
        except OSError as exc:
            raise RequestRejected("WORKSPACE_UNSAFE", "The workspace .kk cannot be created") from exc
        return
    if not stat.S_ISDIR(mode):
        raise RequestRejected("WORKSPACE_UNSAFE", "The workspace .kk is not a folder")


class DockerDriver:
    name = "docker"

    def __init__(self, settings: Settings, engine: DockerEngine | None = None) -> None:
        self.settings = settings
        self.engine = engine or DockerEngine(settings.docker_host)
        self.instance = secrets.token_hex(6)  # label kk.manager: the reaper only touches its own containers

    async def health(self) -> str:
        return "ok" if await self.engine.ping() else "unavailable"

    async def images(self) -> dict[str, bool]:
        """Whether each configured image is in the local cache (a run never pulls)."""
        present: dict[str, bool] = {}
        for alias, ref in self.settings.images.items():
            try:
                present[alias] = await self.engine.image_exists(ref)
            except (DockerUnavailable, DockerError):
                present[alias] = False
        return present

    async def cleanup_orphans(self) -> int:
        removed = 0
        for container_id in await self.engine.managed_containers():
            await self.engine.remove(container_id)
            removed += 1
        swept = await asyncio.to_thread(sweep_run_folders, self.settings)
        if removed or swept:
            log.warning("start: removed %d leftover containers and %d run folders", removed, swept)
        return removed

    async def reap(self, active_run_ids: set[str]) -> int:
        """Remove this instance's containers that belong to no active run."""
        reaped = 0
        for item in await self.engine.list_containers(["kk.managed=1", f"kk.manager={self.instance}"]):
            if (item.get("Labels") or {}).get("kk.run_id") not in active_run_ids:
                await self.engine.remove(str(item["Id"]))
                reaped += 1
        if reaped:
            log.warning("reaper: removed %d containers without an active run", reaped)
        return reaped

    async def kill(self, record: RunRecord) -> None:
        if record.container_id:
            try:
                await self.engine.kill(record.container_id)
            except (DockerUnavailable, DockerError) as exc:
                raise DriverError(str(exc)) from exc

    async def execute(self, run: ValidatedRun, record: RunRecord) -> None:
        """The final status is written only after the container is removed and the artefacts are read.

        A caller that sees a final status can rely on nothing still writing into the workspace; when that
        cannot be confirmed the run ends ``error`` with ``CONTAINER_NOT_REMOVED``.
        """
        image = self.settings.images[run.profile.image]
        record.image = image
        artifacts = run_artifacts_dir(self.settings, run.project_id, record.run_id)
        outcome: Outcome = ("error", None, "MANAGER_ERROR")
        try:
            outcome = await self._run_container(run, record, image)
        except RequestRejected as exc:  # the workspace changed between validation and the run
            outcome = ("error", None, exc.code)
        except DockerUnavailable as exc:
            log.warning("run %s: docker unreachable (%s)", record.run_id, exc)
            outcome = ("error", None, "DOCKER_UNAVAILABLE")
        except DockerError as exc:
            log.warning("run %s: docker refused %s: %s %s", record.run_id, exc.what, exc.status, exc.message)
            outcome = ("error", None, "DOCKER_ERROR")
        finally:
            if await self._remove_container(record):
                await asyncio.to_thread(remove_tree, artifacts)
            else:  # it may still write there: leave the folder to the start-up sweep, never race it
                outcome = ("error", outcome[1], "CONTAINER_NOT_REMOVED")
        status, exit_code, error_code = outcome
        record.finish(status, exit_code=exit_code, error_code=error_code)

    async def _remove_container(self, record: RunRecord) -> bool:
        target = record.container_id or record.container_name  # by name when create's answer was lost
        if target is None:
            return True
        for attempt in range(REMOVE_ATTEMPTS):
            try:
                await self.engine.remove(target)
                return True
            except (DockerUnavailable, DockerError) as exc:
                log.error(
                    "run %s: removing container failed (attempt %d): %s", record.run_id, attempt + 1, exc
                )
                await asyncio.sleep(attempt + 1)
        return False  # swept at the next start (cleanup_orphans)

    async def _run_container(self, run: ValidatedRun, record: RunRecord, image: str) -> Outcome:
        if not await self.engine.image_exists(image):
            return ("error", None, "IMAGE_MISSING")
        await asyncio.to_thread(ensure_mountpoint, run.workspace)
        artifacts = await asyncio.to_thread(prepare_artifacts, self.settings, run.project_id, record.run_id)
        try:
            before = None if run.read_only else await asyncio.to_thread(tree_bytes, run.workspace)
        except Unmeasurable:
            return ("error", None, "WORKSPACE_UNSAFE")  # a folder the manager cannot read
        spec = build_container_spec(run, record.run_id, self.settings)
        spec["Labels"]["kk.manager"] = self.instance
        if record.kill_requested:
            return ("killed", None, None)
        record.container_name = f"kk-run-{record.run_id}"
        record.container_id = await self.engine.create(spec, record.container_name)
        await self.engine.start(record.container_id)
        record.mark_started()
        if record.kill_requested:  # asked while the container was being created: driver.kill found no id
            await self.engine.kill(record.container_id)
        breach: list[str] = []  # written by the watchdog before it kills, so a cancel cannot lose it
        watchdog = asyncio.create_task(self._watch(record, record.container_id, artifacts, breach))
        try:
            status, exit_code = await self._supervise(record, record.container_id, run.timeout_s)
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
        state = await self.engine.inspect(record.container_id) or {}
        record.image_digest = state.get("Image")
        oom_killed = bool((state.get("State") or {}).get("OOMKilled"))
        if breach:  # the watchdog killed the container
            return ("error", exit_code, breach[0])
        await asyncio.to_thread(self._collect_artifacts, record, artifacts)
        try:
            grown = await asyncio.to_thread(tree_bytes, artifacts)  # read-only runs can still write here
            if before is not None:
                grown += await asyncio.to_thread(tree_bytes, run.workspace) - before
        except Unmeasurable:
            return ("error", exit_code, "DISK_UNMEASURABLE")
        if grown > self.settings.max_workspace_growth_mb * 1024 * 1024:
            return ("error", exit_code, "DISK_LIMIT")
        if oom_killed and status not in ("timeout", "killed"):
            return ("error", exit_code, "OOM_KILLED")
        if status == "succeeded":
            for name in run.profile.artifacts:
                state_of = record.artifact_status.get(name, "missing")
                if state_of != "ok":
                    code = "ARTIFACT_MISSING" if state_of == "missing" else "ARTIFACT_REJECTED"
                    return ("error", exit_code, code)
        return (status, exit_code, None)

    async def _watch(self, record: RunRecord, container_id: str, artifacts: Path, breach: list[str]) -> None:
        """Kill the container once its artefact folder outgrows its bound; the code goes to ``breach``."""
        while True:
            await asyncio.sleep(WATCH_INTERVAL_S)
            try:
                size = await asyncio.to_thread(tree_bytes, artifacts)
            except Unmeasurable:
                code = "DISK_UNMEASURABLE"
            else:
                if size <= MAX_ARTIFACT_DIR_BYTES:
                    continue
                code = "DISK_LIMIT"
            log.warning("run %s: artefact folder %s, killing the container", record.run_id, code)
            breach.append(code)
            await self.engine.kill(container_id)
            return

    @staticmethod
    def _collect_artifacts(record: RunRecord, folder: Path) -> None:
        for key, name in ARTIFACT_FILES.items():
            text, state = _read_artifact(folder, name)
            record.artifact_status[key] = state
            if text is not None:
                record.artifacts[key] = text

    async def _supervise(
        self, record: RunRecord, container_id: str, timeout_s: int
    ) -> tuple[RunStatus, int | None]:
        async def pump() -> None:
            async for stream, data in self.engine.logs(container_id):
                record.append(stream, data)

        reader = asyncio.create_task(pump())
        try:
            try:
                exit_code: int | None = await asyncio.wait_for(
                    self.engine.wait(container_id, timeout_s + 5), timeout_s
                )
            except TimeoutError:
                await self.engine.kill(container_id)
                try:
                    exit_code = await asyncio.wait_for(self.engine.wait(container_id, 30), 30)
                except TimeoutError:
                    exit_code = None  # removal (force) follows; failing that, CONTAINER_NOT_REMOVED
                await _drain(reader, record)
                return "timeout", exit_code
            await _drain(reader, record)
            if record.kill_requested and exit_code != 0:  # exit 0 means it finished before the kill
                return "killed", exit_code
            return ("succeeded" if exit_code == 0 else "failed"), exit_code
        finally:
            if not reader.done():
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)


async def _drain(reader: asyncio.Task[None], record: RunRecord) -> None:
    """Let the log stream finish; a stream that broke or did not end marks the stored log incomplete."""
    try:
        await asyncio.wait_for(reader, 10)  # cancels the reader on timeout
    except (TimeoutError, DockerUnavailable, DockerError) as exc:
        log.warning("run %s: log stream incomplete (%s)", record.run_id, type(exc).__name__)
        record.log_incomplete = True
