"""Regressions from the WP-1.1c security pre-review (T5 secret leakage, ADR-002 single owner)."""

from __future__ import annotations

import asyncio
import io
import traceback

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.db import make_engine, make_sessionmaker
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.identity import User, UserRole
from app.services.users import EmailTakenError, OwnerExistsError, create_owner

PASSWORD = "bootstrap-long-passphrase"


async def seed_member(engine: AsyncEngine, email: str) -> None:
    async with make_sessionmaker(engine)() as session:
        session.add(User(email=email, display_name="Member", password_hash=hash_password(PASSWORD)))
        await session.commit()


def full_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc))


async def test_duplicate_email_error_carries_no_password_hash(engine: AsyncEngine) -> None:
    await seed_member(engine, "taken@example.test")
    with pytest.raises(EmailTakenError) as caught:
        await create_owner(
            make_sessionmaker(engine),
            AuditWriter(Redactor()),
            email="taken@example.test",
            display_name="Owner",
            password=PASSWORD,
        )
    rendered = full_traceback(caught.value)
    assert "$argon2" not in rendered
    assert PASSWORD not in rendered


INSERT_USER = text("INSERT INTO users (id, email, display_name, password_hash) VALUES (:id, :e, 'U', :h)")


async def test_engine_hides_bind_parameters_in_db_errors(migrated_database: str) -> None:
    engine = make_engine(migrated_database)
    first = {"id": "00000000-0000-0000-0000-000000000001", "e": "dup@example.test", "h": "$argon2id$one"}
    second = {"id": "00000000-0000-0000-0000-000000000002", "e": "dup@example.test", "h": "$argon2id$two"}
    try:
        async with engine.begin() as conn:
            await conn.execute(INSERT_USER, first)
        with pytest.raises(IntegrityError) as caught:
            async with engine.begin() as conn:
                await conn.execute(INSERT_USER, second)
    finally:
        await engine.dispose()
    assert "$argon2id$two" not in full_traceback(caught.value)


async def test_concurrent_bootstrap_creates_exactly_one_owner(migrated_database: str) -> None:
    engine = make_engine(migrated_database)
    sessions = make_sessionmaker(engine)
    audit = AuditWriter(Redactor())
    try:
        results = await asyncio.gather(
            *(
                create_owner(
                    sessions, audit, email=f"o{i}@example.test", display_name=f"O{i}", password=PASSWORD
                )
                for i in range(6)
            ),
            return_exceptions=True,
        )
        async with sessions() as session:
            owners = await session.scalar(
                select(func.count())
                .select_from(UserRole)
                .where(UserRole.role_name == "owner", UserRole.scope == "global")
            )
    finally:
        await engine.dispose()
    failures = [r for r in results if isinstance(r, BaseException)]
    assert owners == 1
    assert len(failures) == 5
    assert all(isinstance(r, OwnerExistsError) for r in failures)


def test_cli_duplicate_email_prints_no_hash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], migrated_database: str
) -> None:
    from app.cli import main
    from tests.conftest import make_test_env

    async def seed() -> None:
        engine = make_engine(migrated_database)
        try:
            await seed_member(engine, "member@example.test")
        finally:
            await engine.dispose()

    asyncio.run(seed())
    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{PASSWORD}\n"))
    code = main(
        ["create-owner", "--email", "member@example.test", "--display-name", "Owner", "--password-stdin"]
    )
    out = capsys.readouterr()
    assert code == 1
    assert "already exists" in out.err
    assert "$argon2" not in out.err + out.out
