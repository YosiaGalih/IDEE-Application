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
# agent_runs.error_code (spec WP-3.1f Keputusan Lead #15, #35): set exactly when a run failed. internal_error
# is an unexpected internal failure; worker_cancelled an asyncio cancel of the runtime from outside;
# interrupted is written by recovery for a run its worker left behind (WP-3.1f2b).
AGENT_RUN_ERROR_CODES = (
    "PROVIDER_DISABLED",
    "provider_not_ready",
    "provider_auth",
    "usage_unrecorded",
    "invalid_output",
    "iteration_limit",
    "timeout",
    "lease_lost",
    "model_error",
    "internal_error",
    "worker_cancelled",
    "interrupted",
)
# ADR-025: "actual" is reserved for provider-reported costs.
COST_STATUSES = ("estimated", "partial", "actual", "unavailable")
# DATA_MODEL approvals (v1 CHECK). waive_criterion stays in the CHECK but is never created (ADR-034).
APPROVAL_ACTION_TYPES = (
    "apply_changes",
    "budget_increase",
    "tool_r3",
    "project_delete",
    "deploy_staging",
    "deploy_production",
    "git_push",
    "waive_criterion",
)
APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "consumed", "superseded")
# ADR-024: a decision is committed before its handler runs; these track the handler.
HANDLER_STATUSES = ("pending", "succeeded", "failed", "not_required")
# DATA_MODEL tool_calls + running (the claim before a side-effect handler) and cancelled (spec WP-3.1d,
# Keputusan Lead 4).
TOOL_CALL_STATUSES = ("running", "ok", "error", "denied", "timeout", "approval_required", "cancelled")
# DATA_MODEL provider_configs (ADR-004, ADR-032; spec WP-3.1e Goal 1).
PROVIDERS = ("anthropic",)
READINESS_STATUSES = ("unknown", "passed", "failed")
SECRET_STATUSES = ("unknown", "configured", "missing")  # as last seen by the worker (spec 3.1e Keputusan #4)
LLM_PROVIDER_MODES = ("anthropic", "scripted")
# Usage ledger (spec WP-3.1f Keputusan Lead #3): one row per attempt. ``app.models`` cannot import
# ``app.llm``, so these copy ``AttemptRecord.status`` and ``app.llm.base.ErrorKind``;
# tests/unit/test_pricing_usage.py keeps them equal.
USAGE_ATTEMPT_STATUSES = ("ok", "error", "cancelled")
LLM_ERROR_KINDS = (
    "rate_limited",
    "overloaded",
    "transient",
    "timeout",
    "refusal",
    "bad_request",
    "auth",
    "malformed",
    "cancelled",
    "max_tokens",
)
