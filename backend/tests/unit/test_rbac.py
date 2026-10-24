"""RBAC matrix — API_CONTRACTS §2 (FR-003, AC-01)."""

from __future__ import annotations

import uuid

import pytest

from app.core.rbac import (
    ALL_PERMISSIONS,
    GLOBAL_ONLY_PERMISSIONS,
    RoleGrant,
    effective_permissions,
    has_permission,
)

USER = uuid.uuid4()
OTHER = uuid.uuid4()
PROJECT = uuid.uuid4()

DEVELOPER = {
    "project:read",
    "project:write",
    "task:read",
    "task:create",
    "task:control",
    "workspace:read",
    "workspace:write",
    "test:run",
    "review:read",
    "review:write",
    "approval:decide",
    "git:read",
    "git:branch",
}
REVIEWER = {
    "project:read",
    "workspace:read",
    "task:read",
    "review:read",
    "review:write",
    "approval:decide",
    "git:read",
}
READONLY = {"project:read", "workspace:read", "task:read", "review:read", "git:read"}


@pytest.mark.parametrize(
    ("role", "expected"),
    [("developer", DEVELOPER), ("reviewer", REVIEWER), ("readonly", READONLY)],
)
def test_project_role_matrix(role: str, expected: set[str]) -> None:
    grants = [RoleGrant(role, f"project:{PROJECT}")]
    perms = effective_permissions(grants, user_id=USER, project_id=PROJECT, project_owner_id=OTHER)
    assert perms == expected


def test_owner_has_everything_and_admin_everything_but_owner_transfer() -> None:
    owner = effective_permissions([RoleGrant("owner", "global")], user_id=USER)
    admin = effective_permissions([RoleGrant("admin", "global")], user_id=USER)
    assert owner == ALL_PERMISSIONS
    assert admin == ALL_PERMISSIONS - {"owner:transfer"}


def test_global_only_permissions_never_come_from_project_scope() -> None:
    grants = [RoleGrant("admin", f"project:{PROJECT}")]
    perms = effective_permissions(grants, user_id=USER, project_id=PROJECT, project_owner_id=OTHER)
    assert not perms & GLOBAL_ONLY_PERMISSIONS
    assert "project:delete" in perms


def test_global_role_does_not_reach_other_peoples_projects() -> None:
    grants = [RoleGrant("admin", "global")]
    perms = effective_permissions(grants, user_id=USER, project_id=PROJECT, project_owner_id=OTHER)
    assert perms == frozenset()
    assert not has_permission(
        grants, "project:read", user_id=USER, project_id=PROJECT, project_owner_id=OTHER
    )


def test_global_role_applies_where_user_has_a_project_role() -> None:
    grants = [RoleGrant("admin", "global"), RoleGrant("readonly", f"project:{PROJECT}")]
    perms = effective_permissions(grants, user_id=USER, project_id=PROJECT, project_owner_id=OTHER)
    assert "project:delete" in perms


def test_project_owner_is_always_owner_of_the_project() -> None:
    perms = effective_permissions([], user_id=USER, project_id=PROJECT, project_owner_id=USER)
    assert perms == ALL_PERMISSIONS - GLOBAL_ONLY_PERMISSIONS


def test_project_scope_does_not_leak_to_another_project() -> None:
    grants = [RoleGrant("developer", f"project:{PROJECT}")]
    perms = effective_permissions(grants, user_id=USER, project_id=uuid.uuid4(), project_owner_id=OTHER)
    assert perms == frozenset()


def test_developer_cannot_delete_or_deploy_production() -> None:
    grants = [RoleGrant("developer", f"project:{PROJECT}")]
    for perm in ("project:delete", "deploy:production", "admin:users"):
        assert not has_permission(grants, perm, user_id=USER, project_id=PROJECT, project_owner_id=OTHER)


def test_unknown_role_or_scope_is_rejected() -> None:
    with pytest.raises(ValueError):
        RoleGrant("superuser", "global")
    with pytest.raises(ValueError):
        RoleGrant("admin", "project:not-a-uuid")


def test_unknown_permission_is_rejected() -> None:
    with pytest.raises(ValueError):
        has_permission([RoleGrant("owner", "global")], "everything:please", user_id=USER)
