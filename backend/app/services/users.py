"""User bootstrap (ADR-002: the first owner is created from the CLI; public registration is disabled)."""

from __future__ import annotations

import uuid

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.security import hash_password
from app.models.identity import User, UserRole

# Not fixed by the docs; 12 follows NIST SP 800-63B guidance for user-chosen
# passwords [Kemungkinan Besar].
MIN_PASSWORD_LENGTH = 12
# Upper bound keeps one argon2 hash cheap enough that long inputs cannot be used for DoS [Kemungkinan Besar].
MAX_PASSWORD_LENGTH = 1024

# Serialises owner bootstrap so the "no owner yet" check and the insert cannot interleave (ADR-002).
_OWNER_BOOTSTRAP_LOCK = "kodekantor.create_owner"


class OwnerExistsError(Exception):
    pass


class EmailTakenError(Exception):
    pass


def validate_new_password(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(f"password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters")


async def create_owner(
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    *,
    email: str,
    display_name: str,
    password: str,
) -> uuid.UUID:
    """Create the single global owner. Refuses when an owner already exists."""
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValueError("email is not valid")
    if not display_name.strip() or len(display_name) > 120:
        raise ValueError("display_name must be 1-120 characters")
    validate_new_password(password)
    async with sessions() as session:
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(_OWNER_BOOTSTRAP_LOCK))))
        owner_exists = await session.scalar(
            select(exists().where(UserRole.role_name == "owner", UserRole.scope == "global"))
        )
        if owner_exists:
            raise OwnerExistsError("an owner already exists")
        user = User(email=email, display_name=display_name.strip(), password_hash=hash_password(password))
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            if "uq_users_email" not in str(exc.orig):
                raise
            # Raised without the DB error attached: its text can carry the INSERT values.
            raise EmailTakenError("a user with this email already exists") from None
        session.add(UserRole(user_id=user.id, role_name="owner", scope="global"))
        await audit.write(
            session,
            actor_type="system",
            actor_id="cli",
            action="user.owner_created",
            result="success",
            resource_type="user",
            resource_id=str(user.id),
        )
        await session.commit()
        return user.id
