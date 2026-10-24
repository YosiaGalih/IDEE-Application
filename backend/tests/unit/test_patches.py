"""Unified diff apply: all-or-nothing, no fuzz (FR-031, SECURITY_MODEL T9, TEST_STRATEGY app/workspace)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.workspace import fs
from app.workspace.errors import PatchConflict, PatchRejected, PathViolation
from app.workspace.fs import Workspace
from app.workspace.patches import HunkConflict, apply_hunks, parse_unified_diff
from app.workspace.policy import Access

A_PY = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text(A_PY)
    (root / "b.py").write_text("x = 1\ny = 2\n")
    (root / "old.txt").write_text("gone\n")
    return root


@pytest.fixture
def ws(root: Path) -> Workspace:
    return Workspace(root)


def contents(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def leftovers(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.name.startswith(fs.TEMP_PREFIX)]


THREE_FILES = """diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -4,3 +4,3 @@ def add(a, b):

 def sub(a, b):
-    return a - b
+    return a - b  # checked
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+print("new")
+print("file")
diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
"""


async def test_modify_add_delete(ws: Workspace, root: Path) -> None:
    changes = await ws.apply_patch(THREE_FILES, Access.AGENT_WRITE)
    assert [(c.path, c.change) for c in changes] == [
        ("a.py", "modified"),
        ("new.py", "added"),
        ("old.txt", "deleted"),
    ]
    assert (root / "a.py").read_text().endswith("    return a - b  # checked\n")
    assert (root / "new.py").read_text() == 'print("new")\nprint("file")\n'
    assert not (root / "old.txt").exists()
    assert leftovers(root) == []


def test_offset_applies() -> None:
    hunks = parse_unified_diff(THREE_FILES)[0].hunks
    shifted = "# one\n# two\n# three\n" + A_PY
    assert apply_hunks(shifted, hunks).endswith("    return a - b  # checked\n")


def test_ambiguous_context_conflicts() -> None:
    diff = "--- a/f\n+++ b/f\n@@ -10,2 +10,2 @@\n x\n-y\n+z\n"
    with pytest.raises(HunkConflict) as caught:
        apply_hunks("x\ny\nq\nx\ny\n", parse_unified_diff(diff)[0].hunks)
    assert (caught.value.hunk_index, caught.value.reason) == (0, "ambiguous")


def test_context_mismatch_conflicts() -> None:
    diff = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n x\n-y\n+z\n"
    with pytest.raises(HunkConflict) as caught:
        apply_hunks("x\nw\n", parse_unified_diff(diff)[0].hunks)
    assert caught.value.reason == "context_mismatch"


def test_blank_context_line_without_space() -> None:
    """LLM-written diffs often drop the leading space of an empty context line."""
    diff = "--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n\n-b\n+c\n"
    assert apply_hunks("a\n\nb\n", parse_unified_diff(diff)[0].hunks) == "a\n\nc\n"


def test_multiple_hunks_in_order() -> None:
    diff = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n-a\n+A\n b\n@@ -4,2 +4,2 @@\n d\n-e\n+E\n"
    assert apply_hunks("a\nb\nc\nd\ne\n", parse_unified_diff(diff)[0].hunks) == "A\nb\nc\nd\nE\n"


def test_overlapping_hunks_conflict() -> None:
    diff = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    with pytest.raises(HunkConflict) as caught:
        apply_hunks("a\nb\n", parse_unified_diff(diff)[0].hunks)
    assert caught.value.hunk_index == 1


async def test_conflict_changes_nothing(ws: Workspace, root: Path) -> None:
    diff = (
        "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-def add(a, b):\n+def add(a: int, b: int):\n"
        "     return a + b\n"
        "--- a/b.py\n+++ b/b.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 3\n+y = 4\n"
    )
    before = contents(root)
    with pytest.raises(PatchConflict) as caught:
        await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert caught.value.details == [{"path": "b.py", "hunk": 0, "reason": "context_mismatch"}]
    assert contents(root) == before


async def test_crlf_preserved(ws: Workspace, root: Path) -> None:
    (root / "win.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    diff = "--- a/win.txt\n+++ b/win.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
    await ws.apply_patch(diff, Access.USER_WRITE)
    assert (root / "win.txt").read_bytes() == b"one\r\nTWO\r\nthree\r\n"


def test_no_newline_at_eof_both_directions() -> None:
    add_newline = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n\\ No newline at end of file\n+x\n"
    assert apply_hunks("x", parse_unified_diff(add_newline)[0].hunks) == "x\n"
    drop_newline = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n\\ No newline at end of file\n"
    assert apply_hunks("x\n", parse_unified_diff(drop_newline)[0].hunks) == "y"


def test_hunk_not_at_eof_keeps_missing_newline() -> None:
    diff = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n-a\n+A\n b\n"
    assert apply_hunks("a\nb\nc", parse_unified_diff(diff)[0].hunks) == "A\nb\nc"


@pytest.mark.parametrize(
    ("diff", "reason"),
    [
        ("diff --git a/x b/y\nsimilarity index 90%\nrename from x\nrename to y\n", "rename"),
        ("--- a/x\n+++ b/y\n@@ -1 +1 @@\n-a\n+b\n", "rename"),
        ("diff --git a/x b/y\ncopy from x\ncopy to y\n", "copy"),
        ("diff --git a/x b/x\nold mode 100644\nnew mode 100755\n", "mode"),
        ("diff --git a/x b/x\nBinary files a/x and b/x differ\n", "binary"),
        ("diff --git a/x b/x\nGIT binary patch\nliteral 3\n", "binary"),
        ('--- "a/sp ace"\n+++ "b/sp ace"\n@@ -1 +1 @@\n-a\n+b\n', "quoted_path"),
        ("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-b\n+c\n", "duplicate_path"),
        ("", "empty"),
        ("just prose, no diff\n", "empty"),
        ("--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-a\n", "malformed"),
        ("--- a/x\n+++ b/x\n@@ bogus @@\n", "malformed"),
        ("--- a/x\n+++ b/x\n@@ -1 +1 @@\n?a\n", "malformed"),
        ("--- /dev/null\n+++ /dev/null\n@@ -0,0 +0,0 @@\n", "malformed"),
    ],
)
def test_rejects_unsupported(diff: str, reason: str) -> None:
    with pytest.raises(PatchRejected) as caught:
        parse_unified_diff(diff)
    assert caught.value.reason == reason


def test_rejects_oversize_and_too_many_files() -> None:
    with pytest.raises(PatchRejected) as caught:
        parse_unified_diff("x" * (1024 * 1024 + 1))
    assert caught.value.reason == "too_large"
    many = "".join(f"--- a/f{i}\n+++ b/f{i}\n@@ -1 +1 @@\n-a\n+b\n" for i in range(201))
    with pytest.raises(PatchRejected) as caught:
        parse_unified_diff(many)
    assert caught.value.reason == "too_many_files"


async def test_add_existing_conflicts(ws: Workspace) -> None:
    diff = "--- /dev/null\n+++ b/b.py\n@@ -0,0 +1 @@\n+z = 3\n"
    with pytest.raises(PatchConflict) as caught:
        await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert caught.value.details == [{"path": "b.py", "hunk": None, "reason": "exists"}]


async def test_modify_missing_conflicts(ws: Workspace) -> None:
    diff = "--- a/nope.py\n+++ b/nope.py\n@@ -1 +1 @@\n-a\n+b\n"
    with pytest.raises(PatchConflict) as caught:
        await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert caught.value.details == [{"path": "nope.py", "hunk": None, "reason": "missing"}]


async def test_delete_partial_conflicts(ws: Workspace, root: Path) -> None:
    diff = "--- a/b.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    with pytest.raises(PatchConflict) as caught:
        await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert caught.value.details == [{"path": "b.py", "hunk": None, "reason": "not_empty_after_delete"}]
    assert (root / "b.py").exists()


async def test_binary_target_conflicts(ws: Workspace, root: Path) -> None:
    (root / "img.bin").write_bytes(b"\x00\x01")
    diff = "--- a/img.bin\n+++ b/img.bin\n@@ -1 +1 @@\n-a\n+b\n"
    with pytest.raises(PatchConflict) as caught:
        await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert caught.value.details[0]["reason"] == "binary"


@pytest.mark.parametrize(
    ("path", "access"), [(".git/config", Access.USER_WRITE), (".env", Access.AGENT_WRITE)]
)
async def test_patch_respects_policy(ws: Workspace, path: str, access: Access) -> None:
    diff = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+x\n"
    with pytest.raises(PathViolation):
        await ws.apply_patch(diff, access)


async def test_patch_path_traversal_rejected(ws: Workspace) -> None:
    diff = "--- /dev/null\n+++ b/../escape.py\n@@ -0,0 +1 @@\n+x\n"
    with pytest.raises(PathViolation):
        await ws.apply_patch(diff, Access.AGENT_WRITE)


async def test_rollback_on_replace_failure(
    ws: Workspace, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = (
        "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-def add(a, b):\n+def add(a: int, b: int):\n"
        "     return a + b\n"
        "--- a/b.py\n+++ b/b.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3\n"
        "--- /dev/null\n+++ b/c.py\n@@ -0,0 +1 @@\n+c = 1\n"
    )
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src: str, dst: str, **kwargs: int) -> None:
        calls["n"] += 1
        if calls["n"] == 2 and dst == "b.py":
            raise OSError("disk full")
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", flaky_replace)
    with pytest.raises(OSError, match="disk full"):
        await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert (root / "a.py").read_text() == A_PY
    assert (root / "b.py").read_text() == "x = 1\ny = 2\n"
    assert not (root / "c.py").exists()
    assert leftovers(root) == []


async def test_empty_file_add_and_delete_from_bare_git_header(ws: Workspace, root: Path) -> None:
    add = "diff --git a/pkg/__init__.py b/pkg/__init__.py\nnew file mode 100644\nindex 0000000..e69de29\n"
    changes = await ws.apply_patch(add, Access.AGENT_WRITE)
    assert [(c.path, c.change, c.size) for c in changes] == [("pkg/__init__.py", "added", 0)]
    assert (root / "pkg" / "__init__.py").read_bytes() == b""
    delete = (
        "diff --git a/pkg/__init__.py b/pkg/__init__.py\ndeleted file mode 100644\nindex e69de29..0000000\n"
    )
    await ws.apply_patch(delete, Access.AGENT_WRITE)
    assert not (root / "pkg" / "__init__.py").exists()


def test_bare_git_header_without_change_is_malformed() -> None:
    with pytest.raises(PatchRejected) as caught:
        parse_unified_diff("diff --git a/x b/x\nindex 1111111..2222222 100644\n")
    assert caught.value.reason == "malformed"


async def test_add_into_new_folder(ws: Workspace, root: Path) -> None:
    diff = "--- /dev/null\n+++ b/deep/er/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert (root / "deep" / "er" / "new.py").read_text() == "x = 1\n"


# --- review regressions (WP-1.2a) ---------------------------------------------------------------------------


def test_body_longer_than_header_counts_is_rejected() -> None:
    """A miscounted hunk header must not silently drop the extra lines (backend review H1)."""
    diff = "--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-a\n+A\n-b\n+B\n"
    with pytest.raises(PatchRejected) as caught:
        parse_unified_diff(diff)
    assert caught.value.reason == "malformed"


def test_next_file_header_after_hunk_is_fine() -> None:
    diff = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+A\n--- a/g\n+++ b/g\n@@ -1 +1 @@\n-b\n+B\n"
    assert [p.path for p in parse_unified_diff(diff)] == ["f", "g"]


def first_line(path: str) -> str:
    return "def add(a, b):" if "a.py" in path else "x = 1"


@pytest.mark.parametrize(
    ("first", "second"), [("a.py", "./a.py"), ("a.py", "a.py/"), ("src//b.py", "src/b.py")]
)
async def test_alias_paths_are_one_file(ws: Workspace, root: Path, first: str, second: str) -> None:
    """Two spellings of one path would apply two patches to one snapshot (review H2)."""
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "b.py").write_text("x = 1\ny = 2\n")
    before = contents(root)
    diff = "".join(
        f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{first_line(path)}\n+{new}\n"
        for path, new in ((first, "one"), (second, "two"))
    )
    with pytest.raises(PatchRejected) as caught:
        await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert caught.value.reason == "duplicate_path"
    assert contents(root) == before


def test_final_newline_must_agree_with_the_file() -> None:
    """The diff's view of the final newline must match the file, as git apply requires (review M2)."""
    marker_but_file_has_newline = (
        "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n-a\n+A\n b\n\\ No newline at end of file\n"
    )
    with pytest.raises(HunkConflict):
        apply_hunks("a\nb\n", parse_unified_diff(marker_but_file_has_newline)[0].hunks)
    no_marker_but_file_lacks_newline = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n-a\n+A\n b\n"
    with pytest.raises(HunkConflict):
        apply_hunks("a\nb", parse_unified_diff(no_marker_but_file_lacks_newline)[0].hunks)
    consistent = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n-a\n+A\n b\n\\ No newline at end of file\n"
    assert apply_hunks("a\nb", parse_unified_diff(consistent)[0].hunks) == "A\nb"


