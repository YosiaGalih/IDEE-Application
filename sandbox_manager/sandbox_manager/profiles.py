"""Fixed run profiles (``sandbox-images/sandbox-profiles.yaml``, mounted read-only; ADR-009 (2)).

A caller only ever names a ``profile_id``. The image, the command and the network all come from here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    image: str  # an alias resolved through Settings.images, never an image reference
    suite: str
    argv: tuple[str, ...] = Field(min_length=1)
    args_pattern: str  # every extra argument must fullmatch this
    max_args: int = Field(default=20, ge=0, le=100)
    timeout_s: int | None = Field(default=None, gt=0)  # None → SANDBOX_TIMEOUT_SECONDS, capped at the max
    max_timeout_s: int = Field(gt=0)
    network: Literal["none", "egress-allowed"] = "none"
    # Files the profile writes to /workspace/.kk. Exit 0 without one of them ends the run as an error
    # (ARTIFACT_MISSING/ARTIFACT_REJECTED): "no report" is never mistaken for "no tests".
    artifacts: tuple[Literal["junit", "coverage"], ...] = ()

    @field_validator("profile_id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _PROFILE_ID.fullmatch(value):
            raise ValueError("profile_id must look like <pack>.<name>")
        return value

    @field_validator("args_pattern")
    @classmethod
    def _pattern(cls, value: str) -> str:
        re.compile(value)
        return value

    @model_validator(mode="after")
    def _timeouts(self) -> Profile:
        if self.timeout_s is not None and self.timeout_s > self.max_timeout_s:
            raise ValueError("timeout_s exceeds max_timeout_s")
        return self


class ProfileFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    profiles: list[Profile]


def load_profiles(path: Path, image_aliases: set[str]) -> dict[str, Profile]:
    data = ProfileFile.model_validate(yaml.safe_load(path.read_text()))
    profiles: dict[str, Profile] = {}
    for profile in data.profiles:
        if profile.profile_id in profiles:
            raise ValueError(f"duplicate profile {profile.profile_id}")
        if profile.image not in image_aliases:
            raise ValueError(f"profile {profile.profile_id} uses an unknown image alias")
        profiles[profile.profile_id] = profile
    return profiles
