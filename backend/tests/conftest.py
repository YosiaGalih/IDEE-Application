from __future__ import annotations

import os

import pytest

from app.core.config import Settings, load_settings

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
