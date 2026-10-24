"""Safe archive import (ARCHITECTURE §7, FR-015, AC-02, ADR-005; SECURITY_MODEL T2, T4).

``import_archive(archive, dest)`` extracts zip, tar or tar.gz into a project folder that does not exist yet.

**All or nothing**
- Extraction goes into a staging folder next to ``dest``, which is renamed to ``dest`` only on success.
- Any violation rejects the whole archive (``ArchiveRejected``) and removes the staging folder.

**What is refused**
- Bad names: absolute, ``..``, NUL, backslash, over-long; or any ``.git`` segment.
- Types other than regular files and folders: symlinks, hardlinks, devices, FIFOs.
- The same file twice, or a file where a folder is needed.
- Too many entries, too many bytes, or a compression ratio above the limit.

**How limits are counted**
- Byte limits use the bytes actually decompressed, never sizes claimed by headers, so a bomb stops early.
- Entries are created through the WP-1.2a primitives (fd walk, ``O_EXCL|O_NOFOLLOW``), never by path string.

**Wrapping folder.** A single top-level folder (as in GitHub's "Download ZIP") is stripped only when the
caller asks (``strip_single_root=True``). Stripping by default would silently turn a project whose only
top-level entry is ``src/`` into a different layout.
"""

from __future__ import annotations

import contextlib
import errno
import gzip
import os
import secrets
import shutil
import stat
import struct
import tarfile
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.workspace.errors import AlreadyExists, ArchiveRejected, PathViolation
from app.workspace.paths import open_parent, split_rel

MiB = 1024 * 1024
_CHUNK = 64 * 1024
_PROGRESS_EVERY = 100
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_ZIP_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)  # streamed with bounded reads by zipfile
# Errors raised by the decoders for a damaged or unsupported archive. Plain OSError is not here: a write
# failure (disk full) is a real error, not a bad archive.
_FORMAT_ERRORS = (
    zipfile.BadZipFile,
    tarfile.TarError,
    EOFError,
    zlib.error,
    gzip.BadGzipFile,
    NotImplementedError,
    RuntimeError,
    ValueError,  # includes UnicodeDecodeError for a name flagged UTF-8 that is not
    struct.error,
)


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int = 200 * MiB
    max_total_bytes: int = 1024 * MiB
    max_entries: int = 50_000
    max_ratio: int = 100
    # [Menebak] below this much output a high ratio is repetitive text, not a bomb
    ratio_floor_bytes: int = MiB


@dataclass(frozen=True)
class ImportReport:
    files: int
    dirs: int
    bytes: int
    stripped_prefix: str | None


class _Extractor:
    def __init__(
        self,
        staging_fd: int,
        limits: ArchiveLimits,
        archive_size: int,
        on_progress: Callable[[int, int], None] | None,
    ) -> None:
        self.fd = staging_fd
        self.limits = limits
        self.archive_size = archive_size
        self.on_progress = on_progress
        self.kinds: dict[tuple[str, ...], str] = {}
        self.entries = 0
        self.files = 0
        self.bytes = 0
        self.streamed = 0

    # --- limits ---

    def count_entry(self) -> None:
        self.entries += 1
        if self.entries > self.limits.max_entries:
            raise ArchiveRejected("too_many_files")

    def check_output(self, total: int) -> None:
        if total > self.limits.max_total_bytes:
            raise ArchiveRejected("oversize")
        if total > self.limits.ratio_floor_bytes and total > self.limits.max_ratio * self.archive_size:
            raise ArchiveRejected("ratio")

    def stream_read(self, source: _Readable) -> Callable[[int], bytes]:
        """``read`` for a decompressed stream; every byte counts, headers included (pax headers too)."""

        def read(size: int = -1) -> bytes:
            data = source.read(size)
            self.streamed += len(data)
            self.check_output(self.streamed)
            return data

        return read

    # --- entries ---

    def entry_parts(self, name: str) -> tuple[str, ...]:
        try:
            parts = split_rel(name)
        except PathViolation as exc:
            raise ArchiveRejected(
                exc.reason if exc.reason in ("absolute", "traversal") else "invalid_name"
            ) from None
        if any(segment.lower() == ".git" for segment in parts):
            raise ArchiveRejected("git_dir")
        return parts

    def _claim_parents(self, parts: tuple[str, ...]) -> None:
        for i in range(1, len(parts)):
            if self.kinds.setdefault(parts[:i], "dir") != "dir":
                raise ArchiveRejected("duplicate")

    def add_dir(self, parts: tuple[str, ...]) -> None:
        if not parts:
            return
        self._claim_parents(parts)
        if self.kinds.setdefault(parts, "dir") != "dir":
            raise ArchiveRejected("duplicate")
        os.close(open_parent(self.fd, (*parts, ""), create=True))

    def add_file(self, parts: tuple[str, ...], read: Callable[[int], bytes], executable: bool) -> None:
        if not parts:
            raise ArchiveRejected("invalid_name")
        self._claim_parents(parts)
        if parts in self.kinds:
            raise ArchiveRejected("duplicate")
        self.kinds[parts] = "file"
        parent = open_parent(self.fd, parts, create=True)
        try:
            try:
                out = os.open(parts[-1], _FILE_FLAGS, 0o600, dir_fd=parent)
            except FileExistsError:
                raise ArchiveRejected("duplicate") from None
            with os.fdopen(out, "wb") as handle:
                while chunk := read(_CHUNK):
                    self.bytes += len(chunk)
                    self.check_output(self.bytes)
                    handle.write(chunk)
                os.fchmod(handle.fileno(), 0o755 if executable else 0o644)
        finally:
            os.close(parent)
        self.files += 1
        if self.on_progress and self.files % _PROGRESS_EVERY == 0:
            self.on_progress(self.files, self.bytes)

    # --- formats ---

    def extract_zip(self, path: Path) -> None:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > self.limits.max_entries:
                raise ArchiveRejected("too_many_files")
            if sum(info.file_size for info in infos) > self.limits.max_total_bytes:
                raise ArchiveRejected("oversize")
            for info in infos:
                self.count_entry()
                parts = self.entry_parts(info.filename)
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind == stat.S_IFLNK:
                    raise ArchiveRejected("link")
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ArchiveRejected("device")
                if info.flag_bits & 0x1:
                    raise ArchiveRejected("format")  # encrypted
                if info.compress_type not in _ZIP_METHODS:
                    # zipfile inflates BZIP2/LZMA members in one unbounded call: the byte counter would
                    # only see the output after it had already been allocated.
                    raise ArchiveRejected("format")
                if info.is_dir() or kind == stat.S_IFDIR:
                    self.add_dir(parts)
                    continue
                ratio = info.file_size / max(info.compress_size, 1)
                if info.file_size > self.limits.ratio_floor_bytes and ratio > self.limits.max_ratio:
                    raise ArchiveRejected("ratio")
                with zf.open(info) as source:
                    self.add_file(parts, source.read, bool(mode & 0o111))

    def extract_tar(self, path: Path, *, compressed: bool) -> None:
        with path.open("rb") as raw, contextlib.ExitStack() as stack:
            source: _Readable = raw
            if compressed:
                source = stack.enter_context(gzip.GzipFile(fileobj=raw))
            counted = _Reader(self.stream_read(source))
            with tarfile.open(fileobj=counted, mode="r|") as tf:  # type: ignore[call-overload]
                for member in tf:
                    self.count_entry()
                    parts = self.entry_parts(member.name)
                    if member.issym() or member.islnk():
                        raise ArchiveRejected("link")
                    if member.isdir():
                        self.add_dir(parts)
                        continue
                    if not member.isreg():
                        raise ArchiveRejected("device")
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        raise ArchiveRejected("format")
                    self.add_file(parts, extracted.read, bool(member.mode & 0o111))


