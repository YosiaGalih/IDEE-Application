#!/usr/bin/env python3
"""security-scan gate (spec WP-1.5d, SECURITY_MODEL §11). Reads the JSON that Trivy and Semgrep wrote and decides.

  docker compose ... config --format json | scan_gate.py images     (every image the stack runs, one per line)
  scan_gate.py trivy --images FILE --reports DIR --db-info FILE [--exceptions TOML] [--decisions MD] [--today DATE]
  scan_gate.py semgrep --report FILE --expect PATH [--expect PATH ...] --min-files N

Trivy, per finding: CRITICAL blocks; HIGH with a fixed version blocks; HIGH without one is listed and allowed while
the [unfixed_high] exception (EX-02) lasts; any other severity is unexpected and blocks. An end-of-life OS, a
listed image without exactly one report, a report without an OS or without an os-pkgs result, a report for an
image nobody listed, and a vulnerability DB older than two days block too. [[exception]] entries cover findings of
one image (matched exactly, digest allowed) by CVE and package name, by package path, or an end-of-life OS; each
needs a DECISIONS row whose expiry cell is exactly the same date, at most 92 days ahead, and a CVE or path entry
must still match a finding. Semgrep: findings other than WARNING/INFO and every scan error block; each --expect
path must have been scanned, and at least --min-files files.

Exit 0 pass, 1 blocking findings, 2 the exceptions file or DECISIONS is wrong (an expired exception included).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

MAX_EXCEPTION_DAYS = 92
MAX_DB_AGE = timedelta(days=2)
SEVERITIES = ("HIGH", "CRITICAL")  # what scan-images.sh asks Trivy for


class ConfigError(Exception):
    """The exceptions file or DECISIONS does not allow this run (exit 2)."""


def today_utc() -> date:
    return datetime.now(UTC).date()


def decision_expiry(decisions: str, exc_id: str) -> tuple[date, str]:
    """The expiry date of an open DECISIONS row (its last cell is exactly YYYY-MM-DD) and the row's 'Siapa' cell."""
    row = next((line for line in decisions.splitlines() if line.startswith(f"| {exc_id} |")), None)
    if row is None:
        raise ConfigError(f"{exc_id} has no row in DECISIONS (Pengecualian keamanan)")
    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    try:
        expiry = date.fromisoformat(cells[-1])
    except ValueError:
        raise ConfigError(f"{exc_id}: the DECISIONS row is closed or its expiry is not a plain date") from None
    return expiry, cells[3] if len(cells) > 3 else ""


def check_window(exc_id: str, until: date, today: date) -> None:
    if until < today:
        raise ConfigError(f"{exc_id} expired on {until}: fix the findings, or renew it in DECISIONS and here")
    if (until - today).days > MAX_EXCEPTION_DAYS:
        raise ConfigError(f"{exc_id}: until {until} is more than {MAX_EXCEPTION_DAYS} days ahead")


@dataclass
class ExceptionEntry:
    id: str
    until: date
    image: str
    status: str = ""
    vulns: set[str] = field(default_factory=set)
    pkgs: set[str] = field(default_factory=set)
    pkg_path: str | None = None
    eosl: bool = False
    used: set[str] = field(default_factory=set)

    def for_image(self, name: str) -> bool:
        return name == self.image or name.startswith(self.image + "@")

    def covers(self, name: str, vuln_id: str, pkg: str, path: str) -> bool:
        if not self.for_image(name) or self.eosl:
            return False
        if self.pkg_path is not None:
            return path == self.pkg_path
        return vuln_id in self.vulns and pkg in self.pkgs

    @property
    def label(self) -> str:
        return f"{self.id} until {self.until}" + (f", {self.status}" if "menunggu" in self.status else "")


@dataclass
class Policy:
    unfixed_high: ExceptionEntry
    exceptions: list[ExceptionEntry]


def _checked(entry: dict, decisions: str, today: date, where: str) -> tuple[str, date, str]:
    exc_id, until = entry.get("id"), entry.get("until")
    if not isinstance(exc_id, str) or not re.fullmatch(r"EX-\d{2}", exc_id):
        raise ConfigError(f"{where}: id must look like EX-nn (got {exc_id!r})")
    if not isinstance(until, date) or isinstance(until, datetime):
        raise ConfigError(f"{where} {exc_id}: until must be a date (got {until!r})")
    check_window(exc_id, until, today)
    expiry, status = decision_expiry(decisions, exc_id)
    if expiry != until:
        raise ConfigError(f"{exc_id}: DECISIONS gives expiry {expiry}, the exceptions file {until}")
    return exc_id, until, status


