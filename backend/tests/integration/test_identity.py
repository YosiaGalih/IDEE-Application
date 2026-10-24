"""Identity schema and owner bootstrap (DATA_MODEL users/user_roles/sessions, ADR-002)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.core.security import hash_token, verify_password
from app.models.identity import Session, User, UserRole
from app.services.users import OwnerExistsError, create_owner
from tests.conftest import reset_data


@pytest.fixture(autouse=True)
async def clean_identity(engine: AsyncEngine) -> None:
    await reset_data(engine)


def new_user(email: str = "dev@example.test") -> User:
    return User(email=email, display_name="Dev", password_hash="x")


async def test_email_must_be_lowercase_and_unique(session: AsyncSession) -> None:
    session.add(new_user("Dev@Example.test"))
    with pytest.raises(IntegrityError, match="ck_users_email_lowercase"):
        await session.flush()
    await session.rollback()
    session.add_all([new_user(), new_user()])
    with pytest.raises(IntegrityError, match="uq_users_email"):
        await session.flush()


async def test_role_and_scope_are_checked(session: AsyncSession) -> None:
    user = new_user()
    session.add(user)
    await session.flush()
    session.add(UserRole(user_id=user.id, role_name="root", scope="global"))
    with pytest.raises(IntegrityError, match="ck_user_roles_role_name"):
        await session.flush()
    await session.rollback()
    user = new_user()
    session.add(user)
    await session.flush()
    session.add(UserRole(user_id=user.id, role_name="admin", scope="project:../../etc"))
    with pytest.raises(IntegrityError, match="ck_user_roles_scope"):
        await session.flush()


async def test_deleting_a_user_cascades_roles_and_sessions(session: AsyncSession) -> None:
    user = new_user()
    session.add(user)
    await session.flush()
    session.add(UserRole(user_id=user.id, role_name="developer", scope=f"project:{uuid.uuid4()}"))
    session.add(
        Session(
            user_id=user.id,
            token_hash=hash_token("t"),
            csrf_token="c" * 32,
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
    )
    await session.commit()
    await session.delete(user)
    await session.commit()
    assert (await session.scalar(text("SELECT count(*) FROM user_roles"))) == 0
    assert (await session.scalar(text("SELECT count(*) FROM sessions"))) == 0


async def test_create_owner_once_with_argon2_and_audit(engine: AsyncEngine, session: AsyncSession) -> None:
    sessions = make_sessionmaker(engine)
    audit = AuditWriter(Redactor())
    user_id = await create_owner(
        sessions, audit, email="  Owner@Example.TEST ", display_name="Owner", password="a-long-passphrase"
    )
    user = await session.scalar(select(User).where(User.id == user_id))
    assert user is not None and user.email == "owner@example.test"
    assert verify_password(user.password_hash, "a-long-passphrase")
    role = await session.scalar(select(UserRole).where(UserRole.user_id == user_id))
    assert role is not None and (role.role_name, role.scope) == ("owner", "global")
    audited = await session.scalar(
        text("SELECT count(*) FROM audit_events WHERE action = 'user.owner_created' AND resource_id = :id"),
        {"id": str(user_id)},
    )
    assert audited == 1
    with pytest.raises(OwnerExistsError):
        await create_owner(
            sessions, audit, email="b@example.test", display_name="B", password="another-long-pass"
        )


async def test_create_owner_rejects_weak_input(engine: AsyncEngine) -> None:
    sessions = make_sessionmaker(engine)
    audit = AuditWriter(Redactor())
    with pytest.raises(ValueError, match="password"):
        await create_owner(sessions, audit, email="o@example.test", display_name="O", password="short")
    with pytest.raises(ValueError, match="email"):
        await create_owner(
            sessions, audit, email="not-an-email", display_name="O", password="a-long-passphrase"
        )


def test_cli_create_owner_reads_password_from_stdin(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import io

    from app.cli import main
    from tests.conftest import make_test_env

    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.stdin", io.StringIO("cli-long-passphrase\n"))
    assert (
        main(["create-owner", "--email", "cli@example.test", "--display-name", "CLI", "--password-stdin"])
        == 0
    )
    assert "owner created" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO("cli-long-passphrase\n"))
    assert main(["create-owner", "--email", "x@example.test", "--display-name", "X", "--password-stdin"]) == 1
