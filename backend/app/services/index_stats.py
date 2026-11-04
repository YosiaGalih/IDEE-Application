"""``stats.files`` of a project (API_CONTRACTS §11 #14, WP-2.1d): the files of its indexed commit."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import RepoIndexEntry, Repository


async def indexed_file_count(session: AsyncSession, repository: Repository | None) -> int | None:
    """``None`` until the repository was indexed once; then the number of files in that index."""
    if repository is None or repository.last_indexed_at is None:
        return None
    count = await session.scalar(
        select(func.count())
        .select_from(RepoIndexEntry)
        .where(RepoIndexEntry.project_id == repository.project_id, RepoIndexEntry.kind == "file")
    )
    return int(count or 0)
