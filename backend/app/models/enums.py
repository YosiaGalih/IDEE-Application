"""Enum values shared by models and core logic (stored as VARCHAR + CHECK, ADR-018)."""

ROLES = ("owner", "admin", "developer", "reviewer", "readonly")
USER_STATUSES = ("active", "disabled")
PROJECT_STATUSES = ("active", "archived", "deleting_pending_approval", "deleted")
REPOSITORY_PROVIDERS = ("local", "template", "upload", "git_https")
REPOSITORY_SYNC_STATES = ("ready", "importing", "indexing", "error")
INDEX_ENTRY_KINDS = ("file", "symbol")  # repo_index_entries.kind (WP-2.1d)
# ARCHITECTURE §5.1 (with ADR-006, ADR-021). Only workflow.states.transition writes tasks.status.
TASK_STATUSES = (
    "draft",
    "needs_clarification",
    "planned",
    "queued",
    "running",
    "validating",
    "needs_changes",
    "waiting_approval",
    "interrupted",
    "completed",
    "failed",
    "cancelled",
    "rejected",
)
WORKFLOW_TYPES = ("create", "modify", "debug", "test", "review", "docs")
TASK_STEPS = (
    "intake",
    "context",
    "plan",
    "implement",
    "test",
    "debug",
    "review",
    "user_proxy",
    "gate",
    "apply",
)
TASK_PRIORITIES = ("low", "normal", "high")
DEPENDENCY_TYPES = ("blocks", "relates")
# Agents (DATA_MODEL M07, WP-3.1b). Values mirror the registry schema 2 (app/agents/registry.py).
AGENT_GROUPS = (
    "planning_intent",
    "software_development",
    "testing_debugging",
    "quality_security",
    "git_collaboration",
    "devops_operations",
    "documentation_knowledge",
    "specialized_engineering",
    "orchestration",
    "ai_assistant",
    "integrations",
    "portfolio",
)
AGENT_MODES = ("llm", "profile", "tool", "planned")
AGENT_TARGET_MODES = ("llm", "profile", "tool")
AGENT_PRIORITIES = ("P0", "P1", "P2")
MODEL_TIERS = ("fast", "default", "complex")
# ADR-025: approval_required is a run status of its own, not "failed" with a message.
AGENT_RUN_STATUSES = ("running", "succeeded", "failed", "cancelled", "budget_exceeded", "approval_required")
# ADR-025: "actual" is reserved for provider-reported costs.
COST_STATUSES = ("estimated", "partial", "actual", "unavailable")
