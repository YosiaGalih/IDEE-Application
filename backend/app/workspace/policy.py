"""Which paths an access may touch (ARCHITECTURE §7, FR-025, SECURITY_MODEL T2/T5).

| Group | USER_READ | USER_WRITE | AGENT_READ (= may reach the LLM) | AGENT_WRITE |
|---|---|---|---|---|
| ``.git`` segment at any depth | deny | deny | deny | deny |
| secret files (``.env*``, keys, credential files) | allow | deny | deny | deny |
| ``.agentignore`` patterns | allow | deny | deny | deny |
| the ``.agentignore`` file itself | allow | allow | allow | deny |
| dependency / build folders | allow | allow | deny | allow |

**Matching rules**
- Secret names and dependency folders match on *every* segment, so nothing below them is reachable either.
- Secret names are compared case-insensitively.
- ``.agentignore`` uses gitignore syntax without ``!``: a user pattern can add denials, never lift the
  built-in ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase

AGENTIGNORE = ".agentignore"

_SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
)
_DEPENDENCY_DIRS = frozenset(
    {
        "node_modules",
        "dist",
        "build",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        "coverage",
        ".next",
        "target",
    }
)


class Access(StrEnum):
    USER_READ = "user_read"
    USER_WRITE = "user_write"
    AGENT_READ = "agent_read"
    AGENT_WRITE = "agent_write"


def _is_secret(segment: str) -> bool:
    lowered = segment.lower()
    return any(fnmatchcase(lowered, pattern) for pattern in _SECRET_PATTERNS)


def _translate(pattern: str) -> str:
    """One gitignore-style glob (already stripped of anchors and trailing ``/``) as a regex body."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("/.*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and (end := pattern.find("]", i + 2)) != -1:
            body = pattern[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


def _compile(line: str) -> re.Pattern[str] | None:
    pattern = line.rstrip()
    if not pattern or pattern.startswith(("#", "!")):
        return None
    pattern = pattern.rstrip("/")  # directory-only patterns also match a file of that name: stricter
    if not pattern:
        return None
    anchored = "/" in pattern
    body = _translate(pattern.lstrip("/"))
    try:
        return re.compile(body if anchored else "(?:.*/)?" + body)
    except re.error:
        return None  # an unparsable user pattern denies nothing extra; the built-in denylists still apply


@dataclass(frozen=True)
class Policy:
    agentignore: tuple[re.Pattern[str], ...] = ()

    @classmethod
    def from_agentignore(cls, text: str | None) -> Policy:
        if not text:
            return cls()
        return cls(tuple(p for line in text.splitlines() if (p := _compile(line)) is not None))

    def denied(self, parts: tuple[str, ...], access: Access) -> bool:
        if ".git" in parts:
            return True
        if access is Access.USER_READ:
            return False
        if any(_is_secret(segment) for segment in parts):
            return True
        if self._ignored(parts):
            return True
        if access is Access.AGENT_WRITE and parts == (AGENTIGNORE,):
            return True
        return access is Access.AGENT_READ and any(segment in _DEPENDENCY_DIRS for segment in parts)

    def _ignored(self, parts: tuple[str, ...]) -> bool:
        prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        return any(pattern.fullmatch(prefix) for pattern in self.agentignore for prefix in prefixes)
