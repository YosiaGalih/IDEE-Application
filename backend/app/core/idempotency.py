"""Idempotency-Key for mutating requests (API_CONTRACTS §1): the first response is replayed for 24 h.

**Scope**
- Applies to POST/PUT/PATCH/DELETE outside ``/auth/`` that carry a key and the cookie of a session that is
  still valid. The session is validated before anything is stored or replayed, so a logged-out or revoked
  session gets the normal 401, never a replay.
- Everything else passes through untouched.

**Record**
- A key is scoped to the session id, method and path.
- The request fingerprint (query, content type, body) must match, otherwise 409.
- Status, content type, body and a small allowlist of safe headers are replayed; ``Set-Cookie`` never is.

**What is not stored**
- 5xx responses and transient rejections (401, 403, 408, 422, 425, 429) are not stored; the key is freed
  so the client can retry.
- A success whose body is too large to keep is recorded as "executed": a retry gets 409, never a second
  execution.

**Ownership**
- The pending marker carries an owner token and is refreshed while the request runs.
- Only its owner may finalize or release it (compare-and-set in Lua), so a late finisher cannot overwrite
  another request's record.

**Limits**
- Redis is the store (the contract fixes it). If the record cannot be written after the handler
  succeeded, the pending marker is left in place and the failure is logged at error level. Retries get 409
  until it expires; after that a retry may execute again.
- Without Redis a keyed request is refused (503).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.csrf import MUTATING_METHODS, SESSION_COOKIE
from app.core.errors import send_error
from app.core.logging import get_logger

HEADER = b"idempotency-key"
REPLAY_HEADER = (b"idempotent-replay", b"true")
TTL_SECONDS = 24 * 3600
PENDING_TTL_SECONDS = 300
PENDING_REFRESH_SECONDS = 60.0
MAX_STORED_BODY = 1024 * 1024
MAX_REQUEST_BODY = 10 * 1024 * 1024  # keyed requests are buffered to fingerprint them [Menebak]
# Rejections that say "try again later / differently": storing them would lock the key for a day.
_UNSTORED_STATUSES = frozenset({401, 403, 408, 422, 425, 429})
_REPLAYED_HEADERS = frozenset(
    {b"location", b"cache-control", b"retry-after", b"etag", b"content-disposition"}
)
_KEY_FORMAT = re.compile(r"[A-Za-z0-9_.:-]{1,128}")

# Owner-checked operations: the record (JSON) carries the owner token of the request that claimed it.
_OWNED = (
    "local cur = redis.call('GET', KEYS[1]) "
    "if not cur then return 0 end "
    "if cjson.decode(cur)['owner'] ~= ARGV[1] then return 0 end "
)
_FINALIZE = _OWNED + "redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3]) return 1"
_RELEASE = _OWNED + "redis.call('DEL', KEYS[1]) return 1"
_EXTEND = _OWNED + "redis.call('EXPIRE', KEYS[1], ARGV[2]) return 1"

logger = get_logger(__name__)


@dataclass(frozen=True)
class SessionIdentity:
    session_id: str
    user_id: str


SessionValidator = Callable[[str], Awaitable[SessionIdentity | None]]


class IdempotencyMiddleware:
    def __init__(
        self, app: ASGIApp, *, redis: Redis, session_validator: SessionValidator, exempt_prefix: str
    ) -> None:
        self.app = app
        self.redis = redis
        self.validate_session = session_validator
        self.exempt_prefix = exempt_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in MUTATING_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        raw_key = headers.get(HEADER)
        token = cookie_parser(headers.get(b"cookie", b"").decode("latin-1")).get(SESSION_COOKIE)
        if raw_key is None or not token or scope["path"].startswith(self.exempt_prefix):
            await self.app(scope, receive, send)
            return
        key = raw_key.decode("latin-1")
        if not _KEY_FORMAT.fullmatch(key):
            await send_error(
                scope, send, http_status=400, code="BAD_REQUEST", message="Invalid Idempotency-Key"
            )
            return
        if _declared_length(headers) > MAX_REQUEST_BODY:
            await _too_large(scope, send)
            return

        try:
            identity = await self.validate_session(token)
        except Exception as exc:
            logger.warning("idempotency_session_check_failed", error_type=type(exc).__name__)
            await _unavailable(scope, send)
            return
        if identity is None:
            # Not a live session: no storing, no replay; the route's own auth answers (401).
            await self.app(scope, receive, send)
            return

        body = await _read_body(receive)
        if body is None:  # client went away mid-body: nothing to execute, nothing to claim
            return
        if len(body) > MAX_REQUEST_BODY:
            await _too_large(scope, send)
            return

        fingerprint = hashlib.sha256(
            b"\n".join([scope.get("query_string", b""), headers.get(b"content-type", b""), body])
        ).hexdigest()
        scope_id = f"{identity.session_id}|{scope['method']}|{scope['path']}|{key}"
        redis_key = "kk:idem:" + hashlib.sha256(scope_id.encode()).hexdigest()
        owner = secrets.token_hex(16)
        pending = {"state": "pending", "owner": owner, "fingerprint": fingerprint, "user": identity.user_id}
        try:
            claimed = await self.redis.set(redis_key, json.dumps(pending), nx=True, ex=PENDING_TTL_SECONDS)
            existing = None if claimed else await self.redis.get(redis_key)
        except RedisError as exc:
            logger.warning("idempotency_store_unavailable", error_type=type(exc).__name__)
            await _unavailable(scope, send)
            return

        if not claimed:
            await self._answer_existing(scope, send, redis_key, existing, fingerprint, identity)
            return
        await self._execute(scope, receive, send, body, redis_key, owner, fingerprint, identity)

    async def _answer_existing(
        self, scope: Scope, send: Send, redis_key: str, raw: Any, fingerprint: str, identity: SessionIdentity
    ) -> None:
        try:
            record = json.loads(raw) if raw else {"state": "pending", "fingerprint": fingerprint}
        except ValueError:
            logger.error("idempotency_record_corrupt")
            with contextlib.suppress(RedisError):
                await self.redis.delete(redis_key)
            await _conflict(scope, send, "The stored Idempotency-Key record was unreadable; retry")
            return
        if record.get("fingerprint") != fingerprint or record.get("user") not in (None, identity.user_id):
            await _conflict(scope, send, "Idempotency-Key reused with a different request")
            return
        if record["state"] == "pending":
            await _conflict(scope, send, "A request with this Idempotency-Key is in progress")
            return
        if record["state"] == "executed":
            await _conflict(scope, send, "The request was executed; its response is not retained")
            return
        payload = record["body"].encode("latin-1")
        replay_headers = [(b"content-length", str(len(payload)).encode()), REPLAY_HEADER]
        replay_headers += [(k.encode("latin-1"), v.encode("latin-1")) for k, v in record.get("headers", [])]
        await send({"type": "http.response.start", "status": record["status"], "headers": replay_headers})
        await send({"type": "http.response.body", "body": payload})

    async def _execute(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        body: bytes,
        redis_key: str,
        owner: str,
        fingerprint: str,
        identity: SessionIdentity,
    ) -> None:
        status = 500
        kept_headers: list[list[str]] = []
        chunks: list[bytes] = []
        size = 0
        delivered = False

        async def replay_then_passthrough() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()  # disconnect detection keeps working (streaming responses)

        async def capture(message: Message) -> None:
            nonlocal status, size
            if message["type"] == "http.response.start":
                status = message["status"]
                for name, value in message.get("headers", []):
                    lowered = name.lower()
                    if lowered == b"content-type" or lowered in _REPLAYED_HEADERS:
                        kept_headers.append([lowered.decode("latin-1"), value.decode("latin-1")])
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                size += len(chunk)
                if size <= MAX_STORED_BODY:
                    chunks.append(chunk)
            await send(message)

        refresher = asyncio.create_task(self._keep_alive(redis_key, owner))
        handled = False
        try:
            await self.app(scope, replay_then_passthrough, capture)
            handled = True
        finally:
            refresher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresher
            if not handled or status >= 500 or status in _UNSTORED_STATUSES:
                await self._release(redis_key, owner)
        if status >= 500 or status in _UNSTORED_STATUSES:
            return

        record: dict[str, Any] = {"owner": owner, "fingerprint": fingerprint, "user": identity.user_id}
        if size <= MAX_STORED_BODY:
            record |= {
                "state": "done",
                "status": status,
                "headers": kept_headers,
                "body": b"".join(chunks).decode("latin-1"),
            }
        else:
            record |= {"state": "executed", "status": status}
        try:
            if not await self._finalize(redis_key, owner, record):
                logger.warning("idempotency_marker_lost", status=status)
        except RedisError as exc:
            # The client already has its response; leaving the pending marker means retries get 409 until it
            # expires instead of executing the mutation a second time right away.
            logger.error("idempotency_record_not_stored", status=status, error_type=type(exc).__name__)

    async def _finalize(self, redis_key: str, owner: str, record: dict[str, Any]) -> bool:
        stored = await self.redis.eval(_FINALIZE, 1, redis_key, owner, json.dumps(record), str(TTL_SECONDS))  # type: ignore[misc]
        return bool(stored)

    async def _release(self, redis_key: str, owner: str) -> None:
        try:
            await self.redis.eval(_RELEASE, 1, redis_key, owner)  # type: ignore[misc]
        except RedisError as exc:
            logger.warning("idempotency_release_failed", error_type=type(exc).__name__)

    async def _keep_alive(self, redis_key: str, owner: str) -> None:
        while True:
            await asyncio.sleep(PENDING_REFRESH_SECONDS)
            try:
                await self.redis.eval(_EXTEND, 1, redis_key, owner, str(PENDING_TTL_SECONDS))  # type: ignore[misc]
            except RedisError as exc:
                logger.warning("idempotency_refresh_failed", error_type=type(exc).__name__)


def _declared_length(headers: dict[bytes, bytes]) -> int:
    try:
        return int(headers.get(b"content-length", b"0"))
    except ValueError:
        return 0


async def _read_body(receive: Receive) -> bytes | None:
    """The whole body, or None if the client disconnected before sending all of it."""
    parts: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        parts.append(message.get("body", b""))
        size += len(parts[-1])
        if size > MAX_REQUEST_BODY or not message.get("more_body"):
            return b"".join(parts)


async def _conflict(scope: Scope, send: Send, message: str) -> None:
    await send_error(scope, send, http_status=409, code="CONFLICT", message=message)


async def _too_large(scope: Scope, send: Send) -> None:
    await send_error(scope, send, http_status=413, code="PAYLOAD_TOO_LARGE", message="Request body too large")


async def _unavailable(scope: Scope, send: Send) -> None:
    await send_error(
        scope,
        send,
        http_status=503,
        code="DEPENDENCY_UNAVAILABLE",
        message="A required dependency is unavailable",
    )
