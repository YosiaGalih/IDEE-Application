"""Tests for the security-scan gate (spec WP-1.5d): Trivy and Semgrep JSON reports, exceptions EX-nn."""

import contextlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("scan_gate", ROOT / "infra/security/scan_gate.py")
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
sys.modules["scan_gate"] = gate  # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(gate)

TODAY = "2026-11-01"
DECISIONS = """
| ID | Temuan | Komponen | Siapa | Alasan | Mitigasi | Kedaluwarsa |
|---|---|---|---|---|---|---|
| EX-02 | unfixed HIGH | all | Lead | none | hardening | 2027-01-31 |
| EX-03 | upstream lag | caddy | Lead | none | not run | 2026-11-30 |
| EX-04 | gosu | postgres | Lead | not run | user 70:70 | 2027-01-31 |
"""
POLICY = """
[unfixed_high]
id = "EX-02"
until = 2027-01-31
"""


def vuln(cve, severity="HIGH", fixed=None, pkg="libfoo"):
    item = {"VulnerabilityID": cve, "PkgName": pkg, "InstalledVersion": "1.0", "Severity": severity}
    if fixed:
        item["FixedVersion"] = fixed
    return item


def report(name, vulns=(), os_=None, target=None, cls="os-pkgs"):
    return {
        "SchemaVersion": 2,
        "ArtifactName": name,
        "ArtifactType": "container_image",
        "Metadata": {"OS": os_ if os_ is not None else {"Family": "debian", "Name": "13.7"}},
        "Results": [{"Target": target or f"{name} (debian 13.7)", "Class": cls, "Vulnerabilities": list(vulns)}],
    }


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.reports = self.tmp / "reports"
        self.reports.mkdir()
        (self.tmp / "DECISIONS.md").write_text(DECISIONS)
        self.policy(POLICY)
        self.images = []

    def policy(self, text):
        (self.tmp / "exceptions.toml").write_text(text)

    def add(self, rep, listed=True):
        index = len(list(self.reports.iterdir()))
        (self.reports / f"{index}.json").write_text(json.dumps(rep))
        if listed:
            self.images.append(rep["ArtifactName"])

    def trivy(self, today=TODAY, extra_images=()):
        (self.tmp / "images.txt").write_text("\n".join([*self.images, *extra_images]) + "\n")
        return self.run_gate([
            "trivy", "--images", str(self.tmp / "images.txt"), "--reports", str(self.reports),
            "--exceptions", str(self.tmp / "exceptions.toml"), "--decisions", str(self.tmp / "DECISIONS.md"),
            "--today", today,
        ])

    def run_gate(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = gate.main(argv)
        return code, out.getvalue()


class TrivyGate(Case):
    def test_unfixed_high_is_listed_but_allowed_under_ex02(self):
        self.add(report("kodekantor/backend:dev", [vuln("CVE-1"), vuln("CVE-2")]))
        code, out = self.trivy()
        self.assertEqual(code, 0, out)
        self.assertIn("CVE-1", out)
        self.assertIn("EX-02", out)

    def test_unfixed_critical_blocks(self):
        self.add(report("kodekantor/backend:dev", [vuln("CVE-9", "CRITICAL")]))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("CVE-9", out)

    def test_high_with_a_fix_blocks(self):
        self.add(report("kodekantor/backend:dev", [vuln("CVE-3", fixed="1.1")]))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("CVE-3", out)

    def test_unfixed_high_blocks_once_ex02_expires(self):
        self.add(report("kodekantor/backend:dev", [vuln("CVE-1")]))
        code, out = self.trivy(today="2027-02-01")
        self.assertEqual(code, 2, out)
        self.assertIn("EX-02", out)

    def test_end_of_life_os_blocks(self):
        self.add(report("redis:7", os_={"Family": "alpine", "Name": "3.21.9", "EOSL": True}))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("end of life", out)

    def test_listed_image_without_a_report_blocks(self):
        self.add(report("kodekantor/backend:dev"))
        code, out = self.trivy(extra_images=["caddy:2"])
        self.assertEqual(code, 1, out)
        self.assertIn("caddy:2", out)

    def test_report_without_os_blocks(self):
        self.add(report("kodekantor/backend:dev", os_={}))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("operating system", out)

    def test_report_for_an_image_not_listed_blocks(self):
        self.add(report("kodekantor/backend:dev"))
        self.add(report("stray:1"), listed=False)
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("stray:1", out)

    def test_no_reports_at_all_blocks(self):
        code, out = self.trivy()
        self.assertEqual(code, 1, out)

    def test_unreadable_report_blocks(self):
        (self.reports / "broken.json").write_text("{not json")
        code, out = self.trivy()
        self.assertNotEqual(code, 0, out)

    def test_exception_by_cve_for_one_image(self):
        self.policy(POLICY + '[[exception]]\nid = "EX-03"\nuntil = 2026-11-30\nimage = "caddy:2"\nvulns = ["CVE-5"]\n')
        self.add(report("caddy:2@sha256:abc", [vuln("CVE-5", "CRITICAL", fixed="2")]))
        self.add(report("kodekantor/backend:dev", [vuln("CVE-5", "CRITICAL", fixed="2")]))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)  # the same CVE in another image is not covered
        self.assertIn("kodekantor/backend:dev", out)
        self.assertIn("EX-03", out)

    def test_exception_by_package_path(self):
        self.policy(POLICY + '[[exception]]\nid = "EX-04"\nuntil = 2027-01-31\nimage = "postgres:16"\n'
                    'pkg_path = "usr/local/bin/gosu"\n')
        self.add(report("postgres:16@sha256:abc", [vuln("CVE-7", "CRITICAL", fixed="1.25", pkg="stdlib")],
                        target="usr/local/bin/gosu", cls="lang-pkgs"))
        code, out = self.trivy()
        self.assertEqual(code, 0, out)
        self.assertIn("EX-04", out)

    def test_package_path_exception_that_matches_nothing_blocks(self):
        self.policy(POLICY + '[[exception]]\nid = "EX-04"\nuntil = 2027-01-31\nimage = "postgres:16"\n'
                    'pkg_path = "usr/local/bin/gosu"\n')
        self.add(report("postgres:16@sha256:abc"))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("usr/local/bin/gosu", out)

    def test_expired_exception_blocks(self):
        self.policy(POLICY + '[[exception]]\nid = "EX-03"\nuntil = 2026-11-30\nimage = "caddy:2"\nvulns = ["CVE-5"]\n')
        self.add(report("caddy:2", [vuln("CVE-5", fixed="2")]))
        code, out = self.trivy(today="2026-12-01")
        self.assertEqual(code, 2, out)
        self.assertIn("EX-03", out)

    def test_unused_exception_entry_blocks(self):
        self.policy(POLICY + '[[exception]]\nid = "EX-03"\nuntil = 2026-11-30\nimage = "caddy:2"\n'
                    'vulns = ["CVE-5", "CVE-6"]\n')
        self.add(report("caddy:2", [vuln("CVE-5", fixed="2")]))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("CVE-6", out)

    def test_exception_must_be_in_decisions_with_the_same_date(self):
        for exc_id, until in (("EX-09", "2026-11-30"), ("EX-03", "2026-11-29")):
            with self.subTest(exc_id=exc_id, until=until):
                self.policy(POLICY + f'[[exception]]\nid = "{exc_id}"\nuntil = {until}\nimage = "caddy:2"\n'
                            'vulns = ["CVE-5"]\n')
                self.add(report("caddy:2", [vuln("CVE-5", fixed="2")]))
                code, out = self.trivy()
                self.assertEqual(code, 2, out)
                self.assertIn(exc_id, out)
                for f in self.reports.iterdir():
                    f.unlink()
                self.images = []

    def test_exception_longer_than_92_days_is_refused(self):
        decisions = DECISIONS.replace("| 2026-11-30 |", "| 2027-03-01 |")
        (self.tmp / "DECISIONS.md").write_text(decisions)
        self.policy(POLICY + '[[exception]]\nid = "EX-03"\nuntil = 2027-03-01\nimage = "caddy:2"\nvulns = ["CVE-5"]\n')
        self.add(report("caddy:2", [vuln("CVE-5", fixed="2")]))
        code, out = self.trivy()
        self.assertEqual(code, 2, out)
        self.assertIn("92", out)

    def test_exception_needs_vulns_or_a_package_path(self):
        self.policy(POLICY + '[[exception]]\nid = "EX-03"\nuntil = 2026-11-30\nimage = "caddy:2"\n')
        self.add(report("caddy:2"))
        code, out = self.trivy()
        self.assertEqual(code, 2, out)

    def test_exception_for_an_image_not_scanned_is_refused(self):
        self.policy(POLICY + '[[exception]]\nid = "EX-03"\nuntil = 2026-11-30\nimage = "nginx:1"\nvulns = ["CVE-5"]\n')
        self.add(report("caddy:2"))
        code, out = self.trivy()
        self.assertEqual(code, 2, out)
        self.assertIn("nginx:1", out)


