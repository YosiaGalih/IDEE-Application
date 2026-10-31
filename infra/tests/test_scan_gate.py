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
| EX-01 | old | uv | Lead | x | y | ~~2026-11-03~~ **Ditutup 2026-10-30** |
| EX-02 | unfixed HIGH | all | Lead; **menunggu persetujuan PO** | none | hardening | 2027-01-31 |
| EX-03 | upstream lag | caddy | Lead | none | not run | 2026-11-30 |
| EX-04 | gosu | postgres | Lead | not run | user 70:70 | 2027-01-31 |
"""
POLICY = """
[unfixed_high]
id = "EX-02"
until = 2027-01-31
"""
CADDY_EX03 = '[[exception]]\nid = "EX-03"\nuntil = 2026-11-30\nimage = "caddy:2"\nvulns = ["CVE-5"]\npkgs = ["libfoo"]\n'
GOSU_EX04 = '[[exception]]\nid = "EX-04"\nuntil = 2027-01-31\nimage = "postgres:16"\npkg_path = "usr/local/bin/gosu"\n'


def vuln(cve, severity="HIGH", fixed=None, pkg="libfoo"):
    item = {"VulnerabilityID": cve, "PkgName": pkg, "InstalledVersion": "1.0", "Severity": severity}
    if fixed:
        item["FixedVersion"] = fixed
    return item


def report(name, vulns=(), os_=None, extra_results=()):
    return {
        "SchemaVersion": 2,
        "ArtifactName": name,
        "ArtifactType": "container_image",
        "Metadata": {"OS": os_ if os_ is not None else {"Family": "debian", "Name": "13.7"}},
        "Results": [{"Target": f"{name} (debian 13.7)", "Class": "os-pkgs", "Vulnerabilities": list(vulns)},
                    *extra_results],
    }


def gosu_result(*vulns):
    return {"Target": "usr/local/bin/gosu", "Class": "lang-pkgs", "Vulnerabilities": list(vulns)}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.reports = self.tmp / "reports"
        self.reports.mkdir()
        (self.tmp / "DECISIONS.md").write_text(DECISIONS)
        self.db_info("2026-10-31T01:14:32Z")
        self.policy(POLICY)
        self.images = []

    def policy(self, text):
        (self.tmp / "exceptions.toml").write_text(text)

    def db_info(self, updated_at):
        (self.tmp / "db.json").write_text(json.dumps({"VulnerabilityDB": {"UpdatedAt": updated_at}}))

    def add(self, rep, listed=True):
        index = len(list(self.reports.iterdir()))
        (self.reports / f"report-{index}.json").write_text(json.dumps(rep))
        if listed:
            self.images.append(rep["ArtifactName"])

    def clear(self):
        for path in self.reports.iterdir():
            path.unlink()
        self.images = []

    def trivy(self, today=TODAY, extra_images=()):
        (self.tmp / "images.txt").write_text("\n".join([*self.images, *extra_images]) + "\n")
        return self.run_gate([
            "trivy", "--images", str(self.tmp / "images.txt"), "--reports", str(self.reports),
            "--db-info", str(self.tmp / "db.json"), "--exceptions", str(self.tmp / "exceptions.toml"),
            "--decisions", str(self.tmp / "DECISIONS.md"), "--today", today,
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
        self.assertIn("menunggu persetujuan PO", out)  # an unapproved exception says so in every run

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

    def test_unexpected_or_missing_severity_blocks(self):
        for severity in ("critical", "UNKNOWN", None):
            with self.subTest(severity=severity):
                self.add(report("kodekantor/backend:dev", [vuln("CVE-4", severity)]))
                code, out = self.trivy()
                self.assertEqual(code, 1, out)
                self.assertIn("unexpected severity", out)
                self.clear()

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

    def test_end_of_life_exception_for_one_image(self):
        self.policy(POLICY + '[[exception]]\nid = "EX-03"\nuntil = 2026-11-30\nimage = "redis:7"\neosl = true\n')
        self.add(report("redis:7@sha256:abc", os_={"Family": "alpine", "Name": "3.21.9", "EOSL": True}))
        code, out = self.trivy()
        self.assertEqual(code, 0, out)
        self.assertIn("excepted EX-03", out)
        self.add(report("valkey:8", os_={"Family": "alpine", "Name": "3.21.9", "EOSL": True}))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("valkey:8", out)

    def test_end_of_life_exception_not_needed_yet_is_only_noted(self):
        self.policy(POLICY + '[[exception]]\nid = "EX-03"\nuntil = 2026-11-30\nimage = "redis:7"\neosl = true\n')
        self.add(report("redis:7", os_={"Family": "alpine", "Name": "3.21.9"}))
        code, out = self.trivy()
        self.assertEqual(code, 0, out)
        self.assertIn("not needed today", out)

    def test_listed_image_without_a_report_blocks(self):
        self.add(report("kodekantor/backend:dev"))
        code, out = self.trivy(extra_images=["caddy:2"])
        self.assertEqual(code, 1, out)
        self.assertIn("caddy:2", out)

    def test_two_reports_for_one_image_block(self):
        self.add(report("kodekantor/backend:dev"))
        self.add(report("kodekantor/backend:dev", [vuln("CVE-9", "CRITICAL")]), listed=False)
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("more than one report", out)

    def test_report_without_os_blocks(self):
        self.add(report("kodekantor/backend:dev", os_={}))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("operating system", out)

    def test_report_without_an_os_packages_result_blocks(self):
        for results in ([], None, [gosu_result()]):
            with self.subTest(results=results):
                rep = report("kodekantor/backend:dev")
                rep["Results"] = results
                self.add(rep)
                code, out = self.trivy()
                self.assertEqual(code, 1, out)
                self.assertIn("no os-pkgs result", out)
                self.clear()

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
        (self.reports / "report-broken.json").write_text("{not json")
        self.add(report("kodekantor/backend:dev"))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("unreadable", out)

    def test_stale_or_unreadable_vulnerability_db_blocks(self):
        self.add(report("kodekantor/backend:dev"))
        self.db_info("2026-10-28T01:00:00Z")
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("older than 2 days", out)
        (self.tmp / "db.json").write_text("{}")
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("vulnerability DB date", out)

    def test_exception_by_cve_and_package_for_one_image(self):
        self.policy(POLICY + CADDY_EX03)
        self.add(report("caddy:2@sha256:abc", [vuln("CVE-5", "CRITICAL", fixed="2")]))
        self.add(report("kodekantor/backend:dev", [vuln("CVE-5", "CRITICAL", fixed="2")]))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)  # the same CVE in another image is not covered
        self.assertIn("BLOCKING: kodekantor/backend:dev", out)
        self.assertIn("excepted EX-03", out)

    def test_exception_does_not_cover_the_same_cve_in_another_package(self):
        self.policy(POLICY + CADDY_EX03)
        self.add(report("caddy:2", [vuln("CVE-5", fixed="2"), vuln("CVE-5", fixed="3", pkg="openssl")]))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("BLOCKING: caddy:2: HIGH CVE-5 openssl", out)

    def test_exception_image_must_match_exactly(self):
        for name in ("caddy:20", "caddy:2-debug"):
            with self.subTest(name=name):
                self.policy(POLICY + CADDY_EX03)
                self.add(report(name, [vuln("CVE-5", fixed="2")]))
                code, out = self.trivy()
                self.assertEqual(code, 2, out)  # "caddy:2" is not one of the scanned images
                self.assertIn("not one of the scanned images", out)
                self.clear()

    def test_exception_by_package_path(self):
        self.policy(POLICY + GOSU_EX04)
        self.add(report("postgres:16@sha256:abc", extra_results=[gosu_result(vuln("CVE-7", "CRITICAL", "1.25", "stdlib"))]))
        code, out = self.trivy()
        self.assertEqual(code, 0, out)
        self.assertIn("EX-04", out)

    def test_package_path_exception_that_matches_nothing_blocks(self):
        self.policy(POLICY + GOSU_EX04)
        self.add(report("postgres:16@sha256:abc"))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("usr/local/bin/gosu", out)

    def test_expired_exception_blocks(self):
        self.policy(POLICY + CADDY_EX03)
        self.add(report("caddy:2", [vuln("CVE-5", fixed="2")]))
        code, out = self.trivy(today="2026-12-01")
        self.assertEqual(code, 2, out)
        self.assertIn("EX-03", out)

    def test_unused_exception_entry_blocks(self):
        self.policy(POLICY + CADDY_EX03.replace('["CVE-5"]', '["CVE-5", "CVE-6"]'))
        self.add(report("caddy:2", [vuln("CVE-5", fixed="2")]))
        code, out = self.trivy()
        self.assertEqual(code, 1, out)
        self.assertIn("CVE-6", out)

    def test_exception_needs_an_open_decisions_row_with_the_same_date(self):
        for exc_id, until, message in (("EX-09", "2026-11-30", "no row"), ("EX-03", "2026-11-29", "expiry"),
                                       ("EX-01", "2026-11-03", "closed")):
            with self.subTest(exc_id=exc_id):
                self.policy(POLICY + CADDY_EX03.replace('"EX-03"', f'"{exc_id}"').replace("2026-11-30", until))
                self.add(report("caddy:2", [vuln("CVE-5", fixed="2")]))
                code, out = self.trivy()
                self.assertEqual(code, 2, out)
                self.assertIn(exc_id, out)
                self.assertIn(message, out)
                self.clear()

    def test_exception_longer_than_92_days_is_refused(self):
        (self.tmp / "DECISIONS.md").write_text(DECISIONS.replace("| 2026-11-30 |", "| 2027-03-01 |"))
        self.policy(POLICY + CADDY_EX03.replace("2026-11-30", "2027-03-01"))
        self.add(report("caddy:2", [vuln("CVE-5", fixed="2")]))
        code, out = self.trivy()
        self.assertEqual(code, 2, out)
        self.assertIn("92", out)

    def test_exception_needs_exactly_one_kind_of_match(self):
        bodies = ("", 'vulns = ["CVE-5"]\n', 'pkg_path = "x"\neosl = true\n', "eosl = false\n", 'vulns = []\npkgs = ["a"]\n')
        for body in bodies:
            with self.subTest(body=body):
                self.policy(POLICY + f'[[exception]]\nid = "EX-03"\nuntil = 2026-11-30\nimage = "caddy:2"\n{body}')
                self.add(report("caddy:2"))
                code, out = self.trivy()
                self.assertEqual(code, 2, out)
                self.clear()

    def test_exception_for_an_image_not_scanned_is_refused(self):
        self.policy(POLICY + CADDY_EX03.replace('"caddy:2"', '"nginx:1"'))
        self.add(report("caddy:2"))
        code, out = self.trivy()
        self.assertEqual(code, 2, out)
        self.assertIn("nginx:1", out)


class SemgrepGate(Case):
    def semgrep(self, results=(), errors=(), scanned=("backend/app/main.py", "infra/compose.yml"), min_files=2):
        path = self.tmp / "semgrep.json"
        path.write_text(json.dumps({"results": list(results), "errors": list(errors),
                                    "paths": {"scanned": list(scanned)}}))
        return self.run_gate(["semgrep", "--report", str(path), "--expect", "backend/app/main.py",
                              "--expect", "infra/compose.yml", "--min-files", str(min_files)])

    def finding(self, severity, rule="python.lang.security.x"):
        return {"check_id": rule, "path": "backend/app/x.py", "start": {"line": 3},
                "extra": {"severity": severity, "message": "bad thing"}}

    def test_clean_scan_passes(self):
        code, out = self.semgrep()
        self.assertEqual(code, 0, out)

    def test_findings_block_unless_warning_or_info(self):
        for severity in ("ERROR", "CRITICAL", "HIGH", "error", None):
            with self.subTest(severity=severity):
                code, out = self.semgrep([self.finding(severity)])
                self.assertEqual(code, 1, out)
                self.assertIn("backend/app/x.py:3", out)

    def test_warning_and_info_are_listed_but_allowed(self):
        code, out = self.semgrep([self.finding("WARNING"), self.finding("INFO", "python.other")])
        self.assertEqual(code, 0, out)
        self.assertIn("python.lang.security.x", out)
        self.assertIn("python.other", out)

    def test_every_scan_error_blocks(self):
        for error in ({"level": "error", "type": "Rule parse error"}, {"level": "warn", "type": "Timeout"},
                      {"level": "warn", "type": "Syntax error"}, {"type": "Rule parse error"}):
            with self.subTest(error=error):
                code, out = self.semgrep(errors=[error])
                self.assertEqual(code, 1, out)

    def test_expected_files_must_have_been_scanned(self):
        code, out = self.semgrep(scanned=("backend/app/main.py", "other.py"))
        self.assertEqual(code, 1, out)
        self.assertIn("infra/compose.yml", out)

    def test_too_few_files_scanned_blocks(self):
        code, out = self.semgrep(min_files=3)
        self.assertEqual(code, 1, out)
        self.assertIn("only 2 files", out)

    def test_expect_and_min_files_are_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gate.main(["semgrep", "--report", "x.json", "--min-files", "1"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gate.main(["semgrep", "--report", "x.json", "--expect", "a"])


class StackImages(unittest.TestCase):
    def test_service_images_and_sandbox_images(self):
        config = {"services": {
            "api": {"image": "kodekantor/backend:dev"},
            "worker": {"image": "kodekantor/backend:dev"},
            "sandbox-manager": {"image": "kodekantor/sandbox-manager:dev",
                                "environment": {"SANDBOX_IMAGE_NODE": "kodekantor/sandbox-node:1", "OTHER": "x"}},
        }}
        self.assertEqual(gate.stack_images(config),
                         ["kodekantor/backend:dev", "kodekantor/sandbox-manager:dev", "kodekantor/sandbox-node:1"])

    def test_a_service_without_an_image_name_is_refused(self):
        with self.assertRaises(ValueError):
            gate.stack_images({"services": {"build-only": {"build": {"context": "."}}}})

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
    """The real exceptions file, DECISIONS and compose stay consistent (infra-config and security-scan)."""

    decisions = (ROOT / "docs/DECISIONS.md").read_text()

    def test_exceptions_file_matches_decisions(self):
        policy = gate.load_policy(ROOT / "infra/security/scan-exceptions.toml", self.decisions, gate.today_utc())
        self.assertEqual(policy.unfixed_high.id, "EX-02")

    def test_gosu_exception_requires_postgres_to_run_as_70(self):
        data = tomllib.loads((ROOT / "infra/security/scan-exceptions.toml").read_text())
        if any(e.get("pkg_path") == "usr/local/bin/gosu" for e in data.get("exception", [])):
            compose = (ROOT / "infra/compose.yml").read_text()
            postgres = compose.split("\n  postgres:\n", 1)[1].split("\n  redis:\n", 1)[0]
            self.assertIn('user: "70:70"', postgres)

    def test_every_nosemgrep_names_its_rule_and_an_open_exception_for_that_file(self):
        files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        found = 0
        for name in filter(None, files.decode().split("\0")):
            path = ROOT / name
            if name.startswith("docs/") or name == "infra/tests/test_scan_gate.py" or not path.is_file():
                continue
            try:
                lines = path.read_text().splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, 1):
                if "nosemgrep" not in line:
                    continue
                found += 1
                where = f"{name}:{number}"
                self.assertRegex(line, r"nosemgrep: [\w.-]+\.[\w.-]+", f"{where}: name the full rule id")
                ids = re.findall(r"EX-\d{2}", line)
                self.assertTrue(ids, f"{where}: nosemgrep without EX-nn")
                for exc_id in ids:
                    until, _ = gate.decision_expiry(self.decisions, exc_id)  # open row, plain date
                    gate.check_window(exc_id, until, gate.today_utc())     # same rule as the Trivy exceptions
                    row = next(r for r in self.decisions.splitlines() if r.startswith(f"| {exc_id} |"))
                    self.assertIn(name, row, f"{where}: the {exc_id} row does not name this file")
        self.assertGreaterEqual(found, 1)


if __name__ == "__main__":
    unittest.main()