def load_policy(path: Path, decisions: str, today: date, images: list[str] | None = None) -> Policy:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error
    unfixed = data.get("unfixed_high")
    if not isinstance(unfixed, dict):
        raise ConfigError(f"{path}: [unfixed_high] is missing")
    exc_id, until, status = _checked(unfixed, decisions, today, "[unfixed_high]")
    policy = Policy(ExceptionEntry(exc_id, until, "", status), [])
    for number, entry in enumerate(data.get("exception", []), 1):
        exc_id, until, status = _checked(entry, decisions, today, f"[[exception]] #{number}")
        image = entry.get("image")
        vulns, pkgs, pkg_path, eosl = entry.get("vulns"), entry.get("pkgs"), entry.get("pkg_path"), entry.get("eosl")
        if not isinstance(image, str) or not image:
            raise ConfigError(f"{exc_id}: image is required")
        kinds = [vulns is not None, pkg_path is not None, eosl is not None]
        if sum(kinds) != 1:
            raise ConfigError(f"{exc_id} ({image}): give exactly one of vulns (with pkgs), pkg_path, eosl = true")
        if vulns is not None and (not isinstance(vulns, list) or not vulns or not isinstance(pkgs, list) or not pkgs):
            raise ConfigError(f"{exc_id} ({image}): vulns needs a non-empty list and a non-empty pkgs list")
        if eosl is not None and eosl is not True:
            raise ConfigError(f"{exc_id} ({image}): eosl can only be true")
        exc = ExceptionEntry(exc_id, until, image, status, set(vulns or ()), set(pkgs or ()), pkg_path, bool(eosl))
        if images is not None and not any(exc.for_image(name) for name in images):
            raise ConfigError(f"{exc_id}: {image} is not one of the scanned images")
        policy.exceptions.append(exc)
    return policy


