"""Shared fixtures: a settings object over a temporary WORKSPACE_ROOT and two fixed profiles."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from sandbox_manager.config import Settings, load_settings
from sandbox_manager.profiles import Profile

TOKEN = "t" * 40
PROJECT = uuid.UUID("aaaaaaaa-1111-4111-8111-bbbbbbbbbbbb")
TASK = uuid.UUID("cccccccc-2222-4222-8222-dddddddddddd")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    root = tmp_path / "workspaces"
    root.mkdir()
    loaded = load_settings(
        {
            "SANDBOX_MANAGER_TOKEN": TOKEN,
            "WORKSPACE_ROOT": str(root),
            "SANDBOX_DRIVER": "fake",
            "APP_ENV": "test",
        }
    )
    return replace(loaded, workspace_root=root)


@pytest.fixture
def profiles() -> dict[str, Profile]:
    return {
        "python.pytest": Profile(
            profile_id="python.pytest",
            image="python",
            suite="unit",
            argv=("python", "-m", "pytest", "-q"),
            args_pattern=r"[A-Za-z0-9_./:\[\]-]{1,200}",
            max_args=3,
            timeout_s=60,
            max_timeout_s=120,
        ),
        "node.npm_test": Profile(
            profile_id="node.npm_test",
            image="node",
            suite="unit",
            argv=("npm", "test", "--silent"),
            args_pattern=r"[a-z]{1,20}",
            timeout_s=60,
            max_timeout_s=60,
            network="egress-allowed",
        ),
    }


def task_workspace(settings: Settings, project: uuid.UUID = PROJECT, task: uuid.UUID = TASK) -> Path:
    path = settings.workspace_root / str(project) / "tasks" / str(task)
    path.mkdir(parents=True, exist_ok=True)
    return path


def repo_workspace(settings: Settings, project: uuid.UUID = PROJECT) -> Path:
    path = settings.workspace_root / str(project) / "repo"
    path.mkdir(parents=True, exist_ok=True)
    return path
