"""scripts/backup.sh and scripts/restore.sh without a stack (spec WP-1.5c, RUNBOOK §7).

Stdlib only. `docker` and `curl` are stubs on PATH that log every call; the live round trip is
infra/tests/restore_drill.sh. Workspace restores need root (extraction as uid 10001) and are skipped otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DUMP = b"PGDMP-fake-dump\x00\x01"
STUB_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$KK_STUB_LOG"
case "$*" in
  *"pg_dump"*) [ -n "$KK_STUB_DUMP_FAIL" ] && exit 1; cat "$KK_STUB_DUMP" ;;
  *"pg_restore --list"*) cat >/dev/null; printf '%s\\n' "$KK_STUB_LIST" ;;
  *"pg_restore --exit-on-error"*) cat > "$KK_STUB_RESTORED" ;;
  *"FROM pg_roles"*) echo 1 ;;
  *"string_agg(version_num"*) echo '0003_tasks|1|2|3|4|16.15' ;;
  *"ps -a --format"*) cat "$KK_STUB_PS" ;;
esac
"""
HEALTHY = "\n".join(
    f"{name}|healthy|Up"
    for name in ("caddy", "web", "api", "worker", "scheduler", "postgres", "redis", "sandbox-manager",
                 "docker-socket-proxy")
) + "\nmigrate||Exited (0) 1 minute ago\n"


