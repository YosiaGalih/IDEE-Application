"""Settings from the environment (CONFIGURATION.md ``SANDBOX_*``). A bad value stops the service at start."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

MIN_TOKEN_LENGTH = 32
_MEMORY = re.compile(r"^(\d+)([kmg]?)$")
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


class ConfigError(ValueError):
    """A setting is missing or invalid; the message never contains the value."""


def _memory_bytes(value: str) -> int:
    match = _MEMORY.match(value.strip().lower())
    if match is None:
        raise ConfigError("SANDBOX_MEMORY_LIMIT must look like 1g, 512m or a byte count")
    return int(match[1]) * _UNITS[match[2]]


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    workspace_root: Path
    profiles_path: Path
    driver: str  # "docker" | "fake"
    app_env: str
    docker_host: str  # tcp://docker-socket-proxy:2375; unix:// only outside production (ADR-009 (1))
    images: Mapping[str, str]  # profile image alias → image reference
    cpu_limit: float
    memory_bytes: int
    pids_limit: int
    default_timeout_s: int
    max_output_bytes: int
    max_workspace_growth_mb: int
    egress: str  # "off" | "allowlist"
    egress_network: str
    runtime: str  # "" (runc) | "runsc"
    max_concurrent_runs: int

    @property
    def private_dir(self) -> Path:
        """Manager-owned files under WORKSPACE_ROOT (same path on host and in the container, ADR-005)."""
        return self.workspace_root / ".kk-sandbox"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    token = env.get("SANDBOX_MANAGER_TOKEN", "")
    if len(token) < MIN_TOKEN_LENGTH:
        raise ConfigError(f"SANDBOX_MANAGER_TOKEN must be at least {MIN_TOKEN_LENGTH} characters")
    driver = env.get("SANDBOX_DRIVER", "docker")
    if driver not in ("docker", "fake"):
        raise ConfigError("SANDBOX_DRIVER must be docker or fake")
    if driver == "fake" and env.get("APP_ENV", "production") == "production":
        raise ConfigError("SANDBOX_DRIVER=fake is refused in production")
    egress = env.get("SANDBOX_EGRESS", "off")
    if egress not in ("off", "allowlist"):
        raise ConfigError("SANDBOX_EGRESS must be off or allowlist")
    runtime = env.get("SANDBOX_RUNTIME", "")
    if runtime not in ("", "runsc"):
        raise ConfigError("SANDBOX_RUNTIME must be empty (runc) or runsc")
    workspace_root = Path(env.get("WORKSPACE_ROOT", "/srv/kodekantor/workspaces"))
    if not workspace_root.is_absolute():
        raise ConfigError("WORKSPACE_ROOT must be absolute")
    docker_host = env.get("DOCKER_HOST", "tcp://docker-socket-proxy:2375")
    if not docker_host.startswith(("tcp://", "unix://")):
        raise ConfigError("DOCKER_HOST must be tcp:// or unix://")
    if docker_host.startswith("unix://") and env.get("APP_ENV", "production") == "production":
        raise ConfigError("DOCKER_HOST must be the socket proxy in production, never the socket (ADR-009)")
    try:
        settings = Settings(
            token=token,
            workspace_root=workspace_root,
            profiles_path=Path(env.get("SANDBOX_PROFILES_PATH", "/etc/kodekantor/sandbox-profiles.yaml")),
            driver=driver,
            app_env=env.get("APP_ENV", "production"),
            docker_host=docker_host,
            images={
                "python": env.get("SANDBOX_IMAGE_PYTHON", "kodekantor/sandbox-python:1"),
                "node": env.get("SANDBOX_IMAGE_NODE", "kodekantor/sandbox-node:1"),
            },
            cpu_limit=float(env.get("SANDBOX_CPU_LIMIT", "1.0")),
            memory_bytes=_memory_bytes(env.get("SANDBOX_MEMORY_LIMIT", "1g")),
            pids_limit=int(env.get("SANDBOX_PIDS_LIMIT", "256")),
            default_timeout_s=int(env.get("SANDBOX_TIMEOUT_SECONDS", "300")),
            max_output_bytes=int(env.get("SANDBOX_MAX_OUTPUT_BYTES", "1048576")),
            max_workspace_growth_mb=int(env.get("SANDBOX_MAX_WORKSPACE_GROWTH_MB", "512")),
            egress=egress,
            egress_network=env.get("SANDBOX_EGRESS_NETWORK", "kk-egress"),
            runtime=runtime,
            max_concurrent_runs=int(env.get("SANDBOX_MAX_CONCURRENT_RUNS", "4")),
        )
    except ValueError as exc:
        raise ConfigError("a numeric SANDBOX_* setting is not a number") from exc
    _check_limits(settings)
    return settings


def _check_limits(settings: Settings) -> None:
    """Docker reads 0 or -1 as "no limit": every limit must be a real, positive bound (fail closed)."""
    if not (math.isfinite(settings.cpu_limit) and 0 < settings.cpu_limit <= 64):
        raise ConfigError("SANDBOX_CPU_LIMIT must be a number in (0, 64]")
    if settings.memory_bytes < 64 * 1024**2:
        raise ConfigError("SANDBOX_MEMORY_LIMIT must be at least 64m")
    checks = {
        "SANDBOX_PIDS_LIMIT": settings.pids_limit,
        "SANDBOX_TIMEOUT_SECONDS": settings.default_timeout_s,
        "SANDBOX_MAX_OUTPUT_BYTES": settings.max_output_bytes,
        "SANDBOX_MAX_WORKSPACE_GROWTH_MB": settings.max_workspace_growth_mb,
        "SANDBOX_MAX_CONCURRENT_RUNS": settings.max_concurrent_runs,
    }
    for name, value in checks.items():
        if value < 1:
            raise ConfigError(f"{name} must be at least 1")


def check_process_user(settings: Settings) -> None:
    """In production the manager runs as root or as the sandbox user (10001), never as a third uid.

    With a third uid the run folders would have to be world-writable (``docker_driver.prepare_artifacts``).
    """
    if settings.app_env == "production" and os.geteuid() not in (0, 10001):
        raise ConfigError("in production the sandbox manager must run as uid 10001 (or root)")
