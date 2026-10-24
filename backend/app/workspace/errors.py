"""Workspace errors (API_CONTRACTS §1). Messages and details never carry absolute paths."""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError


class PathViolation(AppError):
    """The path is malformed, leaves the root, crosses a symlink where none is allowed, or is denied."""

    def __init__(self, reason: str) -> None:
        super().__init__("PATH_VIOLATION", "Path is not allowed", details=[{"reason": reason}])
        self.reason = reason


class WorkspaceNotFound(AppError):
    def __init__(self) -> None:
        super().__init__("NOT_FOUND", "Not found", http_status=404)


class VersionConflict(AppError):
    def __init__(self, path: str, current_sha256: str | None) -> None:
        super().__init__(
            "VERSION_CONFLICT",
            "The file changed since it was read",
            http_status=409,
            details=[{"path": path, "current_sha256": current_sha256}],
        )
        self.path = path
        self.current_sha256 = current_sha256


class AlreadyExists(AppError):
    def __init__(self) -> None:
        super().__init__("CONFLICT", "The path already exists", http_status=409)


class WorkspaceBusy(AppError):
    """Another writer held the workspace lock for longer than the wait limit."""

    def __init__(self) -> None:
        super().__init__(
            "CONFLICT", "The workspace is busy; retry", http_status=409, details=[{"reason": "busy"}]
        )


class PatchRollbackFailed(AppError):
    """A patch failed mid-write and restoring the earlier files also failed: the tree is inconsistent."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(
            "INTERNAL_ERROR",
            "The patch was partly applied and could not be rolled back",
            http_status=500,
            details=[{"path": path} for path in paths],
        )


class FileTooLarge(AppError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            "PAYLOAD_TOO_LARGE", "File too large", http_status=413, details=[{"limit_bytes": limit}]
        )


class PatchRejected(AppError):
    """The diff is malformed or uses a feature the apply path does not support."""

    def __init__(self, reason: str) -> None:
        super().__init__("BAD_REQUEST", "The patch cannot be applied", details=[{"reason": reason}])
        self.reason = reason


class PatchConflict(AppError):
    """At least one hunk does not apply; nothing was written."""

    def __init__(self, details: list[dict[str, Any]]) -> None:
        super().__init__("CONFLICT", "The patch does not apply", http_status=409, details=details)


class ArchiveRejected(AppError):
    """The whole archive is refused (FR-015); nothing from it is kept."""

    def __init__(self, reason: str) -> None:
        super().__init__("ARCHIVE_REJECTED", "The archive was rejected", details=[{"reason": reason}])
        self.reason = reason
