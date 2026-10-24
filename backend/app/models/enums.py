"""Enum values shared by models and core logic (stored as VARCHAR + CHECK, ADR-018)."""

ROLES = ("owner", "admin", "developer", "reviewer", "readonly")
USER_STATUSES = ("active", "disabled")