def test_mixed_line_endings_untouched_lines_keep_theirs() -> None:
    """Only added lines take the dominant EOL (review M3)."""
    diff = "--- a/f\n+++ b/f\n@@ -4 +4 @@\n-d\n+D\n"
    assert apply_hunks("a\r\nb\r\nc\nd\ne\n", parse_unified_diff(diff)[0].hunks) == "a\r\nb\r\nc\nD\ne\n"
    context = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    assert apply_hunks("a\r\nb\r\nc\n", parse_unified_diff(context)[0].hunks) == "a\r\nB\r\nc\n"
    # a context line whose ending differs from the dominant one keeps it
    lf_context = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    assert apply_hunks("a\nb\r\nc\r\nd\r\n", parse_unified_diff(lf_context)[0].hunks) == "a\nB\r\nc\r\nd\r\n"


@pytest.mark.parametrize("mode", ["120000", "160000", "100664"])
def test_non_regular_modes_rejected(mode: str) -> None:
    diff = f"diff --git a/l b/l\nnew file mode {mode}\n--- /dev/null\n+++ b/l\n@@ -0,0 +1 @@\n+target\n"
    with pytest.raises(PatchRejected) as caught:
        parse_unified_diff(diff)
    assert caught.value.reason == "mode"


async def test_executable_mode_is_applied(ws: Workspace, root: Path) -> None:
    diff = (
        "diff --git a/run.sh b/run.sh\nnew file mode 100755\n"
        "--- /dev/null\n+++ b/run.sh\n@@ -0,0 +1 @@\n+echo hi\n"
    )
    await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert (root / "run.sh").stat().st_mode & 0o777 == 0o755


