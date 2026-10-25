"""The container spec (ADR-009 (1)(2)(3), ADR-026 (1), ADR-033, SECURITY_MODEL §4)."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from sandbox_manager.config import Settings, load_settings
from sandbox_manager.hostconfig import build_container_spec, empty_file, run_artifacts_dir
from sandbox_manager.profiles import Profile
from sandbox_manager.validation import RequestRejected, RunRequest, ValidatedRun, validate

from .conftest import PROJECT, TASK, TOKEN, repo_workspace, task_workspace

RUN_ID = "abc123"


def validated(settings: Settings, profiles: dict[str, Profile], **overrides: Any) -> ValidatedRun:
    body: dict[str, Any] = {
        "project_id": str(PROJECT),
        "task_id": str(TASK),
        "workspace_subpath": f"{PROJECT}/tasks/{TASK}",
        "profile_id": "python.pytest",
    }
    body.update(overrides)
    return validate(RunRequest.model_validate(body), profiles, settings)


def mounts_by_target(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["Target"]: m for m in spec["HostConfig"]["Mounts"]}


def test_hardened_hostconfig(settings: Settings, profiles: dict[str, Profile]) -> None:
    workspace = task_workspace(settings)
    spec = build_container_spec(validated(settings, profiles), RUN_ID, settings)
    host = spec["HostConfig"]
    assert spec["User"] == "10001:10001"
    assert spec["Image"] == "kodekantor/sandbox-python:1"
    assert spec["Cmd"] == ["python", "-m", "pytest", "-q"]
    assert spec["WorkingDir"] == "/workspace"
    assert spec["NetworkDisabled"] is True
    assert spec["Tty"] is False and spec["OpenStdin"] is False
    assert host["NetworkMode"] == "none"
    assert host["ReadonlyRootfs"] is True
    assert host["Privileged"] is False
    assert host["CapDrop"] == ["ALL"]
    assert "CapAdd" not in host
    assert host["SecurityOpt"] == ["no-new-privileges"]
    assert host["PidsLimit"] == 256
    assert host["Memory"] == 1024**3
    assert host["MemorySwap"] == host["Memory"]
    assert host["NanoCpus"] == 1_000_000_000
    assert host["IpcMode"] == "private"
    assert "PidMode" not in host and "UsernsMode" not in host and "Devices" not in host
    assert set(host["Tmpfs"]) == {"/tmp", "/home/sandbox"}
    assert "noexec" in host["Tmpfs"]["/tmp"]
    assert "uid=10001,gid=10001,mode=0700" in host["Tmpfs"]["/home/sandbox"]  # HOME writable by the sandbox
    mounts = mounts_by_target(spec)
    assert set(mounts) == {"/workspace", "/workspace/.kk"}
    assert mounts["/workspace"] == {
        "Type": "bind",
        "Source": str(workspace),
        "Target": "/workspace",
        "ReadOnly": False,
        "BindOptions": {"Propagation": "rprivate"},
    }
    assert mounts["/workspace/.kk"]["Source"] == str(run_artifacts_dir(settings, PROJECT, RUN_ID))
    assert mounts["/workspace/.kk"]["Source"] == str(
        settings.workspace_root / str(PROJECT) / ".kk-runs" / RUN_ID
    )
    assert host["Ulimits"] == [{"Name": "nofile", "Soft": 1024, "Hard": 1024}]
    assert "size=268435456" in host["Tmpfs"]["/tmp"] and "size=67108864" in host["Tmpfs"]["/home/sandbox"]
    assert host["LogConfig"] == {
        "Type": "json-file",
        "Config": {"max-size": str(64 * 1024 * 1024), "max-file": "2"},
    }
    assert spec["Labels"] == {
        "kk.managed": "1",
        "kk.run_id": RUN_ID,
        "kk.project_id": str(PROJECT),
        "kk.profile_id": "python.pytest",
        "kk.task_id": str(TASK),
    }


def test_no_docker_sock_mount(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    spec = build_container_spec(validated(settings, profiles), RUN_ID, settings)
    text = json.dumps(spec)
    assert "docker.sock" not in text
    assert "Binds" not in spec["HostConfig"]
    for mount in spec["HostConfig"]["Mounts"]:
        if mount["Type"] == "bind":
            assert Path(mount["Source"]).is_relative_to(settings.workspace_root)


def test_docker_host_from_settings() -> None:
    env = {"SANDBOX_MANAGER_TOKEN": TOKEN, "DOCKER_HOST": "tcp://docker-socket-proxy:2375"}
    assert load_settings(env).docker_host == "tcp://docker-socket-proxy:2375"


def test_env_allowlist(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    os.environ["KK_LEAK_CHECK"] = "secret-value"
    try:
        spec = build_container_spec(validated(settings, profiles), RUN_ID, settings)
    finally:
        del os.environ["KK_LEAK_CHECK"]
    names = sorted(item.split("=", 1)[0] for item in spec["Env"])
    assert names == ["CI", "HOME", "LANG", "PATH"]
    assert TOKEN not in json.dumps(spec)
    assert "secret-value" not in json.dumps(spec)


def test_caller_cannot_override(settings: Settings, profiles: dict[str, Profile]) -> None:
    """Only the argument values come from the request; argv, image, user and mounts never do."""
    task_workspace(settings)
    run = validated(settings, profiles, args=["tests/test_x.py"], timeout_s=90)
    spec = build_container_spec(run, RUN_ID, settings)
    assert spec["Cmd"] == ["python", "-m", "pytest", "-q", "tests/test_x.py"]
    assert spec["Cmd"][:4] == list(profiles["python.pytest"].argv)
    assert spec["Image"] == settings.images["python"]
    fields = set(RunRequest.model_fields)
    assert fields == {
        "project_id",
        "task_id",
        "workspace_subpath",
        "profile_id",
        "args",
        "timeout_s",
        "network",
        "read_only",
        "wait",
        "client_request_id",
    }


def test_read_only_run(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    spec = build_container_spec(validated(settings, profiles, read_only=True), RUN_ID, settings)
    mounts = mounts_by_target(spec)
    assert mounts["/workspace"]["ReadOnly"] is True
    assert mounts["/workspace/.kk"]["ReadOnly"] is False  # artefacts still have somewhere to go


def test_repo_run_mount_is_read_only(settings: Settings, profiles: dict[str, Profile]) -> None:
    repo_workspace(settings)
    run = validated(settings, profiles, task_id=None, workspace_subpath=f"{PROJECT}/repo")
    spec = build_container_spec(run, RUN_ID, settings)
    assert mounts_by_target(spec)["/workspace"]["ReadOnly"] is True
    assert "kk.task_id" not in spec["Labels"]


def test_runtime_runsc(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    run = validated(settings, profiles)
    assert "Runtime" not in build_container_spec(run, RUN_ID, settings)["HostConfig"]
    gvisor = replace(settings, runtime="runsc")
    assert build_container_spec(run, RUN_ID, gvisor)["HostConfig"]["Runtime"] == "runsc"


def test_egress_network(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    allowlist = replace(settings, egress="allowlist")
    run = validated(allowlist, profiles, profile_id="node.npm_test", network="egress")
    spec = build_container_spec(run, RUN_ID, allowlist)
    assert spec["HostConfig"]["NetworkMode"] == "kk-egress"
    assert spec["NetworkDisabled"] is False


def test_git_folder_is_covered_by_read_only_tmpfs(settings: Settings, profiles: dict[str, Profile]) -> None:
    (task_workspace(settings) / ".git").mkdir()
    mount = mounts_by_target(build_container_spec(validated(settings, profiles), RUN_ID, settings))[
        "/workspace/.git"
    ]
    assert mount["Type"] == "tmpfs" and mount["ReadOnly"] is True


def test_git_file_is_covered_by_empty_file(settings: Settings, profiles: dict[str, Profile]) -> None:
    (task_workspace(settings) / ".git").write_text("gitdir: /srv/x/.git/worktrees/t\n")
    mount = mounts_by_target(build_container_spec(validated(settings, profiles), RUN_ID, settings))[
        "/workspace/.git"
    ]
    assert mount == {
        "Type": "bind",
        "Source": str(empty_file(settings)),
        "Target": "/workspace/.git",
        "ReadOnly": True,
    }


@pytest.mark.parametrize("kind", ["symlink", "dangling", "fifo"])
def test_git_other_kinds_are_refused(
    settings: Settings, profiles: dict[str, Profile], tmp_path: Path, kind: str
) -> None:
    workspace = task_workspace(settings)
    target = workspace / ".git"
    if kind == "symlink":
        (tmp_path / "real-git").mkdir()
        os.symlink(tmp_path / "real-git", target)
    elif kind == "dangling":
        os.symlink(tmp_path / "missing", target)
    else:
        os.mkfifo(target)
    with pytest.raises(RequestRejected) as info:
        build_container_spec(validated(settings, profiles), RUN_ID, settings)
    assert info.value.code == "GIT_METADATA_UNSAFE"


def test_container_log_is_capped(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    big = replace(settings, max_output_bytes=10 * 1024 * 1024)
    spec = build_container_spec(validated(big, profiles), RUN_ID, big)
    assert spec["HostConfig"]["LogConfig"]["Config"] == {"max-size": str(80 * 1024 * 1024), "max-file": "2"}
