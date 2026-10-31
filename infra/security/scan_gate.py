#!/usr/bin/env python3
"""security-scan gate (spec WP-1.5d, SECURITY_MODEL §11). Reads the JSON that Trivy and Semgrep wrote and decides.

  docker compose ... config --format json | scan_gate.py images     (every image the stack runs, one per line)
  scan_gate.py trivy --images FILE --reports DIR [--exceptions TOML] [--decisions MD] [--today YYYY-MM-DD]
  scan_gate.py semgrep --report FILE --expect PATH [--expect PATH ...]

Trivy, per finding: CRITICAL blocks; HIGH with a fixed version blocks; HIGH without one is listed and allowed while
the [unfixed_high] exception (EX-02) lasts. An end-of-life OS, a listed image without a report, a report without an
OS, and a report for an image nobody listed block too. [[exception]] entries cover findings by CVE or by package
path for one image; each needs a row in DECISIONS with the same date, at most 92 days ahead, and must still match a
finding. Semgrep: ERROR findings and error-level scan errors block; every --expect path must have been scanned.

Exit 0 pass, 1 blocking findings, 2 the exceptions file or DECISIONS is wrong (an expired exception included).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

MAX_EXCEPTION_DAYS = 92


class ConfigError(Exception):
    """The exceptions file or DECISIONS does not allow this run (exit 2)."""


@dataclass
class ExceptionEntry:
    id: str
    until: date
    image: str
    vulns: set[str] = field(default_factory=set)
    pkg_path: str | None = None
    used: set[str] = field(default_factory=set)

    def covers(self, image: str, vuln_id: str, path: str) -> bool:
        if not image.startswith(self.image):
            return False
        if self.pkg_path is not None:
            return path == self.pkg_path
        return vuln_id in self.vulns


@dataclass
class Policy:
    unfixed_high: ExceptionEntry
    exceptions: list[ExceptionEntry]


def _checked(entry: dict, decisions: str, today: date, where: str) -> tuple[str, date]:
    exc_id, until = entry.get("id"), entry.get("until")
    if not isinstance(exc_id, str) or not re.fullmatch(r"EX-\d{2}", exc_id):
        raise ConfigError(f"{where}: id must look like EX-nn (got {exc_id!r})")
    if not isinstance(until, date) or isinstance(until, datetime):
        raise ConfigError(f"{where} {exc_id}: until must be a date (got {until!r})")
    if until < today:
        raise ConfigError(f"{exc_id} expired on {until}: fix the findings, or renew it in DECISIONS and here")
    if (until - today).days > MAX_EXCEPTION_DAYS:
        raise ConfigError(f"{exc_id}: until {until} is more than {MAX_EXCEPTION_DAYS} days ahead")
    row = next((line for line in decisions.splitlines() if line.startswith(f"| {exc_id} |")), None)
    if row is None:
        raise ConfigError(f"{exc_id} has no row in DECISIONS (Pengecualian keamanan)")
    if until.isoformat() not in row.rsplit("|", 2)[-2]:
        raise ConfigError(f"{exc_id}: DECISIONS gives another expiry date than {until}")
    return exc_id, until


def load_policy(path: Path, decisions: str, today: date, images: list[str] | None = None) -> Policy:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error
    unfixed = data.get("unfixed_high")
    if not isinstance(unfixed, dict):
        raise ConfigError(f"{path}: [unfixed_high] is missing")
    exc_id, until = _checked(unfixed, decisions, today, "[unfixed_high]")
    policy = Policy(ExceptionEntry(exc_id, until, ""), [])
    for number, entry in enumerate(data.get("exception", []), 1):
        exc_id, until = _checked(entry, decisions, today, f"[[exception]] #{number}")
        image, vulns, pkg_path = entry.get("image"), entry.get("vulns"), entry.get("pkg_path")
        if not isinstance(image, str) or not image:
            raise ConfigError(f"{exc_id}: image is required")
        if (vulns is None) == (pkg_path is None):
            raise ConfigError(f"{exc_id} ({image}): give either vulns or pkg_path")
        if images is not None and not any(name.startswith(image) for name in images):
            raise ConfigError(f"{exc_id}: {image} is not one of the scanned images")
        policy.exceptions.append(ExceptionEntry(exc_id, until, image, set(vulns or ()), pkg_path))
    return policy


def _read_reports(directory: Path) -> tuple[dict[str, dict], list[str]]:
    reports: dict[str, dict] = {}
    problems: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            report = json.loads(path.read_text())
            name = report["ArtifactName"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            problems.append(f"{path.name}: unreadable Trivy report ({error})")
            continue
        if report.get("SchemaVersion") != 2 or report.get("ArtifactType") != "container_image":
            problems.append(f"{name}: not a Trivy v2 container image report")
            continue
        reports[name] = report
    return reports, problems


def evaluate_trivy(images: list[str], reports: dict[str, dict], policy: Policy) -> tuple[list[str], list[str]]:
    blocking: list[str] = []
    notes: list[str] = []
    for name in sorted(set(reports) - set(images)):
        blocking.append(f"{name}: report for an image that is not in the scan list")
    for name in images:
        report = reports.get(name)
        if report is None:
            blocking.append(f"{name}: no Trivy report")
            continue
        os_ = (report.get("Metadata") or {}).get("OS") or {}
        if not os_.get("Family"):
            blocking.append(f"{name}: Trivy found no operating system, so OS packages were not checked")
        elif os_.get("EOSL"):
            blocking.append(f"{name}: {os_['Family']} {os_.get('Name')} is end of life (no more security fixes)")
        allowed = 0
        for result in report.get("Results") or []:
            for vuln in result.get("Vulnerabilities") or []:
                vuln_id, severity = vuln.get("VulnerabilityID", "?"), vuln.get("Severity", "?")
                path = vuln.get("PkgPath") or result.get("Target", "")
                line = (f"{name}: {severity} {vuln_id} {vuln.get('PkgName')} {vuln.get('InstalledVersion')}"
                        f" -> {vuln.get('FixedVersion') or 'no fix'}")
                if severity not in ("HIGH", "CRITICAL"):
                    continue
                exc = next((e for e in policy.exceptions if e.covers(name, vuln_id, path)), None)
                if exc is not None:
                    exc.used.add(vuln_id)
                    notes.append(f"{line}  [excepted {exc.id} until {exc.until}]")
                elif severity == "HIGH" and not vuln.get("FixedVersion"):
                    allowed += 1
                    notes.append(f"{line}  [allowed {policy.unfixed_high.id} until {policy.unfixed_high.until}]")
                else:
                    blocking.append(line)
        notes.append(f"{name}: {os_.get('Family')} {os_.get('Name')}, {allowed} HIGH without a fix")
    for exc in policy.exceptions:
        if exc.pkg_path is not None and not exc.used:
            blocking.append(f"{exc.id} ({exc.image}, {exc.pkg_path}) matches nothing: remove it")
        for vuln_id in sorted(exc.vulns - exc.used):
            blocking.append(f"{exc.id} ({exc.image}) lists {vuln_id}, which no longer appears: remove it")
    return blocking, notes


def evaluate_semgrep(report: dict, expected: list[str]) -> tuple[list[str], list[str]]:
    blocking: list[str] = []
    notes: list[str] = []
    for result in report.get("results", []):
        extra = result.get("extra", {})
        line = (f"{result.get('path')}:{result.get('start', {}).get('line')} {extra.get('severity')}"
                f" {result.get('check_id')}: {extra.get('message', '').strip()}")
        (blocking if extra.get("severity") == "ERROR" else notes).append(line)
    for error in report.get("errors", []):
        line = f"semgrep {error.get('level')}: {error.get('type')}: {error.get('message', '')}".strip()
        (blocking if error.get("level") == "error" else notes).append(line)
    scanned = set((report.get("paths") or {}).get("scanned") or [])
    for path in expected:
        if path not in scanned:
            blocking.append(f"{path} was not scanned: check .semgrepignore and the scan target")
    notes.append(f"semgrep: {len(scanned)} files scanned, {len(report.get('results', []))} findings")
    return blocking, notes


def stack_images(config: dict) -> list[str]:
    """Service images plus the sandbox images the manager starts (SANDBOX_IMAGE_<ALIAS>)."""
    images: set[str] = set()
    for service in (config.get("services") or {}).values():
        if service.get("image"):
            images.add(service["image"])
        for key, value in (service.get("environment") or {}).items():
            if key.startswith("SANDBOX_IMAGE_") and value:
                images.add(value)
    return sorted(images)


def _finish(blocking: list[str], notes: list[str]) -> int:
    for line in notes:
        print(line)
    for line in blocking:
        print(f"BLOCKING: {line}")
    print(f"gate: {'FAIL' if blocking else 'PASS'} ({len(blocking)} blocking)")
    return 1 if blocking else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="tool", required=True)
    trivy = sub.add_parser("trivy")
    trivy.add_argument("--images", type=Path, required=True)
    trivy.add_argument("--reports", type=Path, required=True)
    trivy.add_argument("--exceptions", type=Path, default=Path("infra/security/scan-exceptions.toml"))
    trivy.add_argument("--decisions", type=Path, default=Path("docs/DECISIONS.md"))
    trivy.add_argument("--today", type=date.fromisoformat, default=datetime.now(UTC).date())
    sub.add_parser("images")
    semgrep = sub.add_parser("semgrep")
    semgrep.add_argument("--report", type=Path, required=True)
    semgrep.add_argument("--expect", action="append", default=[])
    args = parser.parse_args(argv)

    if args.tool == "images":
        images = stack_images(json.load(sys.stdin))
        if not images:
            print("no images in the compose config", file=sys.stderr)
            return 1
        print("\n".join(images))
        return 0

    if args.tool == "semgrep":
        try:
            report = json.loads(args.report.read_text())
        except (OSError, ValueError) as error:
            return _finish([f"{args.report}: unreadable Semgrep report ({error})"], [])
        return _finish(*evaluate_semgrep(report, args.expect))

    images = [line.strip() for line in args.images.read_text().splitlines() if line.strip()]
    try:
        policy = load_policy(args.exceptions, args.decisions.read_text(), args.today, images)
    except (ConfigError, OSError) as error:
        print(f"CONFIG: {error}")
        print("gate: FAIL (exceptions)")
        return 2
    reports, problems = _read_reports(args.reports)
    if not images:
        problems.append("the image list is empty")
    blocking, notes = evaluate_trivy(images, reports, policy)
    return _finish(problems + blocking, notes)


if __name__ == "__main__":
    sys.exit(main())
