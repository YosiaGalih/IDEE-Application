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
        # `docker network inspect` answers from KK_STUB_NET; everything else is `compose ps`.
        (bin_dir / "docker").write_text(
            '#!/bin/sh\nif [ "$1" = network ]; then printf "%s\\n" "$KK_STUB_NET"; else cat "$KK_STUB_PS"; fi\n'
        )
        (bin_dir / "curl").write_text("#!/bin/sh\nprintf 200\n")
        for stub in bin_dir.iterdir():
            stub.chmod(0o755)
        self.path = f"{bin_dir}:{os.environ['PATH']}"

    def run_check(
        self, ps_lines: list[str], egress: str, network: str = "true|kodekantor|true"
    ) -> subprocess.CompletedProcess[str]:
        (self.tmp / "ps.txt").write_text("\n".join(ps_lines) + "\n")
        (self.tmp / "stack.env").write_text(f"APP_DOMAIN=example.test\nSANDBOX_EGRESS={egress}\n")
        env = {
            **os.environ, "PATH": self.path,
            "KK_ENV_FILE": str(self.tmp / "stack.env"), "KK_STUB_PS": str(self.tmp / "ps.txt"),
            "KK_STUB_NET": network,
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

    def test_foreign_kk_egress_network_fails(self) -> None:
        """Review AG-13 WP-1.5b L2: compose adopts an existing kk-egress with only a warning."""
        proxy = [*HEALTHY, "egress-proxy|healthy|Up"]
        for network, reason in (
            ("false|kodekantor|true", "not internal"),
            ("true||true", "not created by this compose project"),
            ("true|kodekantor|", "lets the host keep an address"),
        ):
            with self.subTest(network=network):
                result = self.run_check(proxy, "allowlist", network)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(reason, result.stdout)

    def test_proxy_running_with_egress_off_warns(self) -> None:
        """Review AG-13 WP-1.5b L3: turning egress off does not stop a proxy that already runs."""
        result = self.run_check([*HEALTHY, "egress-proxy|healthy|Up"], "off")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("warning: egress-proxy runs while SANDBOX_EGRESS=off", result.stdout)


if __name__ == "__main__":
    unittest.main()