class SemgrepGate(Case):
    def semgrep(self, results=(), errors=(), scanned=("backend/app/main.py", "infra/compose.yml")):
        path = self.tmp / "semgrep.json"
        path.write_text(json.dumps({"results": list(results), "errors": list(errors),
                                    "paths": {"scanned": list(scanned)}}))
        return self.run_gate(["semgrep", "--report", str(path),
                              "--expect", "backend/app/main.py", "--expect", "infra/compose.yml"])

    def finding(self, severity, rule="python.lang.security.x"):
        return {"check_id": rule, "path": "backend/app/x.py", "start": {"line": 3},
                "extra": {"severity": severity, "message": "bad thing"}}

    def test_clean_scan_passes(self):
        code, out = self.semgrep()
        self.assertEqual(code, 0, out)

    def test_error_severity_blocks(self):
        code, out = self.semgrep([self.finding("ERROR")])
        self.assertEqual(code, 1, out)
        self.assertIn("backend/app/x.py:3", out)

    def test_warning_is_listed_but_allowed(self):
        code, out = self.semgrep([self.finding("WARNING")])
        self.assertEqual(code, 0, out)
        self.assertIn("python.lang.security.x", out)

    def test_error_level_scan_errors_block(self):
        code, out = self.semgrep(errors=[{"level": "error", "type": "Rule parse error", "message": "x"}])
        self.assertEqual(code, 1, out)
        code, out = self.semgrep(errors=[{"level": "warn", "type": "Syntax error", "message": "x"}])
        self.assertEqual(code, 0, out)

    def test_expected_files_must_have_been_scanned(self):
        code, out = self.semgrep(scanned=("backend/app/main.py",))
        self.assertEqual(code, 1, out)
        self.assertIn("infra/compose.yml", out)


