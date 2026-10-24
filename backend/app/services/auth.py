"""Login, sessions and logout (API_CONTRACTS §2, SECURITY_MODEL §8, ADR-002).

Every login failure (unknown email, locked, disabled, wrong password) returns the same 401. The real
reason goes only to the audit trail (API_CONTRACTS §11 #17). Sessions are opaque 256-bit tokens; only
their sha256 is stored.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.config import Settings
from app.core.errors import AppError
from app.core.ratelimit import FixedWindowLimiter, RateLimiterUnavailable
from app.core.rbac import RoleGrant
from app.core.security import hash_password, hash_token, needs_rehash, new_token, verify_password
from app.models.identity import Session, User, UserRole

MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 60
LAST_SEEN_RESOLUTION = timedelta(seconds=60)
INVALID_CREDENTIALS = "Invalid email or password"

# Verified against when the email is unknown, so that path costs one argon2 verification like the others.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))

Sessions = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class LoginResult:
    user: User
    session_token: str
    csrf_token: str
    expires_at: datetime


def unauthenticated(message: str = "Authentication required") -> AppError:
    return AppError("UNAUTHENTICATED", message, http_status=401)


async def enforce_rate_limit(limiter: FixedWindowLimiter, key: str) -> None:
    try:
        decision = await limiter.hit(key, limit=RATE_LIMIT, window_seconds=RATE_WINDOW_SECONDS)
    except RateLimiterUnavailable:
        # Fail closed: without the limiter, brute force is only bounded by the per-account lockout.
        raise AppError(
            "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable", http_status=503
        ) from None
    if not decision.allowed:
        raise AppError(
            "RATE_LIMITED",
            "Too many attempts, try again later",
            http_status=429,
            headers={"Retry-After": str(decision.retry_after)},
        )


async def login(
    sessions: Sessions,
    audit: AuditWriter,
    limiter: FixedWindowLimiter,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
    previous_token: str | None,
) -> LoginResult:
    await enforce_rate_limit(limiter, f"kk:rl:login:{ip}")
    email = email.strip().lower()
    now = datetime.now(UTC)
    async with sessions() as db:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            verify_password(_DUMMY_HASH, password)
            await _deny(sessions, audit, actor_id="anonymous", reason="unknown_email", ip=ip)
            raise unauthenticated(INVALID_CREDENTIALS)
        password_ok = verify_password(user.password_hash, password)
        if user.locked_until is not None and user.locked_until > now:
            await _deny(sessions, audit, actor_id=str(user.id), reason="locked", ip=ip)
            raise unauthenticated(INVALID_CREDENTIALS)
        if user.status != "active":
            await _deny(sessions, audit, actor_id=str(user.id), reason="disabled", ip=ip)
            raise unauthenticated(INVALID_CREDENTIALS)
        if not password_ok:
            locked = await record_failure(db, user.id, now)
            await _deny(sessions, audit, actor_id=str(user.id), reason="bad_password", ip=ip)
            if locked:
                await audit.write_detached(
                    sessions,
                    actor_type="system",
                    actor_id="auth",
                    action="auth.lockout",
                    result="success",
                    resource_type="user",
                    resource_id=str(user.id),
                    ip=ip,
                )
            raise unauthenticated(INVALID_CREDENTIALS)

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        if previous_token:
            # Rotation: the session this browser held before is never reused (SECURITY_MODEL §8).
            await db.execute(
                update(Session)
                .where(Session.token_hash == hash_token(previous_token), Session.revoked_at.is_(None))
                .values(revoked_at=now)
            )
        session_token, csrf_token = new_token(), new_token()
        expires_at = now + timedelta(days=settings.session_absolute_days)
        db.add(
            Session(
                user_id=user.id,
                token_hash=hash_token(session_token),
                csrf_token=csrf_token,
                expires_at=expires_at,
                last_seen_at=now,
                ip=ip,
                user_agent=user_agent[:255] if user_agent else None,
            )
        )
        await audit.write(
            db,
            actor_type="user",
            actor_id=str(user.id),
            action="auth.login",
            result="success",
            resource_type="session",
            ip=ip,
        )
        await db.commit()
        return LoginResult(user, session_token, csrf_token, expires_at)


async def record_failure(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> bool:
    """Count one failed attempt atomically; return True when this attempt locked the account."""
    reaches_limit = User.failed_login_count + 1 >= MAX_FAILED_LOGINS
    count = (
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                failed_login_count=case((reaches_limit, 0), else_=User.failed_login_count + 1),
                locked_until=case((reaches_limit, now + LOCKOUT), else_=User.locked_until),
            )
            .returning(User.failed_login_count)
        )
    ).scalar_one()
    await db.commit()
    return count == 0


async def _deny(
    sessions: Sessions, audit: AuditWriter, *, actor_id: str, reason: str, ip: str | None
) -> None:
    await audit.write_detached(
        sessions,
        actor_type="user",
        actor_id=actor_id,
        action="auth.login",
        result="denied",
        resource_type="session",
        metadata={"reason": reason},
        ip=ip,
    )


async def resolve_session(
    sessions: Sessions, settings: Settings, token: str
) -> tuple[User, Session, list[RoleGrant]]:
    now = datetime.now(UTC)
    async with sessions() as db:
        row = (
            await db.execute(
                select(Session, User)
                .join(User, User.id == Session.user_id)
                .where(Session.token_hash == hash_token(token))
            )
        ).one_or_none()
        if row is None:
            raise unauthenticated()
        session, user = row
        if session.revoked_at is not None or user.status != "active":
            raise unauthenticated()
        idle_limit = session.last_seen_at + timedelta(hours=settings.session_ttl_hours)
        if now >= session.expires_at or now >= idle_limit:
            session.revoked_at = now
            await db.commit()
            raise AppError("SESSION_EXPIRED", "Session expired", http_status=401)
        if now - session.last_seen_at >= LAST_SEEN_RESOLUTION:
            session.last_seen_at = now
            await db.commit()
        roles = await db.scalars(select(UserRole).where(UserRole.user_id == user.id))
        grants = [RoleGrant(role.role_name, role.scope) for role in roles]
        return user, session, grants


async def logout(
    sessions: Sessions, audit: AuditWriter, *, session_id: uuid.UUID, user_id: uuid.UUID, ip: str | None
) -> None:
    async with sessions() as db:
        await db.execute(
            update(Session)
            .where(Session.id == session_id, Session.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await audit.write(
            db,
            actor_type="user",
            actor_id=str(user_id),
            action="auth.logout",
            result="success",
            resource_type="session",
            resource_id=str(session_id),
            ip=ip,
        )
        await db.commit()
