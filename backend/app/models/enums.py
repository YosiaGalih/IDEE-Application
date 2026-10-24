"""Enum values shared by models and core logic (stored as VARCHAR + CHECK, ADR-018)."""

ROLES = ("owner", "admin", "developer", "reviewer", "readonly")
USER_STATUSES = ("active", "disabled")
PROJECT_STATUSES = ("active", "archived", "deleting_pending_approval", "deleted")
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
