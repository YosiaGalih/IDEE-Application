"""Project code never runs in the control plane (CLAUDE.md #3, SECURITY_MODEL T3).

Only ``app/gitops/`` may start processes, and nothing may start one through a shell. The checker is static
and errs on the side of flagging. All of these count:
- aliases of ``os``;
- ``getattr(os, ...)``;
- dynamic imports by name;
- process-capable modules (``ctypes``, ``multiprocessing``, ``_posixsubprocess``).
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
PROCESS_ALLOWED = APP / "gitops"
_PROCESS_MODULES = {"subprocess", "pty", "_posixsubprocess", "ctypes", "multiprocessing", "pexpect"}
_OS_CALL_PREFIXES = ("system", "popen", "spawn", "exec", "fork", "posix_spawn")
_SHELL_NAMES = {"create_subprocess_shell", "subprocess_shell"}
_EXEC_NAMES = {"create_subprocess_exec", "subprocess_exec"}
_DYNAMIC_IMPORTERS = {"import_module", "__import__"}


def _literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def violations(source: str, *, process_allowed: bool) -> list[str]:
    tree = ast.parse(source)
    os_names = {"os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            os_names |= {alias.asname for alias in node.names if alias.name == "os" and alias.asname}

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom) and not process_allowed:
            modules = (
                [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            )
            found += [f"import {m}" for m in modules if m.split(".")[0] in _PROCESS_MODULES]
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.name
                os_call = node.module == "os" and name.startswith(_OS_CALL_PREFIXES)
                if name in _SHELL_NAMES or (not process_allowed and (name in _EXEC_NAMES or os_call)):
                    found.append(f"from-import {name}")

        if isinstance(node, ast.Attribute | ast.Name):
            name = node.attr if isinstance(node, ast.Attribute) else node.id
            if name in _SHELL_NAMES:
                found.append(name)
            elif name in _EXEC_NAMES and not process_allowed:
                found.append(name)
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in os_names
                and name.startswith(_OS_CALL_PREFIXES)
                and not process_allowed
            ):
                found.append(f"os.{name}")

        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            first = _literal(node.args[0]) if node.args else None
            second = _literal(node.args[1]) if len(node.args) > 1 else None
            if name == "getattr" and second and second.startswith(_OS_CALL_PREFIXES) and not process_allowed:
                found.append(f"getattr {second}")
            if name in _DYNAMIC_IMPORTERS and first and first.split(".")[0] in _PROCESS_MODULES:
                found.append(f"dynamic import {first}")

        if isinstance(node, ast.keyword) and node.arg == "shell":
            found.append("shell=")
    return found


def test_checker_flags_shell_and_processes() -> None:
    must_flag_anywhere = [
        "import asyncio\nasyncio.create_subprocess_shell('x')",
        "loop.subprocess_shell(factory, 'x')",
        "run(['x'], shell=True)",
        "import importlib\nimportlib.import_module('subprocess')",
        "__import__('subprocess')",
    ]
    must_flag_outside_gitops = [
        "import subprocess",
        "import os\nos.system('x')",
        "import os\nos.posix_spawn('/bin/sh', [], {})",
        "import os as o\no.system('x')",
        "import os\ngetattr(os, 'system')('x')",
        "from asyncio import create_subprocess_exec",
        "loop.subprocess_exec(factory, 'x')",
        "from os import system",
        "from os import posix_spawnp",
        "import ctypes",
        "import multiprocessing",
        "import _posixsubprocess",
    ]
    for source in must_flag_anywhere:
        assert violations(source, process_allowed=True), source
    for source in must_flag_outside_gitops:
        assert violations(source, process_allowed=False), source
    assert not violations("import asyncio\nasyncio.create_subprocess_exec('git')", process_allowed=True)
    assert not violations("import os\nos.path.join('a', 'b')\nos.fsync(3)", process_allowed=False)


def test_processes_only_in_gitops() -> None:
    problems = {
        str(path.relative_to(APP)): found
        for path in sorted(APP.rglob("*.py"))
        if "templates" not in path.relative_to(APP).parts  # project skeletons, never imported by the app
        if (found := violations(path.read_text(), process_allowed=path.is_relative_to(PROCESS_ALLOWED)))
    }
    assert problems == {}
