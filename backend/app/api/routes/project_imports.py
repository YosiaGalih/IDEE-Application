"""``POST /projects/{project_id}/imports`` (API_CONTRACTS §3, §11 #14; FR-015; WP-2.1b).

The archive is streamed: the multipart body is parsed as it arrives (``python-multipart``'s push parser) and
the file part goes straight to ``ArchiveSink`` on the workspace volume. Nothing is held in memory or in the
container's ``/tmp``. The idempotency middleware lets this path through unbuffered (``stream_paths``).

Every refusal that does not need the file (scope, state, lock, rate) comes from ``begin_import`` before the
first byte is read.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from python_multipart import MultipartParser
from python_multipart.exceptions import ParseError
from python_multipart.multipart import parse_options_header

from app.api.deps import AuditDep, LimiterDep, dependencies
from app.api.routes.projects import ProjectWriter, RepositoryOut, _respond, project_env
from app.core.errors import AppError
from app.services import project_imports
from app.services.project_imports import ArchiveSink, ImportEnv, UploadInfo

router = APIRouter(prefix="/projects", tags=["projects"])

FIELD = "file"
# Headers, boundaries and the closing delimiter around the file; a body past this is not an archive upload.
MULTIPART_OVERHEAD = 1024 * 1024


def _invalid(message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": ["body", FIELD], "msg": message}],
    )


def import_env(request: Request, audit: AuditDep, limiter: LimiterDep) -> ImportEnv:
    deps = dependencies(request)
    if deps.jobs is None:
        raise AppError("DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable", http_status=503)
    return ImportEnv(projects=project_env(request, audit, limiter), redis=deps.redis, jobs=deps.jobs)


ImportEnvDep = Annotated[ImportEnv, Depends(import_env)]


def _boundary(content_type: str | None) -> bytes:
    kind, params = parse_options_header(content_type or "")
    boundary = params.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise _invalid("expected multipart/form-data with one file field")
    return boundary


@dataclass
class _Parts:
    """What the parser saw, collected by its (synchronous) callbacks and checked between writes."""

    header_field: bytearray = field(default_factory=bytearray)
    header_value: bytearray = field(default_factory=bytearray)
    headers: dict[bytes, bytes] = field(default_factory=dict)
    in_file: bool = False
    files: int = 0
    filename: str | None = None
    pending: list[bytes] = field(default_factory=list)
    error: str | None = None
    ended: bool = False

    def on_part_begin(self) -> None:
        self.headers = {}
        self.in_file = False

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self.header_field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self.header_value += data[start:end]

    def on_header_end(self) -> None:
        self.headers[bytes(self.header_field).lower()] = bytes(self.header_value)
        self.header_field.clear()
        self.header_value.clear()

    def on_headers_finished(self) -> None:
        disposition, params = parse_options_header(self.headers.get(b"content-disposition", b""))
        filename = params.get(b"filename")
        if disposition != b"form-data" or params.get(b"name") != FIELD.encode() or filename is None:
            self.error = "only one field is accepted: file, with a file name"
            return
        self.files += 1
        if self.files > 1:
            self.error = "exactly one file is accepted"
            return
        self.in_file = True
        self.filename = filename.decode("utf-8", "replace")

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self.in_file and self.error is None:
            self.pending.append(bytes(data[start:end]))

    def on_part_end(self) -> None:
        self.in_file = False

    def on_end(self) -> None:
        self.ended = True


async def _receive(request: Request, boundary: bytes, sink: ArchiveSink) -> UploadInfo:
    parts = _Parts()
    parser = MultipartParser(
        boundary,
        callbacks={
            "on_part_begin": parts.on_part_begin,
            "on_header_field": parts.on_header_field,
            "on_header_value": parts.on_header_value,
            "on_header_end": parts.on_header_end,
            "on_headers_finished": parts.on_headers_finished,
            "on_part_data": parts.on_part_data,
            "on_part_end": parts.on_part_end,
            "on_end": parts.on_end,
        },
    )
    limit = project_imports.IMPORT_MAX_BYTES + MULTIPART_OVERHEAD
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise AppError("PAYLOAD_TOO_LARGE", "Request body too large", http_status=413)
        try:
            parser.write(chunk)
        except ParseError:
            raise _invalid("malformed multipart body") from None
        if parts.error is not None:
            raise _invalid(parts.error)
        for data in parts.pending:
            await sink.write(data)
        parts.pending.clear()
    try:
        parser.finalize()
    except ParseError:
        raise _invalid("malformed multipart body") from None
    if not parts.ended or parts.files != 1:
        raise _invalid("exactly one file is accepted")
    return await sink.close(parts.filename)


@router.post("/{project_id}/imports")
async def import_archive(
    request: Request, project_id: uuid.UUID, actor: ProjectWriter, env: ImportEnvDep
) -> JSONResponse:
    boundary = _boundary(request.headers.get("content-type"))
    slot = await project_imports.begin_import(env, actor, project_id)
    try:
        upload = await _receive(request, boundary, ArchiveSink(slot))
        accepted = await project_imports.finish_import(env, actor, slot, upload)
    except BaseException:
        # Client gone, limit, type, bad multipart, cancellation: nothing of this upload may stay behind.
        await project_imports.abort_import(env, slot)
        raise
    data = {
        "repository": RepositoryOut.of(accepted.repository).model_dump(mode="json"),
        "job_id": str(accepted.job_id),
        "upload": {
            "filename": accepted.upload.filename,
            "size": accepted.upload.size,
            "sha256": accepted.upload.sha256,
        },
    }
    return _respond(request, data, status_code=202, status="accepted")
