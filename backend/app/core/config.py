"""Application settings — the canonical list is docs/CONFIGURATION.md (first table, v1 application variables).

The app refuses to start when a required value is missing or unsafe (ARCHITECTURE §4.3, ADR-015). Error
messages name the variable and the rule only, never the value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import cached_property
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_PLACEHOLDER_MARKERS = ("change-me", "changeme", "replace-me", "example", "placeholder")
_MIN_SECRET_LENGTH = 32


class ConfigError(Exception):
    """Invalid configuration. ``problems`` holds ``"<VARIABLE>: <rule>"`` lines without values."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid configuration:\n- " + "\n- ".join(problems))


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    app_env: Literal["development", "test", "production"] = "development"
    app_url: str = "http://localhost:5173"
    app_domain: str = "localhost"
    secret_key: str = Field(min_length=_MIN_SECRET_LENGTH)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = ""
    cookie_secure: bool = True
    session_ttl_hours: int = Field(default=12, gt=0)
    session_absolute_days: int = Field(default=7, gt=0)
    database_url: str
    redis_url: str
    llm_provider: Literal["anthropic", "scripted"] = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    claude_model_default: str = Field(min_length=1)
    claude_model_fast: str | None = None
    claude_model_complex: str | None = None
    claude_max_output_tokens: int = Field(default=8192, gt=0)
    claude_request_timeout_seconds: int = Field(default=120, gt=0)
    claude_max_tool_iterations: int = Field(default=40, gt=0)
    pricing_path: str = "config/pricing.yaml"
    task_max_cost_usd: float = Field(default=2.0, ge=0)
    task_max_tokens: int = Field(default=400_000, gt=0)
    task_max_debug_attempts: int = Field(default=5, ge=0)
    task_auto_remediation_max: int = Field(default=1, ge=0)
    task_max_duration_minutes: int = Field(default=60, gt=0)
    task_max_iterations: int = Field(default=3, gt=0)
    max_active_tasks: int = Field(default=2, gt=0)
    workspace_root: str = "/srv/kodekantor/workspaces"
    project_quota_mb: int = Field(default=2048, gt=0)
    upload_max_mb: int = Field(default=200, gt=0)
    git_import_allowed_hosts: str = "github.com,gitlab.com,codeberg.org"
    sandbox_manager_url: str = "http://sandbox-manager:8100"
    sandbox_manager_token: str = Field(min_length=_MIN_SECRET_LENGTH)
    sandbox_image_python: str = "kodekantor/sandbox-python:1"
    sandbox_image_node: str = "kodekantor/sandbox-node:1"
    sandbox_cpu_limit: float = Field(default=1.0, gt=0)
    sandbox_memory_limit: str = "1g"
    sandbox_pids_limit: int = Field(default=256, gt=0)
    sandbox_timeout_seconds: int = Field(default=300, gt=0)
    sandbox_max_output_bytes: int = Field(default=1_048_576, gt=0)
    sandbox_max_workspace_growth_mb: int = Field(default=512, gt=0)
    llm_require_readiness: bool = True
    sandbox_egress: Literal["off", "allowlist"] = "off"
    sandbox_egress_network: str = "kk-egress"
    sandbox_runtime: Literal["", "runsc"] = ""
    sandbox_profiles_path: str = "/etc/kodekantor/sandbox-profiles.yaml"
    sandbox_driver: Literal["docker", "fake"] = "docker"
    sandbox_max_concurrent_runs: int = Field(default=4, gt=0)
    docker_host: str = "tcp://docker-socket-proxy:2375"
    agent_registry_path: str = "/app/agents/registry.yaml"
    celery_task_always_eager: bool = False
    otel_exporter_otlp_endpoint: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        return _split_csv(self.cors_origins)

    @property
    def git_import_allowed_hosts_list(self) -> list[str]:
        return _split_csv(self.git_import_allowed_hosts)

    @cached_property
    def _secrets(self) -> list[str]:
        values = [self.secret_key, self.sandbox_manager_token, self.anthropic_api_key]
        for url in (self.database_url, self.redis_url):
            password = urlsplit(url).password
            values.append(password)
        return [value for value in values if value]

    def secret_values(self) -> list[str]:
        """Literal secret values, fed to the redactor so they never leave the process."""
        return list(self._secrets)

    def model_post_init(self, __context: object) -> None:
        # Settings are frozen; fill the documented "= default" fallbacks without mutating validation input.
        if self.claude_model_fast is None:
            object.__setattr__(self, "claude_model_fast", self.claude_model_default)
        if self.claude_model_complex is None:
            object.__setattr__(self, "claude_model_complex", self.claude_model_default)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _production_problems(settings: Settings) -> list[str]:
    problems: list[str] = []
    if any(marker in settings.secret_key.lower() for marker in _PLACEHOLDER_MARKERS):
        problems.append("SECRET_KEY: example/placeholder values are rejected in production")
    if settings.llm_provider == "scripted":
        problems.append("LLM_PROVIDER: 'scripted' is forbidden in production (ADR-015)")
    if settings.sandbox_driver == "fake":
        problems.append("SANDBOX_DRIVER: 'fake' is forbidden in production")
    if not settings.cookie_secure:
        problems.append("COOKIE_SECURE: must be true in production")
    if not settings.llm_require_readiness:
        problems.append("LLM_REQUIRE_READINESS: must be true in production (ADR-032)")
    if settings.celery_task_always_eager:
        problems.append("CELERY_TASK_ALWAYS_EAGER: must be false in production")
    if settings.docker_host.startswith("unix:"):
        problems.append("DOCKER_HOST: production must use docker-socket-proxy, never the raw socket")
    return problems


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from ``env`` (defaults to ``os.environ``); raise ``ConfigError`` when unsafe."""
    source = os.environ if env is None else env
    fields = Settings.model_fields
    data = {key.lower(): value for key, value in source.items() if key.lower() in fields}
    try:
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_input=False, include_url=False, include_context=False):
            name = str(error["loc"][0]).upper() if error["loc"] else "SETTINGS"
            problems.append(f"{name}: {error['msg']}")
        raise ConfigError(problems) from None
    if settings.is_production:
        problems = _production_problems(settings)
        if problems:
            raise ConfigError(problems)
    return settings
