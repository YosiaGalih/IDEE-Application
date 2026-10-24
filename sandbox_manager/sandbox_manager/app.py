"""Sandbox manager HTTP API (API_CONTRACTS §10).

Internal only: reachable on the ``app`` network, Bearer auth.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sandbox_manager.config import Settings, check_process_user, load_settings
from sandbox_manager.profiles import Profile, load_profiles
from sandbox_manager.runs import Driver, DriverError, IdempotencyConflict, RunRegistry
from sandbox_manager.validation import RequestRejected, RunRequest, ValidatedRun, validate

REAP_INTERVAL_S = 60.0
log = logging.getLogger(__name__)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(
    settings: Settings | None = None,
    *,
    driver: Driver | None = None,
    profiles: dict[str, Profile] | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    profiles = (
        profiles if profiles is not None else load_profiles(settings.profiles_path, set(settings.images))
    )
    if driver is None:
        if settings.driver == "fake":
            from sandbox_manager.fake_driver import FakeDriver

            driver = FakeDriver()
        else:
            from sandbox_manager.docker_driver import DockerDriver, prepare_private_dirs

            check_process_user(settings)
            prepare_private_dirs(settings)
            driver = DockerDriver(settings)
    registry = RunRegistry(driver, settings)

    async def reaper() -> None:
        while True:
            await asyncio.sleep(REAP_INTERVAL_S)
            try:
                await driver.reap(registry.active_run_ids())
            except Exception:
                log.exception("reaper failed; retrying at the next interval")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await driver.cleanup_orphans()
        reaping = asyncio.create_task(reaper())
        try:
            yield
        finally:
            reaping.cancel()
            await asyncio.gather(reaping, return_exceptions=True)
            await registry.shutdown()

    app = FastAPI(
        title="kodekantor-sandbox-manager",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # nothing is served without the token
        lifespan=lifespan,
    )
    app.state.registry = registry

    def authorized(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, presented = (authorization or "").partition(" ")
        if scheme != "Bearer" or not hmac.compare_digest(presented.encode(), settings.token.encode()):
            raise HTTPException(status_code=401)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        codes = {401: "UNAUTHENTICATED", 404: "NOT_FOUND"}
        return _error(exc.status_code, codes.get(exc.status_code, "BAD_REQUEST"), "Request refused")

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, "VALIDATION_ERROR", "Input did not pass validation")

    @app.exception_handler(RequestRejected)
    async def rejected(_: Request, exc: RequestRejected) -> JSONResponse:
        return _error(422, exc.code, exc.message)

    @app.exception_handler(IdempotencyConflict)
    async def conflict(_: Request, exc: IdempotencyConflict) -> JSONResponse:
        return _error(422, "IDEMPOTENCY_CONFLICT", "client_request_id was used for a different run")

    @app.exception_handler(DriverError)
    async def driver_error(_: Request, exc: DriverError) -> JSONResponse:
        return _error(503, "DOCKER_UNAVAILABLE", "The container could not be reached")

    auth = [Depends(authorized)]

    @app.get("/healthz", dependencies=auth)
    async def healthz() -> dict[str, Any]:
        docker = await driver.health()
        images = await driver.images() if docker == "ok" else dict.fromkeys(settings.images, False)
        return {
            "status": "ok",
            "driver": driver.name,
            "docker": docker,
            "images": images,  # alias → present in the local cache (a run never pulls)
            "runtime": settings.runtime or "runc",
        }

    @app.get("/v1/profiles", dependencies=auth)
    async def list_profiles() -> list[dict[str, Any]]:
        return [
            {
                "profile_id": p.profile_id,
                "image": settings.images[p.image],
                "suite": p.suite,
                "timeout_s": p.timeout_s,
                "network": p.network,
                "args_pattern": p.args_pattern,
            }
            for p in profiles.values()
        ]

    def checked(body: RunRequest) -> ValidatedRun:
        """Validation and workspace checks (filesystem work: runs in a thread)."""
        run = validate(body, profiles, settings)
        if driver.name == "docker":  # refuse an unsafe workspace now, as a 422, not as a failed run
            from sandbox_manager.docker_driver import ensure_mountpoint
            from sandbox_manager.hostconfig import git_mount

            git_mount(run.workspace, settings)
            ensure_mountpoint(run.workspace)
        return run

    @app.post("/v1/runs", dependencies=auth)
    async def create_run(body: RunRequest) -> dict[str, Any]:
        try:
            run = await asyncio.to_thread(checked, body)
        except OSError as exc:
            raise RequestRejected("WORKSPACE_UNSAFE", "The workspace could not be inspected") from exc
        record = await registry.submit(run, wait=body.wait, client_request_id=body.client_request_id)
        if body.wait:
            return record.public()
        return {"run_id": record.run_id, "status": record.status, "timeout_s": record.timeout_s}

    def _record(run_id: str):  # type: ignore[no-untyped-def]
        record = registry.get(run_id)
        if record is None:
            raise HTTPException(status_code=404)
        return record

    @app.get("/v1/runs/{run_id}", dependencies=auth)
    async def get_run(run_id: str) -> dict[str, Any]:
        return _record(run_id).public()

    @app.get("/v1/runs/{run_id}/logs", dependencies=auth)
    async def run_logs(run_id: str, offset: int = 0) -> dict[str, Any]:
        return _record(run_id).logs_page(offset)

    @app.post("/v1/runs/{run_id}/kill", dependencies=auth)
    async def kill_run(run_id: str) -> dict[str, Any]:
        record = _record(run_id)
        await registry.kill(record)
        return {"status": record.status}

    @app.post("/v1/tasks/{task_id}/kill", dependencies=auth)
    async def kill_task(task_id: uuid.UUID) -> dict[str, Any]:
        killed, failed = await registry.kill_task(str(task_id))
        return {"killed": killed, "failed": failed}

    return app
