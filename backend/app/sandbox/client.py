"""Client for the sandbox manager (API_CONTRACTS §10, ARCHITECTURE §4.2).

**Contract.**
- ``HttpSandboxClient`` is the only way the backend runs project code (CLAUDE.md #3).
- A run is submitted, then followed through ``/logs`` until the manager reports a final status. This feeds
  ``on_log`` while the run is going.
- The result is the manager's record. A status the manager did not report is never invented here.

**Errors.**
- 401/403 → ``SandboxAuthError``.
- 422 → ``SandboxRequestError``, carrying the manager's code (``INVALID_ARGS``, ``INVALID_WORKSPACE``, ...).
- Network failures, 5xx, a lost run, an unreadable or incoherent response → ``SandboxUnavailable``.
- No message carries the token.

A run that started but could not finish is a *result*, not an exception: ``status="error"`` with the
manager's ``error_code`` (``IMAGE_MISSING``, ``DISK_LIMIT``, ``OOM_KILLED``, ``ARTIFACT_MISSING``,
``CONTAINER_NOT_REMOVED``, ...). Callers must check it.

**A run is never left behind.** If following it fails (network, cancellation, an ``on_log`` error), the
client kills it before re-raising. Retried submissions carry ``client_request_id`` so the manager starts the
run once.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.logging import get_logger

Network = Literal["none", "egress"]
RunStatus = Literal["succeeded", "failed", "timeout", "killed", "error"]
LogCallback = Callable[[str, str], Awaitable[None]]  # (stream, text)

POLL_INTERVAL_S = 0.5  # [Menebak]
DEADLINE_SLACK_S = (
    60  # once running, the manager ends the run by its own timeout; this guards a stuck manager
)
QUEUE_LIMIT_S = 3600  # [Menebak] waiting for a free slot (SANDBOX_MAX_CONCURRENT_RUNS) longer than this
logger = get_logger(__name__)


class SandboxError(Exception):
    code = "SANDBOX_ERROR"


class SandboxAuthError(SandboxError):
    code = "SANDBOX_AUTH"


class SandboxRequestError(SandboxError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SandboxUnavailable(SandboxError):
    code = "SANDBOX_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class SandboxRunRequest:
    project_id: UUID
    task_id: UUID | None
    workspace_subpath: str  # '<project>/tasks/<task>' | '<project>/repo'
    profile_id: str
    args: list[str] = field(default_factory=list)
    timeout_s: int | None = None
    network: Network = "none"
    read_only: bool = False  # ADR-033; the manager forces it for '<project>/repo'
    client_request_id: str | None = None  # same id on a retry → the manager returns the same run


@dataclass(frozen=True, slots=True)
class SandboxRunResult:
    run_id: str
    status: RunStatus
    exit_code: int | None
    stdout: str  # tail, at most 64 KiB
    stderr: str
    truncated: bool
    duration_ms: int | None
    image: str | None
    image_digest: str | None
    artifacts: dict[str, str]  # {"junit": "<xml>", "coverage": "<xml>"} read from /workspace/.kk/
    error_code: str | None = None
    artifact_status: dict[str, str] = field(default_factory=dict)  # ok|missing|too_large|not_regular|...
    log_incomplete: bool = False  # the stored log may be missing output (stream broke)


class SandboxClient(Protocol):
    async def run(self, req: SandboxRunRequest, *, on_log: LogCallback | None = None) -> SandboxRunResult: ...

    async def kill(self, run_id: str) -> None: ...

    async def kill_task(self, task_id: UUID) -> int: ...

    async def health(self) -> bool: ...


class _Run(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    status: Literal["queued", "running", "succeeded", "failed", "timeout", "killed", "error"]
    exit_code: int | None = None
    duration_ms: int | None = None
    image: str | None = None
    image_digest: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    truncated: bool = False
    artifacts: dict[str, str] = {}
    artifact_status: dict[str, str] = {}
    log_incomplete: bool = False
    error_code: str | None = None


class _Chunk(BaseModel):
    stream: str
    data: str


class _Logs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    next_offset: int
    chunks: list[_Chunk]
    status: str
    done: bool


class _Started(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    timeout_s: int | None = None


class HttpSandboxClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        default_timeout_s: int = 300,
        poll_interval_s: float = POLL_INTERVAL_S,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_fake_driver: bool = False,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0, connect=5.0),
            transport=transport,
        )
        self._default_timeout_s = default_timeout_s
        self._poll_interval_s = poll_interval_s
        self._allow_fake_driver = allow_fake_driver  # production refuses a manager on the fake driver

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SandboxUnavailable(f"sandbox manager unreachable ({type(exc).__name__})") from exc
        if response.status_code in (401, 403):
            raise SandboxAuthError("sandbox manager refused the token")
        if response.status_code == 404:
            raise SandboxUnavailable("sandbox manager does not know this run (restarted?)")
        if response.status_code == 422:
            error = _error_body(response)
            code, message = error.get("code", "BAD_REQUEST"), error.get("message", "Request refused")
            raise SandboxRequestError(code, message)
        if response.status_code != 200:
            raise SandboxUnavailable(f"sandbox manager answered {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SandboxUnavailable("sandbox manager sent an unreadable response") from exc

    async def run(self, req: SandboxRunRequest, *, on_log: LogCallback | None = None) -> SandboxRunResult:
        body = {
            "project_id": str(req.project_id),
            "task_id": str(req.task_id) if req.task_id else None,
            "workspace_subpath": req.workspace_subpath,
            "profile_id": req.profile_id,
            "args": list(req.args),
            "timeout_s": req.timeout_s,
            "network": req.network,
            "read_only": req.read_only,
            "client_request_id": req.client_request_id,
            "wait": False,
        }
        started = _model(_Started, await self._call("POST", "/v1/runs", json=body))
        try:
            await self._follow(started, req, on_log)
            record = _model(_Run, await self._call("GET", f"/v1/runs/{started.run_id}"))
        except BaseException:
            await self._kill_quietly(started.run_id)
            raise
        return _result(started.run_id, record)

    async def _follow(self, started: _Started, req: SandboxRunRequest, on_log: LogCallback | None) -> None:
        """Poll ``/logs`` until done. The deadline counts from the moment the run left the queue."""
        timeout_s = started.timeout_s or req.timeout_s or self._default_timeout_s
        queued_until = time.monotonic() + QUEUE_LIMIT_S
        deadline: float | None = None
        offset = 0
        while True:
            page = await self._call("GET", f"/v1/runs/{started.run_id}/logs", params={"offset": offset})
            logs = _model(_Logs, page)
            offset = logs.next_offset
            if on_log is not None:
                for chunk in logs.chunks:
                    await on_log(chunk.stream, chunk.data)
            if logs.done:
                return
            now = time.monotonic()
            if logs.status == "queued":
                if now > queued_until:
                    raise SandboxUnavailable("sandbox run waited too long for a free slot")
            else:
                deadline = deadline or now + timeout_s + DEADLINE_SLACK_S
                if now > deadline:
                    raise SandboxUnavailable("sandbox run did not finish in time")
            await asyncio.sleep(self._poll_interval_s)

    async def _kill_quietly(self, run_id: str) -> None:
        """Best effort, even while the caller is being cancelled: a run must not outlive its caller."""
        with contextlib.suppress(SandboxError, asyncio.CancelledError):
            await asyncio.shield(self.kill(run_id))

    async def kill(self, run_id: str) -> None:
        await self._call("POST", f"/v1/runs/{run_id}/kill")

    async def kill_task(self, task_id: UUID) -> int:
        """Number of runs killed; ``SandboxUnavailable`` if any active run of the task could not be."""
        data = await self._call("POST", f"/v1/tasks/{task_id}/kill")
        killed = data.get("killed") if isinstance(data, dict) else None
        failed = data.get("failed", 0) if isinstance(data, dict) else None
        if not isinstance(killed, int) or not isinstance(failed, int):
            raise SandboxUnavailable("sandbox manager sent an unreadable response")
        if failed:
            raise SandboxUnavailable(f"{failed} sandbox runs of the task could not be killed")
        return killed

    async def health(self) -> bool:
        """True only when the manager can run something: Docker reachable, every image present.

        A manager on the fake driver counts as down unless ``allow_fake_driver``. The reason for a
        ``False`` is logged (never the token).
        """
        try:
            data = await self._call("GET", "/healthz")
        except SandboxError as exc:
            logger.warning("sandbox_health_failed", reason=type(exc).__name__, code=exc.code)
            return False
        reason = _unhealthy(data, allow_fake_driver=self._allow_fake_driver)
        if reason is not None:
            logger.warning("sandbox_health_failed", reason=reason)
            return False
        return True


def _unhealthy(data: Any, *, allow_fake_driver: bool) -> str | None:
    if not isinstance(data, dict) or data.get("status") != "ok":
        return "bad_response"
    if data.get("docker") != "ok":
        return "docker_unavailable"
    images = data.get("images")
    if not isinstance(images, dict) or not images or not all(v is True for v in images.values()):
        return "image_missing"
    if data.get("driver") != "docker" and not allow_fake_driver:
        return "fake_driver"
    return None


def _result(run_id: str, record: _Run) -> SandboxRunResult:
    """The manager's record, only when it is coherent: a status is never taken on trust alone."""
    if record.run_id != run_id:
        raise SandboxUnavailable("sandbox manager answered for another run")
    if record.status in ("queued", "running"):
        raise SandboxUnavailable("sandbox manager reported done without a final status")
    if record.status == "succeeded" and record.exit_code != 0:
        raise SandboxUnavailable("sandbox manager reported success without exit code 0")
    if record.status == "failed" and record.exit_code in (None, 0):
        raise SandboxUnavailable("sandbox manager reported a failure without a failing exit code")
    return SandboxRunResult(
        run_id=record.run_id,
        status=record.status,
        exit_code=record.exit_code,
        stdout=record.stdout_tail,
        stderr=record.stderr_tail,
        truncated=record.truncated,
        duration_ms=record.duration_ms,
        image=record.image,
        image_digest=record.image_digest,
        artifacts=dict(record.artifacts),
        error_code=record.error_code,
        artifact_status=dict(record.artifact_status),
        log_incomplete=record.log_incomplete,
    )


