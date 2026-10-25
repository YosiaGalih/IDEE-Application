"""Run request validation (ADR-009 (2), ADR-007, SECURITY_MODEL §4 T2)."""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from sandbox_manager.config import Settings
from sandbox_manager.profiles import Profile
from sandbox_manager.validation import RequestRejected, RunRequest, validate

from .conftest import PROJECT, TASK, repo_workspace, task_workspace

SUBPATH = f"{PROJECT}/tasks/{TASK}"


def request(**overrides: object) -> RunRequest:
    body: dict[str, object] = {
        "project_id": str(PROJECT),
        "task_id": str(TASK),
        "workspace_subpath": SUBPATH,
        "profile_id": "python.pytest",
    }
    body.update(overrides)
    return RunRequest.model_validate(body)


def rejected(code: str, req: RunRequest, profiles: dict[str, Profile], settings: Settings) -> None:
    with pytest.raises(RequestRejected) as info:
        validate(req, profiles, settings)
    assert info.value.code == code


def test_valid_task_run(settings: Settings, profiles: dict[str, Profile]) -> None:
    workspace = task_workspace(settings)
    run = validate(request(args=["tests/test_a.py::test_x"]), profiles, settings)
    assert run.workspace == workspace
    assert run.args == ("tests/test_a.py::test_x",)
    assert run.timeout_s == 60
    assert run.network == "none"
    assert run.read_only is False


def test_repo_run_is_always_read_only(settings: Settings, profiles: dict[str, Profile]) -> None:
    repo_workspace(settings)
    run = validate(request(task_id=None, workspace_subpath=f"{PROJECT}/repo"), profiles, settings)
    assert run.read_only is True


def test_unknown_fields_are_refused() -> None:
    for field in ("image", "HostConfig", "binds", "privileged", "env", "user", "command"):
        with pytest.raises(ValidationError):
            request(**{field: "x"})


def test_unknown_profile(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    rejected("UNKNOWN_PROFILE", request(profile_id="python.shell"), profiles, settings)


@pytest.mark.parametrize(
    "subpath",
    [
        "../etc",
        f"{PROJECT}/tasks/{TASK}/..",
        f"{PROJECT}/tasks/{TASK}/src",
        f"{PROJECT}/tasks/../repo",
        f"/{PROJECT}/repo",
        f"{PROJECT}/repo/",
        f"{PROJECT.hex}/repo",
        f"{str(PROJECT).upper()}/repo",
        f"{PROJECT}/tasks/{TASK}\n",
        "",
    ],
)
def test_subpath_shape(settings: Settings, profiles: dict[str, Profile], subpath: str) -> None:
    task_workspace(settings)
    repo_workspace(settings)
    rejected("INVALID_WORKSPACE", request(workspace_subpath=subpath), profiles, settings)


def test_subpath_must_belong_to_project_and_task(settings: Settings, profiles: dict[str, Profile]) -> None:
    other = uuid.UUID("33333333-3333-4333-8333-333333333333")
    task_workspace(settings, project=other)
    task_workspace(settings, task=other)
    rejected(
        "INVALID_WORKSPACE",
        request(workspace_subpath=f"{other}/tasks/{TASK}"),
        profiles,
        settings,
    )
    rejected(
        "INVALID_WORKSPACE",
        request(workspace_subpath=f"{PROJECT}/tasks/{other}"),
        profiles,
        settings,
    )
    rejected("INVALID_WORKSPACE", request(task_id=None), profiles, settings)


def test_missing_workspace(settings: Settings, profiles: dict[str, Profile]) -> None:
    rejected("INVALID_WORKSPACE", request(), profiles, settings)


def test_workspace_file_is_not_a_folder(settings: Settings, profiles: dict[str, Profile]) -> None:
    folder = settings.workspace_root / str(PROJECT) / "tasks"
    folder.mkdir(parents=True)
    (folder / str(TASK)).write_text("not a folder")
    rejected("INVALID_WORKSPACE", request(), profiles, settings)


def test_symlinked_workspace_is_refused(
    settings: Settings, profiles: dict[str, Profile], tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    folder = settings.workspace_root / str(PROJECT) / "tasks"
    folder.mkdir(parents=True)
    os.symlink(outside, folder / str(TASK))
    rejected("INVALID_WORKSPACE", request(), profiles, settings)


def test_symlinked_project_folder_is_refused(
    settings: Settings, profiles: dict[str, Profile], tmp_path: Path
) -> None:
    """A link higher up the path (the project folder) also crosses the root."""
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "tasks" / str(TASK)).mkdir(parents=True)
    os.symlink(elsewhere, settings.workspace_root / str(PROJECT))
    rejected("INVALID_WORKSPACE", request(), profiles, settings)


def test_symlinked_workspace_root_is_followed(
    settings: Settings, profiles: dict[str, Profile], tmp_path: Path
) -> None:
    """WORKSPACE_ROOT itself may be a link (e.g. /srv → /data); only links below it are refused."""
    alias = tmp_path / "alias"
    os.symlink(settings.workspace_root, alias)
    workspace = task_workspace(settings)
    run = validate(request(), profiles, replace(settings, workspace_root=alias))
    assert run.workspace == workspace


@pytest.mark.parametrize("arg", ["-x", "--rootdir=/", "-p", "a b", "$(id)", "x;y", "", "a" * 201])
def test_args_must_match_profile(settings: Settings, profiles: dict[str, Profile], arg: str) -> None:
    task_workspace(settings)
    rejected("INVALID_ARGS", request(args=[arg]), profiles, settings)


def test_too_many_args(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    rejected("INVALID_ARGS", request(args=["a", "b", "c", "d"]), profiles, settings)


def test_timeout(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    assert validate(request(timeout_s=120), profiles, settings).timeout_s == 120
    rejected("INVALID_TIMEOUT", request(timeout_s=121), profiles, settings)
    with pytest.raises(ValidationError):
        request(timeout_s=0)


def test_egress_needs_profile_permission(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    allowlist = replace(settings, egress="allowlist")
    rejected("EGRESS_NOT_ALLOWED", request(network="egress"), profiles, allowlist)


def test_egress_needs_setting(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    rejected(
        "EGRESS_NOT_ALLOWED",
        request(profile_id="node.npm_test", network="egress"),
        profiles,
        settings,
    )


def test_egress_allowed_when_both_agree(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    allowlist = replace(settings, egress="allowlist")
    run = validate(request(profile_id="node.npm_test", network="egress"), profiles, allowlist)
    assert run.network == "egress"


def test_network_value_is_closed() -> None:
    for value in ("host", "bridge", "container:x"):
        with pytest.raises(ValidationError):
            request(network=value)