class Scripts(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.tmp.chmod(0o755)  # like /srv/kodekantor: uid 10001 must reach the workspace folder
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(STUB_DOCKER)
        (bin_dir / "curl").write_text("#!/bin/sh\nprintf 200\n")
        for stub in bin_dir.iterdir():
            stub.chmod(0o755)
        (self.tmp / "dump.bin").write_bytes(DUMP)
        (self.tmp / "ps.txt").write_text(HEALTHY)
        self.backups = self.tmp / "backups"
        self.workspace = self.tmp / "workspaces"
        self.log = self.tmp / "docker.log"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "KK_ENV_FILE": str(self.tmp / "stack.env"),
            "KK_STUB_LOG": str(self.log),
            "KK_STUB_DUMP": str(self.tmp / "dump.bin"),
            "KK_STUB_LIST": "215; 0 16400 TABLE DATA public alembic_version kodekantor",
            "KK_STUB_RESTORED": str(self.tmp / "restored.bin"),
            "KK_STUB_PS": str(self.tmp / "ps.txt"),
        }
        self.write_env()
        self.seed_workspace()

    def write_env(self, **overrides: str) -> None:
        values = {
            "APP_DOMAIN": "example.test",
            "BACKUP_DIR": str(self.backups),
            "BACKUP_RETENTION_DAYS": "14",
            "WORKSPACE_HOST_PATH": str(self.workspace),
            **overrides,
        }
        (self.tmp / "stack.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    def seed_workspace(self) -> None:
        files = {
            "p1/repo/file with space.txt": "hello",
            "p1/repo/uploads/user-file.txt": "kept",
            "p1/repo/node_modules/x/i.js": "skipped",
            "p1/repo/pkg/__pycache__/m.pyc": "skipped",
            "p1/uploads/import.zip": "skipped",
            "p1/.kk-runs/r1/out": "skipped",
            "p1/artifacts/t1/report.json": "{}",
        }
        for name, text in files.items():
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def run_script(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ROOT / "scripts" / args[0]), *args[1:]],
            env=self.env, input=stdin, capture_output=True, text=True, check=False,
        )

    def docker_calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def backup(self) -> Path:
        before = set(self.backups.iterdir()) if self.backups.exists() else set()
        result = self.run_script("backup.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        made = [p for p in set(self.backups.iterdir()) - before if re.fullmatch(r"kodekantor-\d{8}T\d{6}Z", p.name)]
        self.assertEqual(len(made), 1, made)
        return made[0]


class Backup(Scripts):
    def test_backup_folder_files_modes_and_checksums(self) -> None:
        folder = self.backup()
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(names, ["SHA256SUMS", "manifest.json", "postgres.dump", "workspaces.manifest", "workspaces.tar.gz"])
        for path in folder.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600, path.name)
        self.assertEqual((folder / "postgres.dump").read_bytes(), DUMP)
        for line in (folder / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), digest, name)
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["counts"], {"users": 1, "projects": 2, "tasks": 3, "audit_events": 4})
        self.assertEqual(manifest["alembic_revision"], "0003_tasks")
        self.assertIs(manifest["quiesced"], False)

    def test_workspace_archive_skips_only_rebuildable_and_temporary_files(self) -> None:
        listing = (self.backup() / "workspaces.manifest").read_text()
        for kept in ("p1/repo/file with space.txt", "p1/repo/uploads/user-file.txt", "p1/artifacts/t1/report.json"):
            self.assertIn(kept, listing)
        for skipped in ("node_modules", "__pycache__", "p1/uploads", ".kk-runs"):
            self.assertNotIn(skipped, listing)

    def test_failed_dump_keeps_nothing(self) -> None:
        self.env["KK_STUB_DUMP_FAIL"] = "1"
        result = self.run_script("backup.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sorted(p.name for p in self.backups.iterdir()), [".lock"])

    def test_dump_without_migration_data_is_rejected(self) -> None:
        self.env["KK_STUB_LIST"] = "215; 0 16400 TABLE DATA public tasks kodekantor"
        result = self.run_script("backup.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("alembic_version", result.stderr)
        self.assertEqual(sorted(p.name for p in self.backups.iterdir()), [".lock"])

    def test_quiesce_restarts_the_services_even_when_the_backup_fails(self) -> None:
        self.env["KK_STUB_DUMP_FAIL"] = "1"
        self.assertNotEqual(self.run_script("backup.sh", "--quiesce").returncode, 0)
        calls = self.docker_calls()
        stops = [i for i, c in enumerate(calls) if c.endswith("stop api worker scheduler")]
        starts = [i for i, c in enumerate(calls) if c.endswith("start api worker scheduler")]
        self.assertTrue(stops and starts and starts[-1] > stops[0], calls)

    def test_retention_removes_only_old_backups_named_exactly(self) -> None:
        self.backups.mkdir()
        for name in ("kodekantor-20000101T000000Z", "kodekantor-20000101T000000Z.partial",
                     "kodekantor-20000101T000000Z-copy", "notes", "kodekantor-29990101T000000Z"):
            (self.backups / name).mkdir()
        folder = self.backup()
        left = sorted(p.name for p in self.backups.iterdir())
        self.assertEqual(left, sorted([".lock", folder.name, "kodekantor-20000101T000000Z-copy", "notes",
                                       "kodekantor-29990101T000000Z"]))

    def test_unsafe_settings_are_refused_before_any_work(self) -> None:
        for key, value in (("BACKUP_DIR", "relative/backups"), ("BACKUP_DIR", "/"), ("BACKUP_RETENTION_DAYS", "0"),
                           ("BACKUP_RETENTION_DAYS", "14d"), ("WORKSPACE_HOST_PATH", "/srv/../etc")):
            with self.subTest(key=key, value=value):
                self.write_env(**{key: value})
                result = self.run_script("backup.sh")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(self.docker_calls(), [])


class Restore(Scripts):
    def setUp(self) -> None:
        super().setUp()
        self.folder = self.backup()
        self.log.unlink()

    def test_altered_backup_is_refused_before_any_docker_call(self) -> None:
        with (self.folder / "workspaces.tar.gz").open("r+b") as archive:
            archive.seek(20)
            archive.write(b"X")
        result = self.run_script("restore.sh", "--yes", str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("checksums do not match", result.stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_incomplete_checksum_list_is_refused(self) -> None:
        sums = self.folder / "SHA256SUMS"
        lines = [line for line in sums.read_text().splitlines() if "workspaces.manifest" not in line]
        sums.write_text("\n".join(lines) + "\n")
        result = self.run_script("restore.sh", "--yes", str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not cover workspaces.manifest", result.stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_folder_name_must_be_a_backup(self) -> None:
        other = self.folder.with_name("not-a-backup")
        self.folder.rename(other)
        self.assertEqual(self.run_script("restore.sh", "--yes", str(other)).returncode, 1)
        self.assertEqual(self.docker_calls(), [])

    def test_confirmation_is_required(self) -> None:
        result = self.run_script("restore.sh", "--db-only", str(self.folder), stdin="yes\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not confirmed", result.stderr)
        self.assertEqual(self.docker_calls(), [])

    @unittest.skipIf(os.geteuid() == 0, "checks the message a non-root operator gets")
    def test_workspace_restore_needs_root(self) -> None:
        result = self.run_script("restore.sh", "--yes", str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs root", result.stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_conflicting_modes_are_a_usage_error(self) -> None:
        result = self.run_script("restore.sh", "--db-only", "--workspace-only", str(self.folder))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.docker_calls(), [])

    def test_database_restore_order(self) -> None:
        result = self.run_script("restore.sh", "--db-only", str(self.folder), stdin="RESTORE\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.docker_calls()

        def first(fragment: str) -> int:
            return next(i for i, call in enumerate(calls) if fragment in call)

        order = [first("stop caddy web api worker scheduler sandbox-manager egress-proxy"), first("FROM pg_roles"),
                 first("DROP DATABASE IF EXISTS kodekantor WITH (FORCE)"), first("pg_restore --exit-on-error"),
                 first("'ops.restore'"), first("up -d --wait")]
        self.assertEqual(order, sorted(order), calls)
        self.assertIn("GRANT CONNECT ON DATABASE kodekantor TO kodekantor_app", calls[order[2]])
        self.assertEqual((self.tmp / "restored.bin").read_bytes(), DUMP)
        self.assertTrue((self.workspace / "p1/repo/file with space.txt").exists(), "db-only leaves workspaces alone")

    @unittest.skipUnless(os.geteuid() == 0, "extraction as uid 10001 needs root")
    def test_unreachable_workspace_is_refused_before_anything_stops(self) -> None:
        self.tmp.chmod(0o700)
        result = self.run_script("restore.sh", "--yes", "--workspace-only", str(self.folder))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("uid 10001 cannot reach", result.stderr)
        self.assertEqual(self.docker_calls(), [])
        self.assertTrue((self.workspace / "p1/repo/file with space.txt").exists())

    @unittest.skipUnless(os.geteuid() == 0, "extraction as uid 10001 needs root")
    def test_workspace_restore_keeps_the_old_folder_and_extracts_as_10001(self) -> None:
        (self.workspace / "p1/repo/file with space.txt").write_text("damaged")
        result = self.run_script("restore.sh", "--yes", "--workspace-only", str(self.folder))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "p1/repo/file with space.txt").read_text(), "hello")
        self.assertFalse((self.workspace / "p1/repo/node_modules").exists())
        owners = {(p.stat().st_uid, p.stat().st_gid) for p in [self.workspace, *self.workspace.rglob("*")]}
        self.assertEqual(owners, {(10001, 10001)})
        previous = [p for p in self.tmp.iterdir() if p.name.startswith("workspaces.pre-restore-")]
        self.assertEqual(len(previous), 1)
        self.assertEqual((previous[0] / "p1/repo/file with space.txt").read_text(), "damaged")


if __name__ == "__main__":
    unittest.main()
