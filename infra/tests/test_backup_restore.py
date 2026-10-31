"""scripts/backup.sh and scripts/restore.sh without a stack (spec WP-1.5c, RUNBOOK §7).

Stdlib only. `docker` and `curl` are stubs on PATH that log every call; the live round trip is
infra/tests/restore_drill.sh. Workspace restores need root (extraction as uid 10001) and are skipped otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
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
  *"pg_restore --exit-on-error"*) cat > "$KK_STUB_RESTORED"; [ -z "$KK_STUB_RESTORE_FAIL" ] ;;
  *"string_agg(rolname"*) echo "${KK_STUB_ROLES:-kodekantor:true,kodekantor_app:false}" ;;
  *"FROM pg_roles"*) echo 1 ;;
  *"FROM pg_database"*) echo 1 ;;
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
        self.own_workspace()

    def own_workspace(self) -> None:
        """As in production (prepare-workspace.sh): the tree belongs to uid 10001. Only root can arrange that."""
        if os.geteuid() == 0:
            subprocess.run(["chown", "-R", "10001:10001", str(self.workspace)], check=True)

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
        self.assertIs(manifest["workspace_consistent"], True)

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
        stops = [i for i, c in enumerate(calls) if c.endswith("stop api worker scheduler sandbox-manager")]
        starts = [i for i, c in enumerate(calls) if c.endswith("start api worker scheduler sandbox-manager")]
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

    def test_files_changing_while_archived_are_kept_and_flagged(self) -> None:
        """Review AG-13 WP-1.5c M5: a live backup must not fail every night because a file changed."""
        tar = self.tmp / "bin" / "tar"
        tar.write_text('#!/bin/sh\n/usr/bin/tar "$@" || exit $?\ncase " $* " in *" --create "*) exit 1 ;; esac\n')
        tar.chmod(0o755)
        folder = self.backup()
        self.assertIs(json.loads((folder / "manifest.json").read_text())["workspace_consistent"], False)

    def test_a_tar_error_still_fails(self) -> None:
        tar = self.tmp / "bin" / "tar"
        tar.write_text('#!/bin/sh\ncase " $* " in *" --create "*) exit 2 ;; esac\nexec /usr/bin/tar "$@"\n')
        tar.chmod(0o755)
        self.assertNotEqual(self.run_script("backup.sh").returncode, 0)
        self.assertEqual(sorted(p.name for p in self.backups.iterdir()), [".lock"])

    @unittest.skipUnless(os.geteuid() == 0, "needs a folder owned by someone else")
    def test_a_backup_folder_owned_by_someone_else_is_refused(self) -> None:
        """Review AG-13 WP-1.5c L4: its owner could plant a .lock symlink that root then truncates."""
        self.backups.mkdir()
        os.chown(self.backups, 65534, 65534)
        result = self.run_script("backup.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("must be a folder owned by", result.stderr)

    def test_unsafe_settings_are_refused_before_any_work(self) -> None:
        for key, value in (("BACKUP_DIR", "relative/backups"), ("BACKUP_DIR", "/"), ("BACKUP_RETENTION_DAYS", "0"),
                           ("BACKUP_RETENTION_DAYS", "14d"), ("WORKSPACE_HOST_PATH", "/srv/../etc"),
                           ("KK_IMAGE_TAG", 'dev",\n"x')):
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
        # Only the read-only check of the dump ran before the question.
        self.assertEqual([c for c in self.docker_calls() if "pg_restore --list" not in c], [])

    @unittest.skipIf(os.geteuid() == 0, "checks the message a non-root operator gets")
    def test_workspace_restore_needs_root(self) -> None:
        result = self.run_script("restore.sh", "--yes", str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs root", result.stderr)
        self.assertEqual([c for c in self.docker_calls() if "pg_restore --list" not in c], [])

    def test_conflicting_modes_are_a_usage_error(self) -> None:
        result = self.run_script("restore.sh", "--db-only", "--workspace-only", str(self.folder))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.docker_calls(), [])

    def test_failed_load_keeps_the_previous_database_and_explains(self) -> None:
        """Review AG-13 WP-1.5c M2: a dump that cannot be loaded must not leave an empty database and silence."""
        self.env["KK_STUB_RESTORE_FAIL"] = "1"
        result = self.run_script("restore.sh", "--yes", "--db-only", str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("services are stopped", result.stderr)
        self.assertRegex(result.stderr, r"previous database is kept as kodekantor_pre_restore_\d{8}t\d{6}z")
        self.assertIn("make up", result.stderr)
        self.assertFalse(any("up -d --wait" in call for call in self.docker_calls()))

    def test_unreadable_dump_is_refused_before_anything_stops(self) -> None:
        self.env["KK_STUB_LIST"] = "215; 0 16400 TABLE DATA public tasks kodekantor"
        result = self.run_script("restore.sh", "--yes", "--db-only", str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no alembic_version data", result.stderr)
        self.assertFalse(any(" stop " in f" {call} " for call in self.docker_calls()))

    def test_a_dump_that_adds_roles_is_rejected(self) -> None:
        """Review AG-13 WP-1.5c M1: the dump runs as a superuser; a new role or superuser must not pass."""
        for roles in ("evil:true,kodekantor:true,kodekantor_app:false", "kodekantor:true,kodekantor_app:true"):
            with self.subTest(roles=roles):
                self.env["KK_STUB_ROLES"] = roles
                result = self.run_script("restore.sh", "--yes", "--db-only", str(self.folder))
                self.assertEqual(result.returncode, 1)
                self.assertIn("unexpected roles after pg_restore", result.stderr)

    def test_the_lock_is_taken_before_anything_else(self) -> None:
        import fcntl

        with (self.backups / ".lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.run_script("restore.sh", "--yes", "--db-only", str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("another backup or restore", result.stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_backup_folders_others_can_write_or_with_links_are_refused(self) -> None:
        """Review AG-13 WP-1.5c L2: a file could change between its check and its use."""
        self.folder.chmod(0o777)
        self.assertIn("not writable by others", self.run_script("restore.sh", "--yes", "--db-only", str(self.folder)).stderr)
        self.folder.chmod(0o700)
        dump = self.folder / "postgres.dump"
        real = self.tmp / "elsewhere.dump"
        dump.rename(real)
        dump.symlink_to(real)
        self.assertIn("not a regular file", self.run_script("restore.sh", "--yes", "--db-only", str(self.folder)).stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_database_restore_order(self) -> None:
        result = self.run_script("restore.sh", "--db-only", str(self.folder), stdin="RESTORE\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.docker_calls()

        def first(fragment: str) -> int:
            return next(i for i, call in enumerate(calls) if fragment in call)

        order = [first("pg_restore --list"), first("stop caddy web api worker scheduler sandbox-manager egress-proxy"),
                 first("FROM pg_roles"), first("ALTER DATABASE kodekantor RENAME TO kodekantor_pre_restore_"),
                 first("CREATE DATABASE kodekantor OWNER kodekantor"), first("pg_restore --exit-on-error"),
                 first("string_agg(rolname"), first("'ops.restore'"), first("up -d --wait")]
        self.assertEqual(order, sorted(order), calls)
        self.assertIn("GRANT CONNECT ON DATABASE kodekantor TO kodekantor_app", calls[order[4]])
        self.assertNotIn("DROP DATABASE", "\n".join(calls), "the previous database is kept, never dropped")
        self.assertIn("DROP DATABASE kodekantor_pre_restore_", result.stdout, "the operator is told what to delete")
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
    def test_restored_workspace_has_no_setuid_bits_and_a_closed_root(self) -> None:
        """Review AG-13 WP-1.5c M3: the archive must not loosen the root folder or bring setuid files back."""
        tool = self.workspace / "p1/repo/tool"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o4755)
        self.workspace.chmod(0o777)
        self.own_workspace()
        tool.chmod(0o4755)  # chown clears the setuid bit
        shutil.rmtree(self.folder)  # backup names are per second
        folder = self.backup()
        result = self.run_script("restore.sh", "--yes", "--workspace-only", str(folder))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.workspace.stat().st_mode & 0o7777, 0o750)
        self.assertEqual(tool.stat().st_mode & 0o7777, 0o755)

    @unittest.skipUnless(os.geteuid() == 0, "extraction as uid 10001 needs root")
    def test_names_with_newlines_restore(self) -> None:
        """Review AG-13 WP-1.5c M4: one odd file name must not make every restore of that backup fail."""
        (self.workspace / "p1/repo/two\nlines.txt").write_text("x")
        self.own_workspace()
        shutil.rmtree(self.folder)  # backup names are per second
        folder = self.backup()
        result = self.run_script("restore.sh", "--yes", "--workspace-only", str(folder))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "p1/repo/two\nlines.txt").read_text(), "x")

    @unittest.skipUnless(os.geteuid() == 0, "mounting needs root")
    def test_workspace_that_is_a_mount_point(self) -> None:
        """Review AG-13 WP-1.5c M6 / RUNBOOK §11: the workspaces may sit on their own volume; it cannot be renamed."""
        if subprocess.run(["mount", "-t", "tmpfs", "-o", "size=32m", "tmpfs", str(self.workspace)], check=False).returncode:
            self.skipTest("cannot mount tmpfs here")
        self.addCleanup(subprocess.run, ["umount", "-l", str(self.workspace)], check=False)
        self.seed_workspace()
        shutil.rmtree(self.folder)
        folder = self.backup()
        (self.workspace / "p1/repo/file with space.txt").write_text("damaged")
        result = self.run_script("restore.sh", "--yes", "--workspace-only", str(folder))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "p1/repo/file with space.txt").read_text(), "hello")
        kept = [p for p in self.workspace.iterdir() if p.name.startswith(".pre-restore-")]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].stat().st_mode & 0o777, 0o700)
        self.assertEqual((kept[0] / "p1/repo/file with space.txt").read_text(), "damaged")
        listing = (self.backup_again() / "workspaces.manifest").read_text()
        self.assertNotIn(".pre-restore-", listing, "the kept copy never lands in later backups")

    def backup_again(self) -> Path:
        for old in self.backups.glob("kodekantor-*"):
            shutil.rmtree(old)
        return self.backup()

    def test_not_enough_space_is_refused_before_anything_stops(self) -> None:
        manifest = self.folder / "workspaces.manifest"
        manifest.write_text("-rw-r--r-- 10001/10001 99999999999999999 2026-10-31 05:00 ./huge\n")
        sums = self.folder / "SHA256SUMS"
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        sums.write_text("".join(
            f"{digest}  workspaces.manifest\n" if line.endswith("workspaces.manifest") else line + "\n"
            for line in sums.read_text().splitlines()
        ))
        if os.geteuid() != 0:
            self.skipTest("the workspace part needs root")
        result = self.run_script("restore.sh", "--yes", "--workspace-only", str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not enough space", result.stderr)
        self.assertEqual(self.docker_calls(), [])

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
