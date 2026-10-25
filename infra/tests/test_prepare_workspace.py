"""scripts/prepare-workspace.sh refuses any path a typo could turn into a chown of a system folder.

Stdlib only. The refusals need no root; creating the folder (chown to 10001) runs only as root.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prepare-workspace.sh"


def run(value: str, env_dir: Path) -> subprocess.CompletedProcess[str]:
    env_file = env_dir / "workspace.env"
    env_file.write_text(f"APP_ENV=production\nWORKSPACE_HOST_PATH={value}\n")
    return subprocess.run(
        [str(SCRIPT)], env={**os.environ, "KK_ENV_FILE": str(env_file)}, capture_output=True, text=True, check=False
    )


class PrepareWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())  # where the workspace would go
        self.env_dir = Path(tempfile.mkdtemp())  # the env file, kept out of the folder checked for changes
        for folder in (self.tmp, self.env_dir):
            self.addCleanup(subprocess.run, ["rm", "-rf", str(folder)], check=False)

    def assert_refused(self, value: str, reason: str) -> None:
        before = {p: p.stat() for p in self.tmp.rglob("*")}
        result = run(value, self.env_dir)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertEqual({p: p.stat() for p in self.tmp.rglob("*")}, before, "nothing may change")

    def test_refuses_values_that_are_not_a_dedicated_absolute_path(self) -> None:
        # Relative values resolve against the repository root (the script cds there): if the script ever
        # accepted them, remove what it made.
        for stray in ("kk-test-relative", "$HOME", '"'):
            self.addCleanup(subprocess.run, ["rm", "-rf", "--", str(ROOT / stray)], check=False)
        t = self.tmp
        for value in ("", "/", "kk-test-relative/ws", f"{t}/ws/", f'"{t}/ws"', f"{t}/my ws", f"{t}/ws # c", "$HOME/ws"):
            with self.subTest(value=value):
                self.assert_refused(value, "WORKSPACE_HOST_PATH")

    def test_refuses_dot_parts(self) -> None:
        for value in (f"{self.tmp}/../etc", f"{self.tmp}/./ws", f"{self.tmp}/ws/.."):
            with self.subTest(value=value):
                self.assert_refused(value, "WORKSPACE_HOST_PATH")

    def test_refuses_an_existing_folder_owned_by_someone_else(self) -> None:
        folder = self.tmp / "taken"
        folder.mkdir(mode=0o755)
        if os.stat(folder).st_uid == 10001:
            self.skipTest("running as uid 10001")
        self.assert_refused(str(folder), "belongs to")
        self.assertEqual(folder.stat().st_mode & 0o777, 0o755)

    def test_refuses_a_file_or_a_symlink(self) -> None:
        (self.tmp / "file").write_text("x")
        (self.tmp / "link").symlink_to("/etc")
        for name in ("file", "link"):
            with self.subTest(name=name):
                self.assert_refused(str(self.tmp / name), "is not a folder")

    @unittest.skipUnless(os.geteuid() == 0, "chown to 10001 needs root")
    def test_creates_the_folder_for_uid_10001(self) -> None:
        target = self.tmp / "workspaces"
        result = run(str(target), self.env_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        info = target.stat()
        self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777), (10001, 10001, 0o750))
        again = run(str(target), self.env_dir)  # idempotent once it belongs to 10001
        self.assertEqual(again.returncode, 0, again.stderr)


if __name__ == "__main__":
    unittest.main()
