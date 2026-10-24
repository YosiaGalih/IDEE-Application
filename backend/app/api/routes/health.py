"""``/health/live`` and ``/health/ready`` (FR-149, API_CONTRACTS §9). Public, no auth."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.context import REQUEST_ID_HEADER
from app.core.errors import envelope, error_response, request_id_of
from app.services.health import readiness

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live(request: Request) -> JSONResponse:
    request_id = request_id_of(request)
    body = envelope(request_id, status="completed", data={"status": "ok"})
    return JSONResponse(body, headers={REQUEST_ID_HEADER: request_id})


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    checks = await readiness(request.app.state.dependencies.readiness_checks())
    failed = [name for name, result in checks.items() if result != "ok"]
    if failed:
        return error_response(
            request,
            http_status=503,
            code="DEPENDENCY_UNAVAILABLE",
            message="A required dependency is unavailable",
            details=[{"loc": ["checks", name], "msg": "unavailable"} for name in failed],
            data={"status": "degraded", "checks": checks},
        )
    request_id = request_id_of(request)
    body = envelope(request_id, status="completed", data={"status": "ok", "checks": checks})
    return JSONResponse(body, headers={REQUEST_ID_HEADER: request_id})
