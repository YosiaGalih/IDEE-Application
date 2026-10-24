"""Idempotency-Key for mutating requests (API_CONTRACTS §1): the first response is replayed for 24 h.

- **Scope.** A key belongs to one session (hash of the ``kk_session`` token) plus method and path. A
  response is never replayed to another session or user.
- **What is replayed.** Only status, content type and body. ``Set-Cookie`` is never stored.
- **What is not stored.** 5xx responses and bodies over 1 MiB; the key is released so the client can
  retry.
- **Conflicts.** The same key with a different body, or while the first request is still running, is
  409 CONFLICT.
- **Fail closed.** Without Redis a keyed request is refused (503) rather than possibly executed twice.
- **Scope of the middleware.** Requests with no key, no session, a safe method, or an ``/auth/`` path
  pass through untouched.
"""

from __future__ import annotations

import hashlib
import json
import re
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
# A crashed request must not pin its key for a day; pending markers expire sooner [Menebak].
PENDING_TTL_SECONDS = 300
MAX_STORED_BODY = 1024 * 1024
_KEY_FORMAT = re.compile(r"[A-Za-z0-9_.:-]{1,128}")

logger = get_logger(__name__)


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, *, redis: Redis, exempt_prefix: str) -> None:
        self.app = app
        self.redis = redis
        self.exempt_prefix = exempt_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in MUTATING_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        raw_key = headers.get(HEADER)
        session = cookie_parser(headers.get(b"cookie", b"").decode("latin-1")).get(SESSION_COOKIE)
        if raw_key is None or not session or scope["path"].startswith(self.exempt_prefix):
            await self.app(scope, receive, send)
            return
        key = raw_key.decode("latin-1")
        if not _KEY_FORMAT.fullmatch(key):
            await send_error(
                scope, send, http_status=400, code="BAD_REQUEST", message="Invalid Idempotency-Key"
            )
            return

        body = await _read_body(receive)
        body_hash = hashlib.sha256(body).hexdigest()
        session_hash = hashlib.sha256(session.encode()).hexdigest()
        scope_id = f"{session_hash}|{scope['method']}|{scope['path']}|{key}"
        redis_key = "kk:idem:" + hashlib.sha256(scope_id.encode()).hexdigest()
        try:
            claimed = await self.redis.set(
                redis_key,
                json.dumps({"state": "pending", "body_hash": body_hash}),
                nx=True,
                ex=PENDING_TTL_SECONDS,
            )
            stored = None if claimed else await self.redis.get(redis_key)
        except RedisError:
            await _unavailable(scope, send)
            return

        if not claimed:
            await self._answer_existing(scope, send, stored, body_hash)
            return
        await self._execute_and_store(scope, send, body, body_hash, redis_key)

    async def _answer_existing(self, scope: Scope, send: Send, stored: Any, body_hash: str) -> None:
        record = json.loads(stored) if stored else None
        if record is None:  # expired between SET NX and GET: treat as in progress, the client retries
            record = {"state": "pending", "body_hash": body_hash}
        if record["body_hash"] != body_hash:
            await send_error(
                scope,
                send,
                http_status=409,
                code="CONFLICT",
                message="Idempotency-Key reused with a different body",
            )
            return
        if record["state"] == "pending":
            await send_error(
                scope,
                send,
                http_status=409,
                code="CONFLICT",
                message="A request with this Idempotency-Key is in progress",
            )
            return
        payload = record["body"].encode("latin-1")
        response_headers = [(b"content-length", str(len(payload)).encode()), REPLAY_HEADER]
        if record.get("content_type"):
            response_headers.append((b"content-type", record["content_type"].encode("latin-1")))
        await send({"type": "http.response.start", "status": record["status"], "headers": response_headers})
        await send({"type": "http.response.body", "body": payload})

    async def _execute_and_store(
        self, scope: Scope, send: Send, body: bytes, body_hash: str, redis_key: str
    ) -> None:
        status = 500
        content_type: bytes | None = None
        chunks: list[bytes] = []
        size = 0

        async def replay_body() -> Message:
            nonlocal body
            message: Message = {"type": "http.request", "body": body, "more_body": False}
            body = b""
            return message

        async def capture(message: Message) -> None:
            nonlocal status, content_type, size
            if message["type"] == "http.response.start":
                status = message["status"]
                content_type = dict((k.lower(), v) for k, v in message.get("headers", [])).get(
                    b"content-type"
                )
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                size += len(chunk)
                if size <= MAX_STORED_BODY:
                    chunks.append(chunk)
            await send(message)

        stored = False
        try:
            await self.app(scope, replay_body, capture)
            if status < 500 and size <= MAX_STORED_BODY:
                record = {
                    "state": "done",
                    "body_hash": body_hash,
                    "status": status,
                    "content_type": content_type.decode("latin-1") if content_type else None,
                    "body": b"".join(chunks).decode("latin-1"),
                }
                try:
                    await self.redis.set(redis_key, json.dumps(record), ex=TTL_SECONDS)
                    stored = True
                except RedisError:
                    # The response is already sent; without a record the key is freed below for a retry.
                    logger.warning("idempotency_store_failed", status=status)
        finally:
            if not stored:
                # Not stored (5xx, too large, exception, Redis failing): free the key for a retry.
                try:
                    await self.redis.delete(redis_key)
                except RedisError:
                    pass  # the pending marker expires after PENDING_TTL_SECONDS


async def _read_body(receive: Receive) -> bytes:
    parts: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            parts.append(message.get("body", b""))
            if not message.get("more_body"):
                return b"".join(parts)
        elif message["type"] == "http.disconnect":
            return b"".join(parts)


async def _unavailable(scope: Scope, send: Send) -> None:
    await send_error(
        scope,
        send,
        http_status=503,
        code="DEPENDENCY_UNAVAILABLE",
        message="A required dependency is unavailable",
    )