class StackImages(unittest.TestCase):
    def test_service_images_and_sandbox_images(self):
        config = {"services": {
            "api": {"image": "kodekantor/backend:dev"},
            "worker": {"image": "kodekantor/backend:dev"},
            "sandbox-manager": {"image": "kodekantor/sandbox-manager:dev",
                                "environment": {"SANDBOX_IMAGE_NODE": "kodekantor/sandbox-node:1", "OTHER": "x"}},
            "build-only": {"build": {"context": "."}},
        }}
        self.assertEqual(gate.stack_images(config),
                         ["kodekantor/backend:dev", "kodekantor/sandbox-manager:dev", "kodekantor/sandbox-node:1"])

    def test_the_real_stack_lists_every_service_and_both_sandbox_images(self):
        config = subprocess.run(
            ["docker", "compose", "-f", "infra/compose.yml", "--env-file", "infra/tests/ci.env", "--profile", "egress",
             "config", "--format", "json"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
        images = gate.stack_images(json.loads(config))
        for expected in ("kodekantor/backend:dev", "kodekantor/egress-proxy:dev", "kodekantor/sandbox-python:1",
                         "kodekantor/sandbox-node:1"):
            self.assertIn(expected, images)
        self.assertTrue(any(name.startswith("caddy:") for name in images), images)
        self.assertEqual(len(images), 10, images)


class RepositoryPolicy(unittest.TestCase):
    """The real exceptions file, DECISIONS and compose stay consistent (runs in infra-config)."""

    def test_exceptions_file_matches_decisions(self):
        text = (ROOT / "docs/DECISIONS.md").read_text()
        policy = gate.load_policy(ROOT / "infra/security/scan-exceptions.toml", text, date.today())
        self.assertEqual(policy.unfixed_high.id, "EX-02")

    def test_gosu_exception_requires_postgres_to_run_as_70(self):
        data = tomllib.loads((ROOT / "infra/security/scan-exceptions.toml").read_text())
        if any(e.get("pkg_path") == "usr/local/bin/gosu" for e in data.get("exception", [])):
            compose = (ROOT / "infra/compose.yml").read_text()
            postgres = compose.split("\n  postgres:\n", 1)[1].split("\n  redis:\n", 1)[0]
            self.assertIn('user: "70:70"', postgres)

    def test_every_nosemgrep_names_a_recorded_exception(self):
        decisions = (ROOT / "docs/DECISIONS.md").read_text()
        files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        for name in filter(None, files.decode().split("\0")):
            path = ROOT / name
            if name.startswith("docs/") or name == "infra/tests/test_scan_gate.py" or not path.is_file():
                continue
            try:
                lines = path.read_text().splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, 1):
                if "nosemgrep" in line:
                    ids = re.findall(r"EX-\d{2}", line)
                    self.assertTrue(ids, f"{name}:{number}: nosemgrep without EX-nn")
                    for exc_id in ids:
                        row = next((r for r in decisions.splitlines() if r.startswith(f"| {exc_id} |")), "")
                        self.assertTrue(row, f"{name}:{number}: {exc_id} not in DECISIONS")
                        # The same expiry rule as the Trivy exceptions: in the future, at most 92 days ahead.
                        until = date.fromisoformat(re.findall(r"\d{4}-\d{2}-\d{2}", row.rsplit("|", 2)[-2])[0])
                        self.assertTrue(0 <= (until - date.today()).days <= gate.MAX_EXCEPTION_DAYS,
                                        f"{name}:{number}: {exc_id} expires {until}")


if __name__ == "__main__":
    unittest.main()
