"""Application factory (ARCHITECTURE §4.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.router import api_router
from app.core.audit import audit_guard_problems
from app.core.config import Settings, load_settings
from app.core.context import REQUEST_ID_HEADER, accept_request_id, request_id_var
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.redaction import Redactor
from app.services.health import Dependencies


class RequestIdMiddleware:
    """Assigns ``X-Request-ID`` (client value only if it matches the contract) to state, logs and response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = dict(scope.get("headers") or []).get(REQUEST_ID_HEADER.lower().encode())
        request_id = accept_request_id(raw.decode("latin-1") if raw else None)
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    redactor = Redactor(settings.secret_values())
    configure_logging(settings.log_level, redactor)
    dependencies = Dependencies.from_settings(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.is_production:
            # Fail closed: the audit trail is only append-only if this role cannot alter it (T10).
            async with dependencies.engine.connect() as conn:
                problems = await audit_guard_problems(conn)
            if problems:
                get_logger(__name__).error("audit_guard_failed", problems=problems)
                raise RuntimeError(
                    "audit_events is not protected from the runtime role: " + "; ".join(problems)
                )
        yield
        await dependencies.aclose()

    app = FastAPI(
        title="KodeKantor API",
        version="0.1.0",
        # Schema served outside production only (API_CONTRACTS does not publish it).
        openapi_url=None if settings.is_production else "/api/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.redactor = redactor
    app.state.dependencies = dependencies
    install_error_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    app.add_middleware(RequestIdMiddleware)
    return app
