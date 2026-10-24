from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, load_settings

BACKEND = Path(__file__).resolve().parents[1]

TEST_SECRET_KEY = "test-secret-key-" + "k" * 32
TEST_SANDBOX_TOKEN = "test-sandbox-token-" + "s" * 32


def make_test_env(**overrides: str) -> dict[str, str]:
    env = {
        "APP_ENV": "test",
        "SECRET_KEY": TEST_SECRET_KEY,
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL", "postgresql+asyncpg://kodekantor:kodekantor@localhost:5432/kodekantor_test"
        ),
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15"),
        "CLAUDE_MODEL_DEFAULT": "claude-test-model",
        "SANDBOX_MANAGER_URL": "http://sandbox-manager.test:8100",
        "SANDBOX_MANAGER_TOKEN": TEST_SANDBOX_TOKEN,
        "LLM_PROVIDER": "scripted",
        "SANDBOX_DRIVER": "fake",
    }
    env.update(overrides)
    return env


@pytest.fixture
def settings() -> Settings:
    return load_settings(make_test_env())


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """A real PostgreSQL migrated to head, once per run (TEST_STRATEGY §2.2)."""
    url = make_test_env()["DATABASE_URL"]
    config = alembic_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return url


@pytest.fixture
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
