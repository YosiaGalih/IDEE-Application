"""The only place a container spec is built (ADR-009 (2), SECURITY_MODEL §4, ARCHITECTURE §8).

Nothing in the spec comes from the caller except the profile's extra arguments (already validated). Image,
user, mounts, network, limits, env and runtime come from the profile and the settings.

**Workspace mounts**
- The workspace is the single bind mount, at ``/workspace``.
- ``.git`` at its root is hidden (ADR-026 (1)):
  - a folder is covered by an empty read-only tmpfs;
  - a worktree ``.git`` file is covered by an empty read-only file;
  - anything else (a symlink, a device) is refused.
- ``/workspace/.kk`` is a fresh per-run artefact folder owned by the manager (conflict #36).
  - ADR-033 asked for a tmpfs read through the archive API, but Docker cannot copy out of a tmpfs mount
    (measured, docker 29/runc).
  - Instead it is a bind of ``<WORKSPACE_ROOT>/<project>/.kk-runs/<run_id>``: inside the project folder, so
    the per-project disk quota covers it (ARCHITECTURE §8), outside every workspace a run can mount.
  - The manager reads it after the run with ``O_NOFOLLOW``, counts it toward the growth limit (read-only
    runs too), then removes it.

**Container log.** Docker keeps a container's stdout in its own log file, whatever the manager stores. The
json-file log is capped so a run cannot fill the host disk through stdout.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from sandbox_manager.config import Settings
from sandbox_manager.validation import RequestRejected, ValidatedRun

SANDBOX_UID = "10001:10001"
WORKSPACE = "/workspace"
ARTIFACTS = "/workspace/.kk"
_ENV = ("HOME=/home/sandbox", "PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8", "CI=1")
_TMPFS = {
    "/tmp": "rw,noexec,nosuid,nodev,size=268435456,mode=1777",  # noqa: S108 - inside the container
    "/home/sandbox": "rw,nosuid,nodev,size=67108864,uid=10001,gid=10001,mode=0700",
}


def empty_file(settings: Settings) -> Path:
    return settings.private_dir / "empty"


RUNS_DIR = ".kk-runs"


def run_artifacts_dir(settings: Settings, project_id: object, run_id: str) -> Path:
    return settings.workspace_root / str(project_id) / RUNS_DIR / run_id


def log_config(settings: Settings) -> dict[str, Any]:
    """Docker's own log of the container: a few times the stored output, at least 16 MiB, two files."""
    size = max(4 * settings.max_output_bytes, 16 * 1024 * 1024)
    return {"Type": "json-file", "Config": {"max-size": str(size), "max-file": "2"}}


def git_mount(workspace: Path, settings: Settings) -> dict[str, Any] | None:
    """How ``<workspace>/.git`` is hidden from the container, or ``GIT_METADATA_UNSAFE``."""
    try:
        mode = os.lstat(workspace / ".git").st_mode
    except FileNotFoundError:
        return None
    if stat.S_ISDIR(mode):
        return {
            "Type": "tmpfs",
            "Target": f"{WORKSPACE}/.git",
            "ReadOnly": True,
            "TmpfsOptions": {"SizeBytes": 4096},
        }
    if stat.S_ISREG(mode):
        return {
            "Type": "bind",
            "Source": str(empty_file(settings)),
            "Target": f"{WORKSPACE}/.git",
            "ReadOnly": True,
        }
    raise RequestRejected("GIT_METADATA_UNSAFE", "The workspace .git is neither a folder nor a file")


def build_container_spec(run: ValidatedRun, run_id: str, settings: Settings) -> dict[str, Any]:
    """The Docker ``POST /containers/create`` body for one run."""
    image = settings.images[run.profile.image]
    mounts: list[dict[str, Any]] = [
        {
            "Type": "bind",
            "Source": str(run.workspace),
            "Target": WORKSPACE,
            "ReadOnly": run.read_only,
            "BindOptions": {"Propagation": "rprivate"},
        },
        {
            "Type": "bind",
            "Source": str(run_artifacts_dir(settings, run.project_id, run_id)),
            "Target": ARTIFACTS,
            "ReadOnly": False,
        },
    ]
    hidden = git_mount(run.workspace, settings)
    if hidden is not None:
        mounts.append(hidden)
    host_config: dict[str, Any] = {
        "NetworkMode": settings.egress_network if run.network == "egress" else "none",
        "ReadonlyRootfs": True,
        "Privileged": False,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges"],
        "PidsLimit": settings.pids_limit,
        "Memory": settings.memory_bytes,
        "MemorySwap": settings.memory_bytes,  # no swap on top of the memory limit
        "NanoCpus": int(settings.cpu_limit * 1_000_000_000),
        "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 1024}],
        "Tmpfs": dict(_TMPFS),
        "Mounts": mounts,
        "IpcMode": "private",
        "LogConfig": log_config(settings),
        "AutoRemove": False,  # removed by the manager after the log and artefacts are read
    }
    if settings.runtime:
        host_config["Runtime"] = settings.runtime
    labels = {
        "kk.managed": "1",
        "kk.run_id": run_id,
        "kk.project_id": str(run.project_id),
        "kk.profile_id": run.profile.profile_id,
    }
    if run.task_id is not None:
        labels["kk.task_id"] = str(run.task_id)
    return {
        "Image": image,
        "Cmd": [*run.profile.argv, *run.args],
        "WorkingDir": WORKSPACE,
        "User": SANDBOX_UID,
        "Env": list(_ENV),
        "Labels": labels,
        "NetworkDisabled": run.network != "egress",
        "AttachStdin": False,
        "OpenStdin": False,
        "Tty": False,
        "HostConfig": host_config,
    }
