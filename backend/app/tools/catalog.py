"""Tool catalog (docs/TOOL_CATALOG.md): risk, executor, permission preconditions and status of every tool.

The registry loader checks allowlists against it (FR-043) and the ToolGateway (WP-3.1d) dispatches from it.
It mirrors ``TOOL_CATALOG`` in ``scripts/validate_registry.py`` (the CI validator); test_tool_catalog.py
proves they agree. ``status``: ``mvp`` (in the MVP), ``disabled`` (P1/P2, never on an enabled agent),
``planned`` (v2 design only). ``implemented`` says whether a builtin exists in this codebase yet: an agent
whose allowlist names an unimplemented tool is not ready (spec WP-3.1b goal 4).

``preconditions`` (checked against the agent's permissions): ``fs_read``, ``fs_write``, ``fs_scoped_write``,
``git_read``, ``git_branch_commit``, ``network_allowlist``, ``provider_web``, ``deployment``,
``portfolio_draft``, ``not_confidential``, ``moderation``. Every R2 tool also requires ``sandbox: allow``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Risk = Literal["R0", "R1", "R2", "R3", "-"]
ToolStatus = Literal["mvp", "disabled", "planned"]


@dataclass(frozen=True, slots=True)
class ToolInfo:
    name: str
    risk: Risk
    executor: str
    preconditions: tuple[str, ...]
    status: ToolStatus
    implemented: bool = False


# Registered by build_tool_gateway (app.tools.builtin.builtin_specs; equality is tested). Agent readiness
# (3.1b) reads this flag: an agent whose allowlist names a tool that is not here is not ready.
IMPLEMENTED = frozenset(
    {
        "repo.get_tree",
        "repo.read_file",
        "repo.search_text",
        "repo.detect_stack",
        "repo.find_related_tests",
        "git.status",
        "git.diff",
        "git.log",
        "security.scan_secrets",
    }
)


def _tool(
    name: str, risk: Risk, executor: str, preconditions: tuple[str, ...], status: ToolStatus
) -> ToolInfo:
    return ToolInfo(name, risk, executor, preconditions, status, implemented=name in IMPLEMENTED)


TOOL_CATALOG: Mapping[str, ToolInfo] = {
    tool.name: tool
    for tool in (
        _tool("repo.get_tree", "R0", "backend", ("fs_read",), "mvp"),
        _tool("repo.read_file", "R0", "backend", ("fs_read",), "mvp"),
        _tool("repo.search_text", "R0", "backend", ("fs_read",), "mvp"),
        _tool("repo.search_symbol", "R0", "backend", ("fs_read",), "mvp"),
        _tool("repo.detect_stack", "R0", "backend", (), "mvp"),
        _tool("repo.find_related_tests", "R0", "backend", ("fs_read",), "mvp"),
        _tool("git.status", "R0", "backend", ("git_read",), "mvp"),
        _tool("git.diff", "R0", "backend", ("git_read",), "mvp"),
        _tool("git.log", "R0", "backend", ("git_read",), "mvp"),
        _tool("tests.list_profiles", "R0", "backend", (), "mvp"),
        _tool("tests.get_run", "R0", "backend", (), "mvp"),
        _tool("review.get_task_diff", "R0", "backend", (), "mvp"),
        _tool("acceptance.list_criteria", "R0", "backend", (), "mvp"),
        _tool("acceptance.list_evidence", "R0", "backend", (), "mvp"),
        _tool("memory.retrieve_context", "R0", "backend", (), "mvp"),
        _tool("security.scan_secrets", "R0", "backend", (), "mvp"),
        _tool("code.write_file", "R1", "backend", ("fs_write",), "mvp"),
        _tool("code.apply_patch", "R1", "backend", ("fs_write",), "mvp"),
        _tool("code.delete_file", "R1", "backend", ("fs_scoped_write",), "mvp"),
        _tool("git.commit_task", "R1", "backend", ("git_branch_commit",), "mvp"),
        _tool("memory.store_decision", "R1", "backend", (), "mvp"),
        _tool("tests.run", "R2", "sandbox", (), "mvp"),
        _tool("env.inspect_runtime", "R2", "sandbox", (), "mvp"),
        _tool("deps.install", "R2", "sandbox", ("network_allowlist",), "disabled"),
        _tool("git.push", "R3", "worker", ("git_branch_commit",), "disabled"),
        _tool("deploy.staging", "R3", "worker", ("deployment",), "disabled"),
        _tool("submit_result", "-", "terminal", (), "mvp"),
        _tool("capabilities.list", "R0", "backend", (), "planned"),
        _tool("web.search", "R0", "provider_anthropic", ("provider_web",), "planned"),
        _tool("web.fetch", "R0", "provider_anthropic", ("provider_web",), "planned"),
        _tool(
            "code.execute_remote", "R2", "provider_anthropic", ("provider_web", "not_confidential"), "planned"
        ),
        _tool(
            "docs.render_skills", "R1", "provider_anthropic", ("provider_web", "not_confidential"), "planned"
        ),
        _tool("docs.render_local", "R2", "sandbox", (), "planned"),
        _tool("assets.store", "R1", "backend", (), "planned"),
        _tool("image.generate", "R1", "provider_image", ("moderation", "not_confidential"), "planned"),
        _tool("image.edit", "R1", "provider_image", ("moderation", "not_confidential"), "planned"),
        _tool("moderation.check_text", "R0", "backend+vendor", (), "planned"),
        _tool("moderation.check_image_prompt", "R0", "backend+vendor", (), "planned"),
        _tool("kb.search", "R0", "backend", (), "planned"),
        _tool("kb.get_chunk", "R0", "backend", (), "planned"),
        _tool("analysis.run", "R2", "sandbox", (), "planned"),
        _tool("github.list_repos", "R0", "backend", ("git_read",), "planned"),
        _tool("github.get_pr_status", "R0", "backend", ("git_read",), "planned"),
        _tool("github.get_repo_summary", "R0", "backend", ("git_read",), "planned"),
        _tool("github.import_repo", "R1", "worker", ("git_read",), "planned"),
        _tool("github.pull", "R1", "worker", ("git_read",), "planned"),
        _tool("github.push_branch", "R3", "worker", ("git_branch_commit",), "planned"),
        _tool("github.open_pr", "R3", "worker", ("git_branch_commit",), "planned"),
        _tool("portfolio.get_profile", "R0", "backend", (), "planned"),
        _tool("portfolio.draft_item", "R1", "backend", ("portfolio_draft",), "planned"),
        _tool("portfolio.privacy_check", "R0", "backend", (), "planned"),
        _tool("portfolio.build_site", "R2", "sandbox", (), "planned"),
        _tool("portfolio.publish", "R3", "backend", (), "planned"),
    )
}

# v2 R3 tools and the only agents that may hold them.
R3_OWNERS: Mapping[str, frozenset[str]] = {
    "github.push_branch": frozenset(["J48"]),
    "github.open_pr": frozenset(["J48"]),
    "portfolio.publish": frozenset(["K51"]),
}
# Tools that can back a CitedAnswer (no submit_result for those agents).
CITATION_SOURCE_TOOLS: frozenset[str] = frozenset(["kb.get_chunk", "kb.search", "web.fetch", "web.search"])