class _Reader:
    """The minimal file object ``tarfile`` stream mode needs."""

    def __init__(self, read: Callable[[int], bytes]) -> None:
        self.read = read


def _detect(path: Path) -> str:
    with path.open("rb") as handle:
        head = handle.read(512)
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "tar.gz"
    if head[257:262] == b"ustar":
        return "tar"
    raise ArchiveRejected("format")


def import_archive(
    archive: Path,
    dest: Path,
    *,
    limits: ArchiveLimits = ArchiveLimits(),  # noqa: B008 - frozen dataclass, safe as a default
    strip_single_root: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> ImportReport:
    """Extract ``archive`` into the new folder ``dest`` or raise ``ArchiveRejected`` leaving nothing."""
    if os.path.lexists(dest):
        raise AlreadyExists()
    size = archive.stat().st_size
    if size > limits.max_archive_bytes:
        raise ArchiveRejected("oversize")
    kind = _detect(archive)

    staging = dest.parent / f".{dest.name}.importing-{secrets.token_hex(6)}"
    os.mkdir(staging, 0o700)
    try:
        staging_fd = os.open(staging, _DIR_FLAGS)
        try:
            extractor = _Extractor(staging_fd, limits, size, on_progress)
            try:
                if kind == "zip":
                    extractor.extract_zip(archive)
                else:
                    extractor.extract_tar(archive, compressed=kind == "tar.gz")
            except _FORMAT_ERRORS:
                raise ArchiveRejected("format") from None
            except OSError as exc:
                if exc.errno == errno.EINVAL:  # zipfile seeking to a corrupt offset; disk errors stay real
                    raise ArchiveRejected("format") from None
                raise
            stripped = _single_root(staging_fd) if strip_single_root else None
        finally:
            os.close(staging_fd)
        source = staging / stripped if stripped else staging
        if os.path.lexists(dest):
            raise AlreadyExists()
        os.rename(source, dest)
        _fchmod_dir(dest, 0o755)
    finally:
        if os.path.lexists(staging):
            shutil.rmtree(staging)
    if on_progress:
        on_progress(extractor.files, extractor.bytes)
    dirs = sum(1 for k in extractor.kinds.values() if k == "dir") - (1 if stripped else 0)
    return ImportReport(extractor.files, dirs, extractor.bytes, stripped)


def _single_root(staging_fd: int) -> str | None:
    names = os.listdir(staging_fd)
    if len(names) != 1:
        return None
    mode = os.stat(names[0], dir_fd=staging_fd, follow_symlinks=False).st_mode
    return names[0] if stat.S_ISDIR(mode) else None


def _fchmod_dir(path: Path, mode: int) -> None:
    fd = os.open(path, _DIR_FLAGS)
    try:
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
