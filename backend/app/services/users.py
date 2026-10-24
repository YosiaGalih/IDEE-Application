"""User bootstrap (ADR-002: the first owner is created from the CLI; public registration is disabled)."""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.security import hash_password
from app.models.identity import User, UserRole

# Not fixed by the docs; 12 follows NIST SP 800-63B guidance for user-chosen
# passwords [Kemungkinan Besar].
MIN_PASSWORD_LENGTH = 12


class OwnerExistsError(Exception):
    pass


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
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    async with sessions() as session:
        owner_exists = await session.scalar(
            select(exists().where(UserRole.role_name == "owner", UserRole.scope == "global"))
        )
        if owner_exists:
            raise OwnerExistsError("an owner already exists")
        user = User(email=email, display_name=display_name.strip(), password_hash=hash_password(password))
        session.add(user)
        await session.flush()
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
