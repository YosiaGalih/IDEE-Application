"""CSRF double-submit for every mutating request (API_CONTRACTS §2, SECURITY_MODEL §8).

The middleware covers all routes, so a new route cannot forget it. It checks that the ``X-CSRF-Token``
header equals the ``kk_csrf`` cookie. For signed-in requests, ``api.deps.current_user`` additionally binds
the token to the session. A request without the cookie gets a pre-session token, so the public
password-reset forms can satisfy the check too.
"""

from __future__ import annotations

import hmac
import json
import secrets

from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import new_request_id
from app.core.errors import envelope

CSRF_COOKIE = "kk_csrf"
CSRF_HEADER = "x-csrf-token"
SESSION_COOKIE = "kk_session"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def tokens_match(expected: str | None, presented: str | None) -> bool:
    if not expected or not presented:
        return False
    return hmac.compare_digest(expected.encode(), presented.encode())


def csrf_cookie_header(value: str, *, secure: bool) -> str:
    """Host-only (no Domain), readable by the SPA (no HttpOnly), SameSite=Lax."""
    return f"{CSRF_COOKIE}={value}; Path=/; SameSite=lax" + ("; Secure" if secure else "")


class CsrfMiddleware:
    def __init__(self, app: ASGIApp, *, secure: bool, exempt: frozenset[tuple[str, str]]) -> None:
        self.app = app
        self.secure = secure
        self.exempt = exempt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        cookies = cookie_parser(headers.get(b"cookie", b"").decode("latin-1"))
        cookie_token = cookies.get(CSRF_COOKIE)
        issue = None if cookie_token else secrets.token_urlsafe(32)

        async def send_with_cookie(message: Message) -> None:
            if issue and message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                route_sets_it = any(
                    k.lower() == b"set-cookie" and v.startswith(f"{CSRF_COOKIE}=".encode())
                    for k, v in response_headers
                )
                if not route_sets_it:
                    response_headers.append(
                        (b"set-cookie", csrf_cookie_header(issue, secure=self.secure).encode())
                    )
                    message["headers"] = response_headers
            await send(message)

        method = scope["method"]
        if method in MUTATING_METHODS and (method, scope["path"]) not in self.exempt:
            presented = headers.get(CSRF_HEADER.encode(), b"").decode("latin-1")
            if not tokens_match(cookie_token, presented):
                await self._reject(scope, send_with_cookie)
                return
        await self.app(scope, receive, send_with_cookie)

    async def _reject(self, scope: Scope, send: Send) -> None:
        request_id = scope.get("state", {}).get("request_id") or new_request_id()
        body = envelope(
            request_id,
            status="failed",
            error={"code": "CSRF_FAILED", "message": "CSRF token missing or invalid", "details": []},
        )
        payload = json.dumps(body).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