def test_lone_surrogate_diff_is_rejected() -> None:
    with pytest.raises(PatchRejected):
        parse_unified_diff("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+\ud800\n")


async def test_rollback_removes_added_files(
    ws: Workspace, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = (
        "--- /dev/null\n+++ b/added.py\n@@ -0,0 +1 @@\n+c = 1\n"
        "--- a/b.py\n+++ b/b.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3\n"
    )
    real_replace = os.replace

    def flaky_replace(src: str, dst: str, **kwargs: int) -> None:
        if dst == "b.py" and src.startswith(fs.TEMP_PREFIX):
            raise OSError("disk full")
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", flaky_replace)
    with pytest.raises(OSError, match="disk full"):
        await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert not (root / "added.py").exists()
    assert (root / "b.py").read_text() == "x = 1\ny = 2\n"
    assert leftovers(root) == []


async def test_failed_rollback_is_reported(
    ws: Workspace, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workspace.errors import PatchRollbackFailed

    diff = (
        "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-def add(a, b):\n+def add(a: int, b: int):\n"
        "     return a + b\n"
        "--- a/b.py\n+++ b/b.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3\n"
    )
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src: str, dst: str, **kwargs: int) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:  # the swap of b.py fails, and so does restoring a.py
            raise OSError("disk full")
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", flaky_replace)
    with pytest.raises(PatchRollbackFailed) as caught:
        await ws.apply_patch(diff, Access.AGENT_WRITE)
    assert caught.value.details == [{"path": "a.py"}]
    assert leftovers(root) == []


@pytest.mark.parametrize("separator", ["", "some prose between hunks"])
def test_hunk_after_blank_or_prose_line_is_rejected(separator: str) -> None:
    """A hunk separated from its file header must fail, not be skipped (backend re-review H1)."""
    diff = f"--- a/f\n+++ b/f\n@@ -1 +1 @@\n-1\n+ONE\n{separator}\n@@ -8 +8 @@\n-8\n+EIGHT\n"
    with pytest.raises(PatchRejected) as caught:
        parse_unified_diff(diff)
    assert caught.value.reason == "malformed"
