"""Which paths an access may touch (ARCHITECTURE §7, FR-025, SECURITY_MODEL T2/T5, TOOL_CATALOG R4).

| Group | USER_READ | USER_WRITE | AGENT_READ (= may reach the LLM) | AGENT_WRITE |
|---|---|---|---|---|
| ``.git`` segment at any depth (any case) | deny | deny | deny | deny |
| secret files and folders (``.env*``, keys, credential stores) | allow | deny | deny | deny |
| ``.agentignore`` patterns | allow | deny | deny | deny |
| the ``.agentignore`` file itself | allow | allow | allow | deny |
| ``.github/workflows``, ``.github/actions`` (R4) | allow | allow | allow | deny |
| dependency / build folders | allow | allow | deny | allow |

**Matching rules**
- Secret names and dependency folders match on *every* segment, so nothing below them is reachable either.
- Secret names are compared case-insensitively.

**``.agentignore``**
- Gitignore syntax without ``!``: a user pattern can add denials, never lift the built-in ones.
- The file is untrusted (a sandbox can write a worktree), so matching has bounded cost:
  - patterns are capped in number, length and wildcards;
  - each path segment is matched with ``fnmatch.translate`` (CPython's non-backtracking translation);
  - ``**`` is aligned by a memoised walk, O(pattern segments * path segments).
- A file over the caps is invalid (``AgentignoreInvalid``). The caller fails closed for agent access.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from functools import cache

AGENTIGNORE = ".agentignore"
MAX_PATTERNS = 1000
MAX_PATTERN_CHARS = 256
MAX_STARS = 8  # runs of "*" per pattern
MAX_GLOBSTARS = 4  # "**" segments per pattern

_SECRET_PATTERNS = (
    ".env*",
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
    "*.ppk",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".pgpass",
    ".htpasswd",
    ".aws",
    ".docker",
    ".kube",
    ".ssh",
    ".gnupg",
)
_AGENT_WRITE_PROTECTED = ((".github", "workflows"), (".github", "actions"))  # TOOL_CATALOG R4
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


class AgentignoreInvalid(ValueError):
    """The ``.agentignore`` exceeds the caps that keep its matching cost bounded."""


def _is_secret(segment: str) -> bool:
    lowered = segment.lower()
    return any(fnmatchcase(lowered, pattern) for pattern in _SECRET_PATTERNS)


@dataclass(frozen=True)
class _Glob:
    segments: tuple[re.Pattern[str] | None, ...]  # None is "**": zero or more whole segments

    def matches(self, parts: tuple[str, ...]) -> bool:
        """True when the pattern matches ``parts`` or an ancestor (a denied folder hides its subtree)."""
        segments = self.segments

        @cache
        def walk(pi: int, si: int) -> bool:
            if pi == len(segments):
                return si > 0
            segment = segments[pi]
            if segment is None:
                return walk(pi + 1, si) or (si < len(parts) and walk(pi, si + 1))
            return si < len(parts) and segment.match(parts[si]) is not None and walk(pi + 1, si + 1)

        return walk(0, 0)


def _compile(line: str) -> _Glob | None:
    text = line.rstrip()
    if not text or text.startswith(("#", "!")):
        return None
    if text.startswith(("\\#", "\\!")):
        text = text[1:]
    if len(text) > MAX_PATTERN_CHARS or len(re.findall(r"\*+", text)) > MAX_STARS:
        raise AgentignoreInvalid("pattern too complex")
    text = text.rstrip("/")  # directory-only patterns also match a file of that name: stricter
    anchored = "/" in text
    segments: list[re.Pattern[str] | None] = []
    for raw in (piece for piece in text.split("/") if piece):
        if raw == "**":
            if not segments or segments[-1] is not None:
                segments.append(None)
            continue
        segments.append(re.compile(fnmatch.translate(re.sub(r"\*{2,}", "*", raw))))
    if not segments:
        return None
    if not anchored and segments[0] is not None:
        segments.insert(0, None)
    if sum(segment is None for segment in segments) > MAX_GLOBSTARS:
        raise AgentignoreInvalid("too many **")
    return _Glob(tuple(segments))


@dataclass(frozen=True)
class Policy:
    agentignore: tuple[_Glob, ...] = ()

    @classmethod
    def from_agentignore(cls, text: str | None) -> Policy:
        """Parse ``.agentignore``; ``AgentignoreInvalid`` when it is over the caps."""
        if not text:
            return cls()
        globs = tuple(g for line in text.splitlines() if (g := _compile(line)) is not None)
        if len(globs) > MAX_PATTERNS:
            raise AgentignoreInvalid("too many patterns")
        return cls(globs)

    def denied(self, parts: tuple[str, ...], access: Access) -> bool:
        access = Access(access)  # a plain string must not slip past the identity checks below
        if any(segment.lower() == ".git" for segment in parts):
            return True
        if access is Access.USER_READ:
            return False
        if any(_is_secret(segment) for segment in parts):
            return True
        own_file = parts == (AGENTIGNORE,)
        if not (own_file and access is Access.USER_WRITE) and self._ignored(parts):
            return True
        if access is Access.AGENT_WRITE:
            lowered = tuple(segment.lower() for segment in parts[:2])
            if own_file or lowered in _AGENT_WRITE_PROTECTED:
                return True
        return access is Access.AGENT_READ and any(segment in _DEPENDENCY_DIRS for segment in parts)

    def _ignored(self, parts: tuple[str, ...]) -> bool:
        return any(glob.matches(parts) for glob in self.agentignore)
