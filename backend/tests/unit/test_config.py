"""Configuration contract — docs/CONFIGURATION.md, ARCHITECTURE §4.3, ADR-015 (fail-start)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import ConfigError, Settings, load_settings

CONFIGURATION_MD = Path(__file__).resolve().parents[3] / "docs" / "CONFIGURATION.md"

SECRET = "x" * 40
TOKEN = "t" * 40


def base_env(**overrides: str) -> dict[str, str]:
    env = {
        "APP_ENV": "development",
        "SECRET_KEY": SECRET,
        "DATABASE_URL": "postgresql+asyncpg://kk:dbpass1234@localhost:5432/kodekantor",
        "REDIS_URL": "redis://:redispass99@localhost:6379/0",
        "CLAUDE_MODEL_DEFAULT": "claude-model-id-from-config",
        "SANDBOX_MANAGER_TOKEN": TOKEN,
    }
    env.update(overrides)
    return env


def production_env(**overrides: str) -> dict[str, str]:
    return base_env(APP_ENV="production", **overrides)


def documented_app_variables() -> list[str]:
    """Variable names in the first table of CONFIGURATION.md (v1 application variables)."""
    text = CONFIGURATION_MD.read_text(encoding="utf-8")
    first_table = text.split("\n## ", 1)[0]
    names = re.findall(r"^\| `([A-Z][A-Z0-9_]+)`", first_table, flags=re.MULTILINE)
    # VITE_* are frontend build-time variables, not backend settings.
    return [name for name in names if not name.startswith("VITE_")]


def test_every_documented_application_variable_is_a_setting() -> None:
    documented = documented_app_variables()
    assert len(documented) > 40, "CONFIGURATION.md table was not parsed"
    fields = {name.upper() for name in Settings.model_fields}
    assert sorted(set(documented) - fields) == []


def test_defaults_follow_the_documentation() -> None:
    settings = load_settings(base_env())
    assert settings.app_env == "development"
    assert settings.llm_provider == "anthropic"
    assert settings.max_active_tasks == 2
    assert settings.task_max_cost_usd == 2.0
    assert settings.sandbox_driver == "docker"
    assert settings.sandbox_egress == "off"
    assert settings.cookie_secure is True
    assert settings.claude_model_fast == "claude-model-id-from-config"
    assert settings.git_import_allowed_hosts_list == ["github.com", "gitlab.com", "codeberg.org"]


def test_values_are_read_from_the_environment_mapping() -> None:
    settings = load_settings(
        base_env(MAX_ACTIVE_TASKS="4", COOKIE_SECURE="false", CORS_ORIGINS="a.test, b.test")
    )
    assert settings.max_active_tasks == 4
    assert settings.cookie_secure is False
    assert settings.cors_origins_list == ["a.test", "b.test"]


@pytest.mark.parametrize(
    "missing",
    ["SECRET_KEY", "DATABASE_URL", "REDIS_URL", "CLAUDE_MODEL_DEFAULT", "SANDBOX_MANAGER_TOKEN"],
)
def test_missing_required_variable_fails_start(missing: str) -> None:
    env = base_env()
    del env[missing]
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    assert any(problem.startswith(missing) for problem in exc.value.problems)


@pytest.mark.parametrize(
    ("overrides", "variable"),
    [
        ({"SECRET_KEY": "short"}, "SECRET_KEY"),
        ({"SANDBOX_MANAGER_TOKEN": "short"}, "SANDBOX_MANAGER_TOKEN"),
        ({"APP_ENV": "staging"}, "APP_ENV"),
        ({"LLM_PROVIDER": "openai"}, "LLM_PROVIDER"),
        ({"SANDBOX_DRIVER": "local"}, "SANDBOX_DRIVER"),
        ({"MAX_ACTIVE_TASKS": "two"}, "MAX_ACTIVE_TASKS"),
    ],
)
def test_invalid_values_fail_start(overrides: dict[str, str], variable: str) -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings(base_env(**overrides))
    assert any(problem.startswith(variable) for problem in exc.value.problems)


@pytest.mark.parametrize(
    ("overrides", "variable"),
    [
        ({"LLM_PROVIDER": "scripted"}, "LLM_PROVIDER"),
        ({"SANDBOX_DRIVER": "fake"}, "SANDBOX_DRIVER"),
        ({"COOKIE_SECURE": "false"}, "COOKIE_SECURE"),
        ({"LLM_REQUIRE_READINESS": "false"}, "LLM_REQUIRE_READINESS"),
        ({"CELERY_TASK_ALWAYS_EAGER": "true"}, "CELERY_TASK_ALWAYS_EAGER"),
        ({"DOCKER_HOST": "unix:///var/run/docker.sock"}, "DOCKER_HOST"),
        ({"SECRET_KEY": "change-me-" + "x" * 30}, "SECRET_KEY"),
    ],
)
def test_production_rejects_unsafe_settings(overrides: dict[str, str], variable: str) -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings(production_env(**overrides))
    assert any(problem.startswith(variable) for problem in exc.value.problems)


def test_safe_production_settings_start() -> None:
    settings = load_settings(production_env())
    assert settings.is_production


def test_config_error_never_contains_values() -> None:
    env = production_env(SECRET_KEY="change-me-SUPERSECRETVALUE-123456789012", SANDBOX_MANAGER_TOKEN="tok")
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    message = str(exc.value)
    assert "SUPERSECRETVALUE" not in message
    assert "tok" not in message.replace("TOKEN", "")
    assert "dbpass1234" not in message


def test_secret_values_include_credentials_inside_urls() -> None:
    settings = load_settings(base_env(ANTHROPIC_API_KEY="sk-ant-test-" + "0" * 30))
    secrets = settings.secret_values()
    assert SECRET in secrets and TOKEN in secrets
    assert "dbpass1234" in secrets and "redispass99" in secrets
    assert "sk-ant-test-" + "0" * 30 in secrets
