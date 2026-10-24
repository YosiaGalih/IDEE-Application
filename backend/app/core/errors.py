"""Response envelope and error mapping (API_CONTRACTS §1).

Every response body is ``{request_id, status, data, error, meta}``. Errors never carry stack traces, absolute
paths, exception messages or client input.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import REQUEST_ID_HEADER, new_request_id
from app.core.logging import get_logger

Status = Literal["completed", "accepted", "running", "failed"]

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    503: "DEPENDENCY_UNAVAILABLE",
}
_HTTP_MESSAGES = {
    400: "Bad request",
    401: "Authentication required",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    413: "Payload too large",
    422: "Input did not pass validation",
    429: "Too many requests",
    503: "A required dependency is unavailable",
}

logger = get_logger(__name__)


class AppError(Exception):
    """A failure the client is allowed to see: stable ``code``, safe ``message``, optional ``details``."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 400,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details
        self.headers = headers


def envelope(
    request_id: str,
    *,
    status: Status,
    data: Any = None,
    error: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"request_id": request_id, "status": status, "data": data, "error": error, "meta": meta}


def request_id_of(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = new_request_id()
        request.state.request_id = request_id
    return request_id


def error_response(
    request: Request,
    *,
    http_status: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    data: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_of(request)
    body = envelope(
        request_id,
        status="failed",
        data=data,
        error={"code": code, "message": message, "details": details or []},
    )
    return JSONResponse(
        body, status_code=http_status, headers={**(headers or {}), REQUEST_ID_HEADER: request_id}
    )


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.http_status >= 500:
        logger.error(
            "app_error", code=exc.code, http_status=exc.http_status, request_id=request_id_of(request)
        )
    return error_response(
        request,
        http_status=exc.http_status,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    status = exc.status_code
    code = _HTTP_CODES.get(status, "INTERNAL_ERROR" if status >= 500 else "BAD_REQUEST")
    message = _HTTP_MESSAGES.get(status, "Request failed")
    if status >= 500:
        logger.error("http_error", http_status=status, request_id=request_id_of(request))
    return error_response(
        request, http_status=status, code=code, message=message, headers=dict(exc.headers or {})
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        {"loc": list(error.get("loc", ())), "msg": str(error.get("msg", ""))} for error in exc.errors()
    ]
    return error_response(
        request, http_status=422, code="VALIDATION_ERROR", message=_HTTP_MESSAGES[422], details=details
    )


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    # Runs in Starlette's outermost error middleware, after the request-id context was reset.
    logger.error(
        "unhandled_exception", exc_info=exc, path=request.url.path, request_id=request_id_of(request)
    )
    return error_response(request, http_status=500, code="INTERNAL_ERROR", message="Internal error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled_error)


async def send_error(
    scope: Any,
    send: Any,
    *,
    http_status: int,
    code: str,
    message: str,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    """Write a failed envelope straight from ASGI middleware (outside the exception handlers)."""
    request_id = (scope.get("state") or {}).get("request_id") or new_request_id()
    body = envelope(request_id, status="failed", error={"code": code, "message": message, "details": []})
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": http_status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
