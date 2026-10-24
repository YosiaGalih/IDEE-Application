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
  - ``**`` is aligned by an iterative DP whose cost (pattern segments * path segments) is charged to a
    budget; running out denies the path, so a hostile file can only hide files from the agent.
- A file over the caps is invalid (``AgentignoreInvalid``). The caller fails closed for agent access.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase

AGENTIGNORE = ".agentignore"
MAX_PATTERNS = 200
MAX_PATTERN_CHARS = 256
MAX_STARS = 8  # runs of "*" per pattern
MAX_GLOBSTARS = 2  # "**" segments per pattern
DENY_BUDGET = 50_000  # [Menebak] matching cells per denied() call; a few ms of work, far above real files

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


class PolicyBudgetExceeded(Exception):
    """A caller-supplied ``Budget`` ran out (``tree`` stops listing and reports a truncated result)."""


class Budget:
    """Bounded work for ``.agentignore`` matching: one unit per pattern-segment x path-segment cell."""

    def __init__(self, units: int) -> None:
        self.remaining = units

    def spend(self, units: int) -> None:
        self.remaining -= units
        if self.remaining < 0:
            raise PolicyBudgetExceeded()


@dataclass(frozen=True)
class _Glob:
    segments: tuple[re.Pattern[str] | None, ...]  # None is "**": zero or more whole segments

    def matches(self, parts: tuple[str, ...], budget: Budget) -> bool:
        """True when the pattern matches ``parts`` or an ancestor (a denied folder hides its subtree).

        Iterative DP over (pattern segment, path segment): its cost is exactly the table size, charged to
        ``budget`` up front, and no recursion depends on the path depth.
        """
        segments = self.segments
        if None not in segments:  # anchored, no "**": a straight prefix comparison
            budget.spend(len(segments))
            return len(segments) <= len(parts) and all(
                segment is not None and segment.match(part) is not None
                for segment, part in zip(segments, parts, strict=False)
            )
        size = len(parts)
        budget.spend((len(segments) + 1) * (size + 1))
        # below[si]: segments[pi + 1:] match parts[si:] (as a prefix of the remaining path)
        below = [si > 0 for si in range(size + 1)]
        for pi in range(len(segments) - 1, -1, -1):
            segment = segments[pi]
            row = [False] * (size + 1)
            for si in range(size, -1, -1):
                if segment is None:
                    row[si] = below[si] or (si < size and row[si + 1])
                else:
                    row[si] = si < size and below[si + 1] and segment.match(parts[si]) is not None
            below = row
        return below[0]


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

    def denied(self, parts: tuple[str, ...], access: Access, budget: Budget | None = None) -> bool:
        """Whether ``access`` may not touch ``parts``.

        ``.agentignore`` matching is bounded: without a ``budget`` each call gets ``DENY_BUDGET`` units and
        running out denies the path (fail closed: a hostile pattern file only hides files from the agent).
        A caller-supplied budget raises ``PolicyBudgetExceeded`` instead.
        """
        access = Access(access)  # a plain string must not slip past the identity checks below
        if any(segment.lower() == ".git" for segment in parts):
            return True
        if access is Access.USER_READ:
            return False
        if any(_is_secret(segment) for segment in parts):
            return True
        own_file = parts == (AGENTIGNORE,)
        if not (own_file and access is Access.USER_WRITE):
            try:
                if self._ignored(parts, budget or Budget(DENY_BUDGET)):
                    return True
            except PolicyBudgetExceeded:
                if budget is not None:
                    raise
                return True
        if access is Access.AGENT_WRITE:
            lowered = tuple(segment.lower() for segment in parts[:2])
            if own_file or lowered in _AGENT_WRITE_PROTECTED:
                return True
        return access is Access.AGENT_READ and any(segment in _DEPENDENCY_DIRS for segment in parts)

    def _ignored(self, parts: tuple[str, ...], budget: Budget) -> bool:
        return any(glob.matches(parts, budget) for glob in self.agentignore)
