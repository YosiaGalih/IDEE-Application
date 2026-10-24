"""Archive import rejects every unsafe archive and leaves nothing behind (FR-015, AC-02, T2, T4).

Fixtures are built in ``tmp_path`` by the builders below (TEST_STRATEGY §4: generated, never binaries in git).
"""

from __future__ import annotations

import contextlib
import gzip
import io
import os
import stat
import struct
import tarfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from app.workspace.archive import ArchiveLimits, ImportReport, import_archive
from app.workspace.errors import AlreadyExists, ArchiveRejected

MiB = 1024 * 1024
Builder = Callable[[Path], tuple[Path, ArchiveLimits]]
DEFAULT = ArchiveLimits()


def _zip(path: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]], *, deflate: bool = True) -> Path:
    method = zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w", compression=method) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return path


def _tar(path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]], *, gz: bool = True) -> Path:
    with tarfile.open(path, "w:gz" if gz else "w") as tf:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def _member(
    name: str, kind: bytes = tarfile.REGTYPE, *, linkname: str = "", mode: int = 0o644
) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    info.mode = mode
    return info


def _link_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def traversal(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _zip(tmp / "traversal.zip", [("../../etc/passwd", b"root:x:0:0")]), DEFAULT


def absolute(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _tar(tmp / "absolute.tar.gz", [(_member("/etc/x"), b"x")]), DEFAULT


def symlink(tmp: Path) -> tuple[Path, ArchiveLimits]:
    link = _member("link", tarfile.SYMTYPE, linkname="/")
    return _tar(tmp / "symlink_escape.tar.gz", [(link, None)]), DEFAULT


def zip_symlink(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _zip(tmp / "zip_symlink.zip", [(_link_info("link"), b"/")]), DEFAULT


def hardlink(tmp: Path) -> tuple[Path, ArchiveLimits]:
    members = [(_member("a.txt"), b"a"), (_member("hard", tarfile.LNKTYPE, linkname="a.txt"), None)]
    return _tar(tmp / "hardlink.tar.gz", members), DEFAULT


def device(tmp: Path) -> tuple[Path, ArchiveLimits]:
    dev = _member("dev", tarfile.CHRTYPE)
    dev.devmajor, dev.devminor = 1, 3
    return _tar(tmp / "device.tar", [(dev, None)], gz=False), DEFAULT


def fifo(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _tar(tmp / "fifo.tar", [(_member("pipe", tarfile.FIFOTYPE), None)], gz=False), DEFAULT


def zip_bomb(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _zip(tmp / "zip_bomb.zip", [("zeros.bin", b"\0" * (20 * MiB))]), DEFAULT


def gzip_bomb(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _tar(tmp / "gzip_bomb.tar.gz", [(_member("zeros.bin"), b"\0" * (20 * MiB))]), DEFAULT


def pax_bomb(tmp: Path) -> tuple[Path, ArchiveLimits]:
    """A tiny tar.gz whose pax header (not file data) expands to 20 MiB: only the stream counter sees it."""
    path = tmp / "pax_bomb.tar.gz"
    info = _member("a.txt")
    info.pax_headers = {"comment": "A" * (20 * MiB)}
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        info.size = 1
        tf.addfile(info, io.BytesIO(b"a"))
    return path, DEFAULT


def too_many_files(tmp: Path) -> tuple[Path, ArchiveLimits]:
    path = tmp / "too_many_files.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(DEFAULT.max_entries + 1):
            zf.writestr(f"f{i:05}", b"")
    return path, DEFAULT


def oversize(tmp: Path) -> tuple[Path, ArchiveLimits]:
    path = _zip(tmp / "oversize.zip", [("r.bin", os.urandom(2048))], deflate=False)
    return path, ArchiveLimits(max_archive_bytes=1024)


def total_oversize(tmp: Path) -> tuple[Path, ArchiveLimits]:
    path = _zip(tmp / "total.zip", [("r.bin", os.urandom(2 * MiB))], deflate=False)
    return path, ArchiveLimits(max_total_bytes=MiB)


def overwrite(tmp: Path) -> tuple[Path, ArchiveLimits]:
    members = [(_member("a.txt"), b"first"), (_member("a.txt"), b"second")]
    return _tar(tmp / "overwrite.tar.gz", members), DEFAULT


def file_then_dir(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _tar(tmp / "file_then_dir.tar.gz", [(_member("a"), b"file"), (_member("a/b"), b"child")]), DEFAULT


def git_dir(tmp: Path) -> tuple[Path, ArchiveLimits]:
    entries = [("src/app.py", b"x"), (".git/config", b"[core]\n\tfsmonitor = touch /tmp/pwned\n")]
    return _zip(tmp / "git_dir.zip", entries), DEFAULT


def backslash(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _zip(tmp / "backslash.zip", [("a\\b.txt", b"x")]), DEFAULT


def not_an_archive(tmp: Path) -> tuple[Path, ArchiveLimits]:
    path = tmp / "notes.zip"
    path.write_text("just text, not an archive\n" * 40)
    return path, DEFAULT


def corrupt_gzip(tmp: Path) -> tuple[Path, ArchiveLimits]:
    path = _tar(tmp / "corrupt.tar.gz", [(_member("r.bin"), os.urandom(64 * 1024))])
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    return path, DEFAULT


def _patch_zip_headers(path: Path, local_offset: int, central_offset: int, value: bytes) -> None:
    data = bytearray(path.read_bytes())
    data[local_offset : local_offset + len(value)] = value
    central = data.index(b"PK\x01\x02")
    data[central + central_offset : central + central_offset + len(value)] = value
    path.write_bytes(bytes(data))


def encrypted_zip(tmp: Path) -> tuple[Path, ArchiveLimits]:
    path = _zip(tmp / "encrypted.zip", [("secret.txt", b"x" * 100)])
    _patch_zip_headers(path, 6, 8, struct.pack("<H", 0x1))  # general purpose flag: encrypted
    return path, DEFAULT


def size_lie(tmp: Path) -> tuple[Path, ArchiveLimits]:
    path = _zip(tmp / "size_lie.zip", [("a.txt", b"y" * 5000)])
    _patch_zip_headers(path, 22, 24, struct.pack("<I", 10))  # uncompressed size claims 10 bytes
    return path, DEFAULT


def git_dir_upper(tmp: Path) -> tuple[Path, ArchiveLimits]:
    entries = [(".GIT/config", b"[core]\n"), ("x/.Git/hooks/post-checkout", b"#!/bin/sh\n")]
    return _zip(tmp / "git_upper.zip", entries), DEFAULT


def _zip_method(tmp: Path, method: int) -> tuple[Path, ArchiveLimits]:
    path = tmp / f"method{method}.zip"
    with zipfile.ZipFile(path, "w", compression=method) as zf:
        zf.writestr("a.txt", b"a" * 1000)
    return path, DEFAULT


def lzma_zip(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _zip_method(tmp, zipfile.ZIP_LZMA)


def bzip2_zip(tmp: Path) -> tuple[Path, ArchiveLimits]:
    return _zip_method(tmp, zipfile.ZIP_BZIP2)


REJECT_CASES: dict[str, tuple[Builder, set[str]]] = {
    "traversal": (traversal, {"traversal"}),
    "absolute": (absolute, {"absolute"}),
    "symlink": (symlink, {"link"}),
    "zip_symlink": (zip_symlink, {"link"}),
    "hardlink": (hardlink, {"link"}),
    "device": (device, {"device"}),
    "fifo": (fifo, {"device"}),
    "zip_bomb": (zip_bomb, {"ratio"}),
    "gzip_bomb": (gzip_bomb, {"ratio"}),
    "pax_bomb": (pax_bomb, {"ratio"}),
    "too_many_files": (too_many_files, {"too_many_files"}),
    "oversize": (oversize, {"oversize"}),
    "total_oversize": (total_oversize, {"oversize"}),
    "overwrite": (overwrite, {"duplicate"}),
    "file_then_dir": (file_then_dir, {"duplicate"}),
    "git_dir": (git_dir, {"git_dir"}),
    "git_dir_upper": (git_dir_upper, {"git_dir"}),
    "backslash": (backslash, {"invalid_name"}),
    "not_an_archive": (not_an_archive, {"format"}),
    "corrupt_gzip": (corrupt_gzip, {"format"}),
    "encrypted_zip": (encrypted_zip, {"format"}),
    "lzma_zip": (lzma_zip, {"format"}),  # unbounded in-memory inflate in zipfile (security re-review)
    "bzip2_zip": (bzip2_zip, {"format"}),
    "size_lie": (size_lie, {"format", "oversize"}),
}


def leftovers(parent: Path) -> list[str]:
    return [p.name for p in parent.iterdir() if ".importing-" in p.name]


@pytest.mark.parametrize("case", list(REJECT_CASES))
def test_rejects(tmp_path: Path, case: str) -> None:
    builder, reasons = REJECT_CASES[case]
    archive, limits = builder(tmp_path)
    dest = tmp_path / "projects" / "p1"
    dest.parent.mkdir()
    started = time.monotonic()
    with pytest.raises(ArchiveRejected) as caught:
        import_archive(archive, dest, limits=limits)
    assert caught.value.reason in reasons
    assert caught.value.code == "ARCHIVE_REJECTED"
    assert caught.value.http_status == 400
    assert not dest.exists()
    assert leftovers(dest.parent) == []
    assert time.monotonic() - started < 15


def nested_ok(tmp: Path) -> Path:
    run = zipfile.ZipInfo("proj-main/bin/run.sh")
    run.external_attr = (stat.S_IFREG | stat.S_ISUID | 0o755) << 16
    entries: list[tuple[str | zipfile.ZipInfo, bytes]] = [
        ("proj-main/", b""),
        ("proj-main/README.md", b"# Proj\n"),
        (run, b"#!/bin/sh\necho hi\n"),
        ("proj-main/src/app.py", b"print('ok')\n"),
    ]
    return _zip(tmp / "nested_ok.zip", entries)


def test_accepts_valid_archive(tmp_path: Path) -> None:
    dest = tmp_path / "p1"
    progress: list[tuple[int, int]] = []
    report = import_archive(
        nested_ok(tmp_path), dest, strip_single_root=True, on_progress=lambda f, b: progress.append((f, b))
    )
    assert report == ImportReport(files=3, dirs=2, bytes=7 + 18 + 12, stripped_prefix="proj-main")
    assert (dest / "README.md").read_text() == "# Proj\n"
    assert (dest / "src" / "app.py").read_text() == "print('ok')\n"
    assert (dest / "bin" / "run.sh").stat().st_mode & 0o7777 == 0o755  # exec kept, setuid dropped
    assert (dest / "README.md").stat().st_mode & 0o7777 == 0o644
    assert (dest / "src").stat().st_mode & 0o7777 == 0o755
    assert dest.stat().st_mode & 0o7777 == 0o755
    assert progress[-1] == (3, 37)
    assert leftovers(tmp_path) == []


def test_accepts_tar_gz_with_dot_slash_names(tmp_path: Path) -> None:
    """``./`` prefixes are normalised; a lone ``src/`` folder stays where it is (no default stripping)."""
    dir_info = _member("./src", tarfile.DIRTYPE, mode=0o755)
    archive = _tar(
        tmp_path / "dots.tar.gz",
        [(_member("./", tarfile.DIRTYPE, mode=0o755), None), (dir_info, None), (_member("./src/a.py"), b"a")],
    )
    report = import_archive(archive, tmp_path / "p1")
    assert (tmp_path / "p1" / "src" / "a.py").read_text() == "a"
    assert report.files == 1


def test_empty_archive_is_an_empty_project(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "empty.zip", [])
    report = import_archive(archive, tmp_path / "p1")
    assert report == ImportReport(files=0, dirs=0, bytes=0, stripped_prefix=None)
    assert list((tmp_path / "p1").iterdir()) == []


def test_single_root_kept_by_default(tmp_path: Path) -> None:
    report = import_archive(nested_ok(tmp_path), tmp_path / "p1")
    assert report.stripped_prefix is None
    assert (tmp_path / "p1" / "proj-main" / "README.md").exists()


def test_no_strip_when_top_level_has_files(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "mixed.zip", [("proj/a.txt", b"a"), ("top.txt", b"t")])
    report = import_archive(archive, tmp_path / "p1", strip_single_root=True)
    assert report.stripped_prefix is None
    assert (tmp_path / "p1" / "proj" / "a.txt").exists()
    assert (tmp_path / "p1" / "top.txt").exists()


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_repeated_directory_entries_are_fine(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "dirs.zip", [("d/", b""), ("d/", b""), ("d/x", b"x")])
    assert import_archive(archive, tmp_path / "p1").files == 1


def test_dest_exists_conflict(tmp_path: Path) -> None:
    dest = tmp_path / "p1"
    dest.mkdir()
    (dest / "keep.txt").write_text("keep")
    with pytest.raises(AlreadyExists):
        import_archive(nested_ok(tmp_path), dest)
    assert (dest / "keep.txt").read_text() == "keep"


def test_gzip_member_is_read_as_tar_only(tmp_path: Path) -> None:
    """A gzip that is not a tar stream is a format error, not a crash."""
    path = tmp_path / "plain.gz"
    path.write_bytes(gzip.compress(b"hello, not a tar\n" * 100))
    with pytest.raises(ArchiveRejected) as caught:
        import_archive(path, tmp_path / "p1")
    assert caught.value.reason == "format"


def _corrupt_variants(tmp: Path) -> list[bytes]:
    good = _zip(tmp / "good.zip", [("a.txt", b"a" * 200), ("dir/b.txt", b"b" * 300)]).read_bytes()
    central = good.index(b"PK\x01\x02")
    end = good.index(b"PK\x05\x06")
    variants = [good[:n] for n in (10, 40, central, central + 10, end + 4)]
    for offset in (central + 42, central + 46, end + 16):  # local header offset, name, central dir offset
        flipped = bytearray(good)
        flipped[offset : offset + 4] = b"\xff\xff\xff\x7f"
        variants.append(bytes(flipped))
    bad_name = bytearray(good)
    name_at = central + 46
    bad_name[central + 8 : central + 10] = struct.pack("<H", 0x800)  # UTF-8 flag
    bad_name[name_at : name_at + 1] = b"\xff"
    variants.append(bytes(bad_name))
    return variants


def test_corrupt_zips_are_format_rejections(tmp_path: Path) -> None:
    """Damaged archives are a 400 ARCHIVE_REJECTED, never a raw exception (backend re-review)."""
    for index, data in enumerate(_corrupt_variants(tmp_path)):
        archive = tmp_path / f"corrupt{index}.zip"
        archive.write_bytes(data)
        dest = tmp_path / f"p{index}"
        with contextlib.suppress(ArchiveRejected):  # any other exception fails the test
            import_archive(archive, dest)
        assert leftovers(tmp_path) == []
