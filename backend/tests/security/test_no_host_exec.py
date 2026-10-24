"""Project code never runs in the control plane (CLAUDE.md #3, SECURITY_MODEL T3).

Only ``app/gitops/`` may start processes, and nothing may start one through a shell.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
PROCESS_ALLOWED = APP / "gitops"
_PROCESS_MODULES = {"subprocess", "pty"}
_OS_CALL_PREFIXES = ("system", "popen", "spawn", "exec", "fork")


def violations(source: str, *, process_allowed: bool) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            names = []
        if not process_allowed:
            found += [f"import {name}" for name in names if name in _PROCESS_MODULES]

        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.name
                os_call = node.module == "os" and name.startswith(_OS_CALL_PREFIXES)
                if name == "create_subprocess_shell" or (
                    not process_allowed and (name == "create_subprocess_exec" or os_call)
                ):
                    found.append(f"from-import {name}")

        if isinstance(node, ast.Attribute | ast.Name):
            name = node.attr if isinstance(node, ast.Attribute) else node.id
            if name == "create_subprocess_shell":
                found.append(name)
            elif name == "create_subprocess_exec" and not process_allowed:
                found.append(name)
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and name.startswith(_OS_CALL_PREFIXES)
                and not process_allowed
            ):
                found.append(f"os.{name}")
        if isinstance(node, ast.keyword) and node.arg == "shell":
            found.append("shell=")
    return found


def test_checker_flags_shell_and_processes() -> None:
    assert violations("import asyncio\nasyncio.create_subprocess_shell('x')", process_allowed=True)
    assert violations("import subprocess", process_allowed=False)
    assert violations("import os\nos.system('x')", process_allowed=False)
    assert violations("from asyncio import create_subprocess_exec", process_allowed=False)
    assert violations("from os import system", process_allowed=False)
    assert violations("run(['x'], shell=True)", process_allowed=True)
    assert not violations("import asyncio\nasyncio.create_subprocess_exec('git')", process_allowed=True)


def test_processes_only_in_gitops() -> None:
    problems = {
        str(path.relative_to(APP)): found
        for path in sorted(APP.rglob("*.py"))
        if (found := violations(path.read_text(), process_allowed=path.is_relative_to(PROCESS_ALLOWED)))
    }
    assert problems == {}
