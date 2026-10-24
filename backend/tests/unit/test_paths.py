"""Safe paths and access policy (SECURITY_MODEL T2, ARCHITECTURE §7, FR-025, FR-028)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.workspace import paths
from app.workspace.errors import PathViolation, WorkspaceNotFound
from app.workspace.paths import open_parent, open_root, resolve, split_rel
from app.workspace.policy import Access, Policy

ALL = tuple(Access)
NO_POLICY = Policy()
SECRET_DENIED = (Access.USER_WRITE, Access.AGENT_READ, Access.AGENT_WRITE)


def parts(path: str) -> tuple[str, ...]:
    return tuple(path.split("/"))


def expect(path: str, denied: tuple[Access, ...]) -> list[tuple[str, Access, bool]]:
    return [(path, access, access in denied) for access in ALL]


POLICY_CASES = [
    *expect(".git/config", ALL),
    *expect("sub/.git/HEAD", ALL),
    *[
        case
        for secret in (
            ".env",
            "app/.env.local",
            "certs/server.pem",
            "x.key",
            "id_rsa",
            "id_ed25519.pub",
            "keystore.jks",
            ".npmrc",
            ".netrc",
            ".git-credentials",
            ".pypirc",
            "ID_RSA",
        )
        for case in expect(secret, SECRET_DENIED)
    ],
    *expect(".agentignore", (Access.AGENT_WRITE,)),
    *expect("node_modules/a/index.js", (Access.AGENT_READ,)),
    *expect("dist/app.js", (Access.AGENT_READ,)),
    *expect(".venv/lib/x.py", (Access.AGENT_READ,)),
    *expect("__pycache__/m.pyc", (Access.AGENT_READ,)),
    *expect("src/main.py", ()),
]


@pytest.mark.parametrize(("path", "access", "denied"), POLICY_CASES)
def test_policy_matrix(path: str, access: Access, denied: bool) -> None:
    assert Policy.from_agentignore(None).denied(parts(path), access) is denied


AGENTIGNORE = "secrets/\n*.sqlite\n/build-cache\ndocs/**/private.md\n!secrets/ok.txt\n# c\n"


@pytest.mark.parametrize(
    ("path", "matched"),
    [
        ("secrets/a.txt", True),
        ("secrets/ok.txt", True),  # negation is not supported: it cannot re-open a denied path
        ("data/db.sqlite", True),
        ("build-cache/x", True),
        ("docs/a/b/private.md", True),
        ("docs/private.md", True),
        ("x/build-cache/y", False),  # anchored pattern
        ("src/secrets.py", False),
    ],
)
def test_agentignore_patterns(path: str, matched: bool) -> None:
    policy = Policy.from_agentignore(AGENTIGNORE)
    assert policy.denied(parts(path), Access.AGENT_READ) is matched
    assert policy.denied(parts(path), Access.AGENT_WRITE) is matched
    assert policy.denied(parts(path), Access.USER_WRITE) is matched
    assert policy.denied(parts(path), Access.USER_READ) is False


def test_agentignore_ignores_blank_comment_and_negation_lines() -> None:
    policy = Policy.from_agentignore("\n   \n# *.py\n!*.md\n")
    assert not policy.denied(("a.py",), Access.AGENT_READ)
    assert not policy.denied(("a.md",), Access.AGENT_READ)


# --- resolution ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "reason"),
    [
        ("/etc/passwd", "absolute"),
        ("../x", "traversal"),
        ("a/../../x", "traversal"),
        ("a/..", "traversal"),
        ("a\x00b", "nul"),
        ("a\\b", "invalid"),
        ("", "invalid"),
        ("a" * 256, "too_long"),
        ("a/" * 2100, "too_long"),
    ],
)
def test_rejects_bad_strings(rel: str, reason: str) -> None:
    with pytest.raises(PathViolation) as caught:
        split_rel(rel)
    assert caught.value.reason == reason
    assert caught.value.details == [{"reason": reason}]


def test_normalises() -> None:
    assert split_rel("./src//app/") == ("src", "app")
    assert split_rel(".") == ()
    assert split_rel("src/") == split_rel("src")


def test_segment_limit_counts_bytes() -> None:
    assert split_rel("é" * 127)  # 254 bytes
    with pytest.raises(PathViolation):
        split_rel("é" * 128)  # 256 bytes


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A workspace root with the symlink fixtures of TEST_STRATEGY §4 next to an outside file."""
    (tmp_path / "outside.txt").write_text("outside")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "a.py").write_text("print('outside')")
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("print('a')")
    (root / ".env").write_text("TOKEN=synthetic")
    (root / "escape").mkdir()
    os.symlink("/", root / "out")
    os.symlink("../../outside.txt", root / "escape" / "f")
    os.symlink(".env", root / "notes")
    os.symlink("loop", root / "loop")
    os.symlink("src", root / "inner")
    return root


