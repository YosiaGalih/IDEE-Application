"""Git errors (API_CONTRACTS §1). Neither carries git's stderr or an absolute path; stderr is only logged."""

from __future__ import annotations

from app.core.errors import AppError


class GitError(AppError):
    """git failed or was stopped (timeout, output cap, not a registered worktree, ...)."""

    def __init__(self, reason: str) -> None:
        super().__init__("GIT_ERROR", "Git operation failed", http_status=409, details=[{"reason": reason}])
        self.reason = reason


class GitInputError(AppError):
    """A branch, ref, message, author or path that the adapter refuses to pass to git."""

    def __init__(self, reason: str) -> None:
        super().__init__("BAD_REQUEST", "Invalid git input", details=[{"reason": reason}])
        self.reason = reason
