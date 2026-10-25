"""Run request validation (API_CONTRACTS §10, SECURITY_MODEL §4 T2).

The request is untrusted input from the API/worker. Everything that shapes the container comes from the
profile and the settings; the request only picks a profile, a workspace and some argument values.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sandbox_manager.config import Settings
from sandbox_manager.profiles import Profile

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_TASK_SUBPATH = re.compile(rf"({_UUID})/tasks/({_UUID})")
_REPO_SUBPATH = re.compile(rf"({_UUID})/repo")


class RunRequest(BaseModel):
    """``POST /v1/runs`` body. Unknown fields are refused: a caller can never add HostConfig."""

    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    task_id: uuid.UUID | None = None
    workspace_subpath: str = Field(max_length=200)
    profile_id: str = Field(max_length=100)
    args: list[str] = Field(default_factory=list, max_length=100)
    timeout_s: int | None = Field(default=None, gt=0)
    network: Literal["none", "egress"] = "none"
    read_only: bool = False
    wait: bool = False
    client_request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")  # retry key


class RequestRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedRun:
    project_id: uuid.UUID
    task_id: uuid.UUID | None
    workspace: Path  # realpath on the host, under WORKSPACE_ROOT
    profile: Profile
    args: tuple[str, ...]
    timeout_s: int
    network: Literal["none", "egress"]
    read_only: bool


def validate(request: RunRequest, profiles: dict[str, Profile], settings: Settings) -> ValidatedRun:
    profile = profiles.get(request.profile_id)
    if profile is None:
        raise RequestRejected("UNKNOWN_PROFILE", "Unknown profile")

    task_match = _TASK_SUBPATH.fullmatch(request.workspace_subpath)
    repo_match = _REPO_SUBPATH.fullmatch(request.workspace_subpath)
    if task_match is None and repo_match is None:
        raise RequestRejected("INVALID_WORKSPACE", "workspace_subpath has an invalid shape")
    project = (task_match or repo_match)[1]  # type: ignore[index]
    if project != str(request.project_id):
        raise RequestRejected("INVALID_WORKSPACE", "workspace_subpath does not belong to project_id")
    if task_match is not None and (request.task_id is None or task_match[2] != str(request.task_id)):
        raise RequestRejected("INVALID_WORKSPACE", "workspace_subpath does not belong to task_id")
    read_only = request.read_only or repo_match is not None  # target main is always read-only (ADR-033)

    root = os.path.realpath(settings.workspace_root)
    real = os.path.realpath(os.path.join(root, request.workspace_subpath))
    if real != os.path.join(root, request.workspace_subpath) or not os.path.isdir(real):
        raise RequestRejected("INVALID_WORKSPACE", "workspace is missing or crosses a symlink")

    if len(request.args) > profile.max_args:
        raise RequestRejected("INVALID_ARGS", "Too many arguments")
    for arg in request.args:
        if arg.startswith("-") or not re.fullmatch(profile.args_pattern, arg):
            raise RequestRejected("INVALID_ARGS", "An argument is not allowed by the profile")

    if request.timeout_s is not None and request.timeout_s > profile.max_timeout_s:
        raise RequestRejected("INVALID_TIMEOUT", "timeout_s exceeds the profile maximum")
    timeout = request.timeout_s or profile.timeout_s or min(settings.default_timeout_s, profile.max_timeout_s)

    if request.network == "egress" and (
        profile.network != "egress-allowed" or settings.egress != "allowlist"
    ):
        raise RequestRejected("EGRESS_NOT_ALLOWED", "Egress is not allowed for this run")

    return ValidatedRun(
        request.project_id,
        request.task_id,
        Path(real),
        profile,
        tuple(request.args),
        timeout,
        request.network,
        read_only,
    )