@pytest.fixture
def root_fd(root: Path) -> Iterator[tuple[int, str]]:
    fd, real = open_root(root)
    yield fd, real
    os.close(fd)


@pytest.mark.parametrize("rel", ["out/etc/passwd", "escape/f", "out"])
@pytest.mark.parametrize("access", ALL)
def test_symlink_out_of_root(root_fd: tuple[int, str], rel: str, access: Access) -> None:
    with pytest.raises(PathViolation) as caught:
        resolve(root_fd[1], rel, access, NO_POLICY)
    assert caught.value.reason == "outside_root"


def test_symlink_to_env_is_denied_for_the_agent(root_fd: tuple[int, str]) -> None:
    with pytest.raises(PathViolation) as caught:
        resolve(root_fd[1], "notes", Access.AGENT_READ, NO_POLICY)
    assert caught.value.reason == "denied"
    assert resolve(root_fd[1], "notes", Access.USER_READ, NO_POLICY) == (".env",)


def test_symlink_loop_is_rejected(root_fd: tuple[int, str]) -> None:
    fd, real = root_fd
    with pytest.raises(PathViolation):
        found = resolve(real, "loop/x", Access.USER_READ, NO_POLICY)
        os.close(open_parent(fd, found, create=False))


def test_in_root_symlink_reads_but_never_writes(root_fd: tuple[int, str]) -> None:
    assert resolve(root_fd[1], "inner/a.py", Access.USER_READ, NO_POLICY) == ("src", "a.py")
    for access in (Access.USER_WRITE, Access.AGENT_WRITE):
        with pytest.raises(PathViolation) as caught:
            resolve(root_fd[1], "inner/a.py", access, NO_POLICY)
        assert caught.value.reason == "symlink"


def test_denylist_applies_before_resolution(root_fd: tuple[int, str]) -> None:
    with pytest.raises(PathViolation) as caught:
        resolve(root_fd[1], ".git/config", Access.USER_READ, NO_POLICY)
    assert caught.value.reason == "denied"


def test_open_parent_walks_and_creates(root_fd: tuple[int, str], root: Path) -> None:
    fd, _ = root_fd
    os.close(open_parent(fd, ("src", "a.py"), create=False))
    with pytest.raises(WorkspaceNotFound):
        open_parent(fd, ("new", "deep", "f.txt"), create=False)
    os.close(open_parent(fd, ("new", "deep", "f.txt"), create=True))
    assert (root / "new" / "deep").is_dir()
    assert (root / "new" / "deep").stat().st_mode & 0o777 == 0o755


def test_open_parent_rejects_file_as_directory(root_fd: tuple[int, str]) -> None:
    with pytest.raises(PathViolation) as caught:
        open_parent(root_fd[0], ("src", "a.py", "x"), create=True)
    assert caught.value.reason == "not_regular"


def test_race_swap_detected(root_fd: tuple[int, str], root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A component swapped for a symlink after the realpath check is still refused when opened."""
    fd, real = root_fd
    monkeypatch.setattr(paths, "_realpath", os.path.normpath)  # the check sees no symlink
    (root / "src" / "a.py").unlink()
    (root / "src").rmdir()
    os.symlink(root.parent / "outside", root / "src")
    found = resolve(real, "src/a.py", Access.USER_READ, NO_POLICY)
    with pytest.raises(PathViolation) as caught:
        open_parent(fd, found, create=False)
    assert caught.value.reason == "symlink"


def test_missing_root(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceNotFound):
        open_root(tmp_path / "missing")


@pytest.mark.parametrize(
    ("pattern", "path", "matched"),
    [
        ("*.lo?", "a/x.log", True),
        ("*.lo?", "a/x.longer", False),
        ("[ab].txt", "b.txt", True),
        ("[ab].txt", "c.txt", False),
        ("[!ab].txt", "c.txt", True),
        ("[!ab].txt", "a.txt", False),
        ("logs/**", "logs/2026/x.txt", True),
        ("logs/**", "src/logs/x.txt", False),
        ("a/**/b", "a/b", True),
        ("a/**/b", "a/x/y/b", True),
        ("[unclosed", "[unclosed", True),
        ("/", "anything", False),
    ],
)
def test_agentignore_glob_syntax(pattern: str, path: str, matched: bool) -> None:
    assert Policy.from_agentignore(pattern).denied(parts(path), Access.AGENT_READ) is matched
