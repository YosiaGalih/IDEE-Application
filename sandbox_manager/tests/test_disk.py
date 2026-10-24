"""Measuring and removing sandbox-written trees (security review F1, silent-failure F2/F12)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sandbox_manager.disk import ENTRY_COST, Unmeasurable, remove_tree, tree_bytes

AS_ROOT = os.geteuid() == 0


def test_counts_files_folders_and_empty_files(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x" * 10_000)
    (tmp_path / "sub").mkdir()
    for i in range(100):
        (tmp_path / "sub" / f"empty{i}").touch()
    total = tree_bytes(tmp_path)
    assert total >= 10_000 + 100 * ENTRY_COST  # millions of empty files are not free


def test_links_are_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big").write_bytes(b"x" * 1_000_000)
    tree = tmp_path / "tree"
    tree.mkdir()
    os.symlink(outside, tree / "link")
    assert tree_bytes(tree) < 100_000
    assert remove_tree(tree)
    assert (outside / "big").exists()  # removing the tree removed the link, not its target


def make_deep(root: Path, depth: int) -> None:
    """A ``depth``-deep chain of folders, made without recursion (pathlib.mkdir(parents=True) recurses)."""
    root.mkdir()
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    for _ in range(depth):
        os.mkdir("d", dir_fd=fd)
        child = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
        os.close(fd)
        fd = child
    os.close(os.open("f", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd))
    os.close(fd)


def test_deep_tree_is_measured_and_removed(tmp_path: Path) -> None:
    root = tmp_path / "deep"
    make_deep(root, 1500)  # shutil.rmtree recurses and fails near 1000 on 3.12
    assert tree_bytes(root) >= 1500 * ENTRY_COST
    assert remove_tree(root)
    assert not root.exists()


@pytest.mark.skipif(AS_ROOT, reason="root reads any folder; covered as uid 10001 in test_isolation_docker")
def test_locked_folder_is_unmeasurable(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "big").write_bytes(b"x" * 1_000_000)
    locked.chmod(0)
    try:
        with pytest.raises(Unmeasurable):
            tree_bytes(tmp_path)
    finally:
        locked.chmod(0o700)


def test_locked_folders_are_removed(tmp_path: Path) -> None:
    root = tmp_path / "run"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "f").write_text("x")
    (root / "a" / "b").chmod(0)
    (root / "a").chmod(0o500)
    assert remove_tree(root)
    assert not root.exists()


def test_missing_path_is_fine(tmp_path: Path) -> None:
    assert remove_tree(tmp_path / "nothing")


def test_tree_deeper_than_path_max_is_removed(tmp_path: Path) -> None:
    """Security review N1: a sandbox can nest folders past PATH_MAX by chdir."""
    root = tmp_path / "run"
    make_deep(root, 2100)  # 2100 x "/d" is past 4096 bytes
    with pytest.raises(Unmeasurable):  # measurement fails closed
        tree_bytes(root)
    assert remove_tree(root)
    assert not root.exists()


def test_name_clashes_do_not_stop_removal(tmp_path: Path) -> None:
    root = tmp_path / "run"
    (root / "a" / ".kk-rm-1").mkdir(parents=True)  # names a naive lifting scheme would reuse
    (root / ".kk-rm-1").mkdir()
    (root / ".kk-rm-1" / "f").write_text("x")
    (root / "a" / "f").write_text("x")
    assert remove_tree(root)
    assert not root.exists()
