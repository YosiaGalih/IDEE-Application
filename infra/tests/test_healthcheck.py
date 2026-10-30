"""scripts/healthcheck.sh decides from `docker compose ps` and the env file (spec WP-1.5b, RUNBOOK §2).

Stdlib only. `docker` and `curl` are replaced by stubs on PATH, so no stack is needed.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "healthcheck.sh"
HEALTHY = [
    "caddy|healthy|Up", "web|healthy|Up", "api|healthy|Up", "migrate||Exited (0) 1 minute ago",
    "worker|healthy|Up", "scheduler|healthy|Up", "postgres|healthy|Up", "redis|healthy|Up",
    "sandbox-manager|healthy|Up", "docker-socket-proxy|healthy|Up",
]


class Healthcheck(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text('#!/bin/sh\ncat "$KK_STUB_PS"\n')
        (bin_dir / "curl").write_text("#!/bin/sh\nprintf 200\n")
        for stub in bin_dir.iterdir():
            stub.chmod(0o755)
        self.path = f"{bin_dir}:{os.environ['PATH']}"

    def run_check(self, ps_lines: list[str], egress: str) -> subprocess.CompletedProcess[str]:
        (self.tmp / "ps.txt").write_text("\n".join(ps_lines) + "\n")
        (self.tmp / "stack.env").write_text(f"APP_DOMAIN=example.test\nSANDBOX_EGRESS={egress}\n")
        env = {
            **os.environ, "PATH": self.path,
            "KK_ENV_FILE": str(self.tmp / "stack.env"), "KK_STUB_PS": str(self.tmp / "ps.txt"),
        }
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, check=False)

    def test_healthy_stack_with_egress_off(self) -> None:
        result = self.run_check(HEALTHY, "off")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GET https://example.test/api/v1/health/ready -> 200", result.stdout)

    def test_allowlist_needs_the_proxy(self) -> None:
        result = self.run_check(HEALTHY, "allowlist")
        self.assertEqual(result.returncode, 1)
        self.assertIn("SANDBOX_EGRESS=allowlist but egress-proxy is not running", result.stdout)

    def test_allowlist_with_a_healthy_proxy(self) -> None:
        result = self.run_check([*HEALTHY, "egress-proxy|healthy|Up"], "allowlist")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unhealthy_proxy_fails(self) -> None:
        result = self.run_check([*HEALTHY, "egress-proxy|unhealthy|Up"], "allowlist")
        self.assertEqual(result.returncode, 1)
        self.assertIn("egress-proxy: unhealthy", result.stdout)

    def test_failed_migration_fails(self) -> None:
        lines = [line if not line.startswith("migrate|") else "migrate||Exited (1) 1 minute ago" for line in HEALTHY]
        result = self.run_check(lines, "off")
        self.assertEqual(result.returncode, 1)
        self.assertIn("migrate: Exited (1)", result.stdout)


if __name__ == "__main__":
    unittest.main()