def _error_body(response: httpx.Response) -> dict[str, str]:
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return {}
    if not isinstance(error, dict):
        return {}
    return {k: v for k, v in error.items() if k in ("code", "message") and isinstance(v, str)}


def _model[M: BaseModel](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise SandboxUnavailable("sandbox manager sent an unreadable response") from exc


@dataclass
class FakeSandboxClient:
    """Programmed results for tests (TEST_STRATEGY: no real sandbox in unit/integration tests)."""

    results: dict[str, SandboxRunResult] = field(default_factory=dict)  # profile_id → result
    default: SandboxRunResult | None = None  # for unlisted profiles; unset → an explicit error result
    logs: list[tuple[str, str]] = field(default_factory=list)
    requests: list[SandboxRunRequest] = field(default_factory=list)
    killed_runs: list[str] = field(default_factory=list)
    killed_tasks: list[UUID] = field(default_factory=list)
    healthy: bool = True

    async def run(self, req: SandboxRunRequest, *, on_log: LogCallback | None = None) -> SandboxRunResult:
        self.requests.append(req)
        if on_log is not None:
            for stream, text in self.logs:
                await on_log(stream, text)
        if req.profile_id in self.results:
            return self.results[req.profile_id]
        if self.default is not None:
            return self.default
        return SandboxRunResult(  # a test that did not program the fake never gets a silent success
            run_id=f"fake-{len(self.requests)}",
            status="error",
            exit_code=None,
            stdout="",
            stderr="",
            truncated=False,
            duration_ms=1,
            image="fake/image:1",
            image_digest=None,
            artifacts={},
            error_code="FAKE_NOT_PROGRAMMED",
        )

    async def kill(self, run_id: str) -> None:
        self.killed_runs.append(run_id)

    async def kill_task(self, task_id: UUID) -> int:
        self.killed_tasks.append(task_id)
        return 0

    async def health(self) -> bool:
        return self.healthy
