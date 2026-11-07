"""Tool policy: what a permission profile allows (SECURITY_MODEL §3, TOOL_CATALOG; spec WP-3.1d Goal 2).

One source for two checkers: the registry loader refuses an agent whose allowlist its permissions cannot
satisfy (FR-043), and the ToolGateway refuses the call again at run time (the enforcement point).
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Iterable, Mapping
from fnmatch import fnmatchcase
from types import MappingProxyType

from app.tools.base import PermissionView
from app.tools.catalog import ToolInfo

# TOOL_CATALOG "Permission profile": the paths each restricted filesystem mode may write.
TEST_ONLY_PATTERNS = (
    "tests/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/*.spec.ts",
    "**/*.spec.tsx",
    "**/__tests__/**",
)
DOCS_ONLY_PATTERNS = ("**/*.md", "docs/**")
WRITE_MODES = frozenset({"test_only", "docs_only", "scoped_write"})

# R4, never registered (SECURITY_MODEL §3, T19): host shell or terminal, Docker, reading .env/keys/secrets,
# changing audit or policy, creating or approving approvals, direct DB access, admin keys, SSO/SCIM,
# force-push.
R4_PREFIXES = (
    "shell.",
    "terminal.",
    "host.",
    "docker.",
    "audit.",
    "policy.",
    "approval.",
    "approvals.",
    "db.",
    "sql.",
    "admin.",
    "sso.",
    "scim.",
    "secrets.",
)
R4_NAMES = frozenset({"env.read", "env.read_file", "git.force_push", "git.push_force", "github.force_push"})
R4_FRAGMENTS = ("force_push", "approve", "exec_host", "sudo", "read_secret", "read_env", "read_key")


def is_r4(name: str) -> bool:
    return name.startswith(R4_PREFIXES) or name in R4_NAMES or any(f in name for f in R4_FRAGMENTS)


_Check = Callable[[PermissionView, frozenset[str]], bool]
PRECONDITIONS: Mapping[str, _Check] = MappingProxyType(
    {
        "fs_read": lambda p, _: p.filesystem != "none",
        "fs_write": lambda p, _: p.filesystem in WRITE_MODES,
        "fs_scoped_write": lambda p, _: p.filesystem == "scoped_write",
        "git_read": lambda p, _: p.git in {"read", "branch_commit"},
        "git_branch_commit": lambda p, _: p.git == "branch_commit",
        "network_allowlist": lambda p, _: p.network == "allowlist",
        "provider_web": lambda p, _: p.network == "provider_web",
        "deployment": lambda p, _: p.deployment == "staging_with_approval",
        "portfolio_draft": lambda p, _: p.portfolio == "draft",
        "not_confidential": lambda p, _: p.data_sensitivity_max != "confidential",
        "moderation": lambda _, tools: "moderation.check_image_prompt" in tools,
    }
)
SANDBOX = "sandbox_allow"


def unmet_preconditions(tool: ToolInfo, permissions: PermissionView, tools: frozenset[str]) -> list[str]:
    """The catalog preconditions this profile does not meet, plus ``sandbox_allow`` for an R2 tool without
    ``sandbox: allow``. Empty: the profile may call the tool. An unknown precondition counts as unmet."""
    unmet = [
        pre
        for pre in tool.preconditions
        if not (pre in PRECONDITIONS and PRECONDITIONS[pre](permissions, tools))
    ]
    if tool.risk == "R2" and permissions.sandbox != "allow":
        unmet.append(SANDBOX)
    return unmet


def _normal(path: str) -> str | None:
    """A relative POSIX path without ``.``/``..`` segments, or ``None`` (never allowed to write)."""
    if not path or "\x00" in path or "\\" in path or path.startswith("/"):
        return None
    normal = posixpath.normpath(path)
    if normal in {".", ".."} or normal.startswith("../") or normal != path.removeprefix("./").rstrip("/"):
        return None
    return normal


def _match(segments: list[str], pattern: list[str]) -> bool:
    """Gitignore-style: ``**`` spans zero or more segments; other segments use ``fnmatchcase``."""
    if not pattern:
        return not segments
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_match(segments[i:], rest) for i in range(len(segments) + 1))
    return bool(segments) and fnmatchcase(segments[0], head) and _match(segments[1:], rest)


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    normal = _normal(path)
    if normal is None:
        return False
    segments = normal.split("/")
    return any(_match(segments, pattern.split("/")) for pattern in patterns)


def write_allowed(filesystem: str, path: str) -> bool:
    """Whether this filesystem mode may write ``path``. ``scoped_write`` allows every valid relative path:
    the write denylist (``.git``, secrets, ``.agentignore``, workflows) is enforced by the workspace."""
    if _normal(path) is None:
        return False
    if filesystem == "scoped_write":
        return True
    if filesystem == "test_only":
        return matches_any(path, TEST_ONLY_PATTERNS)
    if filesystem == "docs_only":
        return matches_any(path, DOCS_ONLY_PATTERNS)
    return False