def read_reports(directory: Path) -> tuple[dict[str, dict], list[str]]:
    reports: dict[str, dict] = {}
    problems: list[str] = []
    for path in sorted(directory.glob("report-*.json")):
        try:
            report = json.loads(path.read_text())
            name = report["ArtifactName"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            problems.append(f"{path.name}: unreadable Trivy report ({error})")
            continue
        if report.get("SchemaVersion") != 2 or report.get("ArtifactType") != "container_image":
            problems.append(f"{name}: not a Trivy v2 container image report")
        elif name in reports:
            problems.append(f"{name}: more than one report (stale files in the report folder?)")
        else:
            reports[name] = report
    return reports, problems


def check_db(info_path: Path, today: date) -> list[str]:
    try:
        updated = json.loads(info_path.read_text())["VulnerabilityDB"]["UpdatedAt"]
        when = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return [f"{info_path}: cannot read the vulnerability DB date ({error})"]
    print(f"trivy DB updated {when.isoformat()}")
    if today - when.date() > MAX_DB_AGE:
        return [f"the vulnerability DB is from {when.date()}, older than {MAX_DB_AGE.days} days"]
    return []


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
        results = report.get("Results") or []
        if not os_.get("Family"):
            blocking.append(f"{name}: Trivy found no operating system, so OS packages were not checked")
        elif os_.get("EOSL"):
            exc = next((e for e in policy.exceptions if e.eosl and e.for_image(name)), None)
            if exc is None:
                blocking.append(f"{name}: {os_['Family']} {os_.get('Name')} is end of life (no more security fixes)")
            else:
                exc.used.add("eosl")
                notes.append(f"{name}: {os_['Family']} {os_.get('Name')} is end of life  [excepted {exc.label}]")
        if not any(result.get("Class") == "os-pkgs" for result in results):
            blocking.append(f"{name}: no os-pkgs result, so no OS package was checked")
        allowed = 0
        for result in results:
            for vuln in result.get("Vulnerabilities") or []:
                vuln_id, severity, pkg = vuln.get("VulnerabilityID", "?"), vuln.get("Severity"), vuln.get("PkgName")
                path = vuln.get("PkgPath") or result.get("Target", "")
                line = (f"{name}: {severity} {vuln_id} {pkg} {vuln.get('InstalledVersion')}"
                        f" -> {vuln.get('FixedVersion') or 'no fix'}")
                if severity not in SEVERITIES:
                    blocking.append(f"{line}  (unexpected severity)")
                    continue
                exc = next((e for e in policy.exceptions if e.covers(name, vuln_id, pkg or "", path)), None)
                if exc is not None:
                    exc.used.add(vuln_id if exc.pkg_path is None else "path")
                    notes.append(f"{line}  [excepted {exc.label}]")
                elif severity == "HIGH" and not vuln.get("FixedVersion"):
                    allowed += 1
                    notes.append(f"{line}  [allowed {policy.unfixed_high.label}]")
                else:
                    blocking.append(line)
        findings = sum(len(r.get("Vulnerabilities") or []) for r in results)
        notes.append(f"{name}: {os_.get('Family')} {os_.get('Name')}, {len(results)} results, {findings} HIGH/CRITICAL"
                     f" findings, {allowed} HIGH without a fix")
    for exc in policy.exceptions:
        if exc.pkg_path is not None and not exc.used:
            blocking.append(f"{exc.id} ({exc.image}, {exc.pkg_path}) matches nothing: remove it")
        if exc.eosl and not exc.used:
            notes.append(f"{exc.id} ({exc.image}, end of life) is not needed today")
        for vuln_id in sorted(exc.vulns - exc.used):
            blocking.append(f"{exc.id} ({exc.image}) lists {vuln_id}, which no longer appears: remove it")
    return blocking, notes


def evaluate_semgrep(report: dict, expected: list[str], min_files: int) -> tuple[list[str], list[str]]:
    blocking: list[str] = []
    notes: list[str] = []
    for result in report.get("results", []):
        extra = result.get("extra", {})
        line = (f"{result.get('path')}:{result.get('start', {}).get('line')} {extra.get('severity')}"
                f" {result.get('check_id')}: {extra.get('message', '').strip()}")
        (notes if extra.get("severity") in ("WARNING", "INFO") else blocking).append(line)
    for error in report.get("errors", []):
        # Any scan error means a file or rule was not (fully) analysed: block, whatever its level.
        blocking.append(f"semgrep {error.get('level')}: {error.get('type')}: {error.get('path', '')}"
                        f" {error.get('message', '')}".strip())
    scanned = set((report.get("paths") or {}).get("scanned") or [])
    for path in expected:
        if path not in scanned:
            blocking.append(f"{path} was not scanned: check .semgrepignore and the scan target")
    if len(scanned) < min_files:
        blocking.append(f"only {len(scanned)} files scanned, expected at least {min_files}")
    notes.append(f"semgrep: {len(scanned)} files scanned, {len(report.get('results', []))} findings")
    return blocking, notes


def stack_images(config: dict) -> list[str]:
    """Service images plus the sandbox images the manager starts (SANDBOX_IMAGE_<ALIAS>)."""
    images: set[str] = set()
    for name, service in (config.get("services") or {}).items():
        if not service.get("image"):
            raise ValueError(f"service {name} has no image name, so it cannot be scanned")
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
    trivy.add_argument("--db-info", type=Path, required=True, help="output of `trivy version --format json`")
    trivy.add_argument("--exceptions", type=Path, default=Path("infra/security/scan-exceptions.toml"))
    trivy.add_argument("--decisions", type=Path, default=Path("docs/DECISIONS.md"))
    trivy.add_argument("--today", type=date.fromisoformat, default=None)
    sub.add_parser("images")
    semgrep = sub.add_parser("semgrep")
    semgrep.add_argument("--report", type=Path, required=True)
    semgrep.add_argument("--expect", action="append", required=True)
    semgrep.add_argument("--min-files", type=int, required=True)
    args = parser.parse_args(argv)

    if args.tool == "images":
        try:
            images = stack_images(json.load(sys.stdin))
        except ValueError as error:
            print(f"scan_gate images: {error}", file=sys.stderr)
            return 1
        if not images:
            print("scan_gate images: no images in the compose config", file=sys.stderr)
            return 1
        print("\n".join(images))
        return 0

    if args.tool == "semgrep":
        try:
            report = json.loads(args.report.read_text())
        except (OSError, ValueError) as error:
            return _finish([f"{args.report}: unreadable Semgrep report ({error})"], [])
        return _finish(*evaluate_semgrep(report, args.expect, args.min_files))

    today = args.today or today_utc()
    images = [line.strip() for line in args.images.read_text().splitlines() if line.strip()]
    try:
        policy = load_policy(args.exceptions, args.decisions.read_text(), today, images)
    except (ConfigError, OSError) as error:
        print(f"CONFIG: {error}")
        print("gate: FAIL (exceptions)")
        return 2
    reports, problems = read_reports(args.reports)
    if not images:
        problems.append("the image list is empty")
    problems += check_db(args.db_info, today)
    blocking, notes = evaluate_trivy(images, reports, policy)
    return _finish(problems + blocking, notes)


if __name__ == "__main__":
    sys.exit(main())
