"""Deterministic stack detection (FR-022, TOOL_CATALOG ``repo.detect_stack``, API_CONTRACTS ``Stack``).

**What is read**
- Only small marker files, at the project root and one folder level down.
- They are read through ``Workspace`` with ``AGENT_READ``, so the denylist and the 256 KiB limit apply.

**What is never done**
- Nothing is executed: no build tool, no package manager.
- A marker that does not parse is ignored rather than guessed at.

**Result.** The same tree always gives the same ``Stack``, with every list sorted.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any

from app.workspace.errors import PathViolation, WorkspaceNotFound
from app.workspace.fs import TreeEntry, Workspace
from app.workspace.policy import Access

_PY_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile")
_PY_LOCKS = (("uv.lock", "uv"), ("poetry.lock", "poetry"), ("Pipfile.lock", "pipenv"), ("pdm.lock", "pdm"))
_PY_FRAMEWORKS = ("django", "fastapi", "flask")
_PY_ENTRYPOINTS = ("main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "app/main.py", "src/main.py")
_NODE_LOCKS = (
    ("package-lock.json", "npm"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
)
_NODE_FRAMEWORKS = ("express", "next", "react", "vite", "vue")
_NODE_ENTRYPOINTS = ("index.js", "server.js", "src/index.js", "src/index.ts", "src/main.js", "src/main.ts")
_OTHER_LANGUAGES = (
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("build.gradle.kts", "java"),
)
_NPM_DEFAULT_TEST = 'echo "Error: no test specified" && exit 1'
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class Stack:
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    test_profiles: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)


class _Found:
    def __init__(self) -> None:
        self.languages: set[str] = set()
        self.frameworks: set[str] = set()
        self.package_managers: set[str] = set()
        self.test_profiles: set[str] = set()
        self.entrypoints: set[str] = set()

    def stack(self) -> Stack:
        return Stack(
            sorted(self.languages),
            sorted(self.frameworks),
            sorted(self.package_managers),
            sorted(self.test_profiles),
            sorted(self.entrypoints),
        )


def _paths(entries: list[TreeEntry], into: set[str]) -> None:
    for entry in entries:
        into.add(entry.path)
        if entry.children:
            _paths(entry.children, into)


def _requirement_names(lines: list[str]) -> set[str]:
    names: set[str] = set()
    for line in lines:
        if (match := _REQUIREMENT_NAME.match(line.split("#", 1)[0])) is not None:
            names.add(match[1].lower().replace("_", "-"))
    return names


def _pyproject_deps(data: dict[str, Any]) -> set[str]:
    project = data.get("project", {})
    lines: list[str] = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        lines += group
    for group in data.get("dependency-groups", {}).values():
        lines += [item for item in group if isinstance(item, str)]
    poetry = data.get("tool", {}).get("poetry", {})
    lines += list(poetry.get("dependencies", {})) + list(poetry.get("dev-dependencies", {}))
    for group in poetry.get("group", {}).values():
        lines += list(group.get("dependencies", {}))
    return _requirement_names([line for line in lines if isinstance(line, str)])


class _Detector:
    def __init__(self, workspace: Workspace, files: set[str]) -> None:
        self.ws = workspace
        self.files = files
        self.found = _Found()

    async def read(self, path: str) -> str | None:
        try:
            content = await self.ws.read_file(path, Access.AGENT_READ)
        except (PathViolation, WorkspaceNotFound):
            return None
        return content.content

    def has(self, folder: str, name: str) -> bool:
        return f"{folder}{name}" in self.files

    async def python(self, folder: str) -> None:
        markers = [name for name in _PY_MARKERS if self.has(folder, name)]
        here = [p[len(folder) :] for p in self.files if p.startswith(folder) and "/" not in p[len(folder) :]]
        requirements = sorted(f"{folder}{n}" for n in here if re.fullmatch(r"requirements[\w.-]*\.txt", n))
        py_files = [n for n in here if n.endswith(".py")]
        if not (markers or requirements or py_files):
            return
        found = self.found
        found.languages.add("python")
        deps: set[str] = set()
        pyproject: dict[str, Any] = {}
        if self.has(folder, "pyproject.toml") and (text := await self.read(f"{folder}pyproject.toml")):
            try:
                pyproject = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                pyproject = {}
            deps |= _pyproject_deps(pyproject)
        for path in requirements:
            if text := await self.read(path):
                deps |= _requirement_names(text.splitlines())

        managers = {manager for lock, manager in _PY_LOCKS if self.has(folder, lock)}
        if self.has(folder, "Pipfile"):
            managers.add("pipenv")
        if not managers and (markers or requirements or folder == ""):
            managers.add("pip")  # a project folder without a lock; a plain source folder adds nothing
        found.package_managers |= managers
        found.frameworks |= {name for name in _PY_FRAMEWORKS if name in deps}
        if self.has(folder, "manage.py"):
            found.frameworks.add("django")

        pytest_config = "pytest" in pyproject.get("tool", {})
        test_layout = any(
            p.startswith(f"{folder}tests/")
            or p.rsplit("/", 1)[-1].startswith("test_")
            or p in (f"{folder}conftest.py", f"{folder}pytest.ini")
            for p in self.files
            if p.startswith(folder)
        )
        if "pytest" in deps or pytest_config or test_layout:
            found.test_profiles.add("python.pytest")
        found.entrypoints |= {f"{folder}{name}" for name in _PY_ENTRYPOINTS if self.has(folder, name)}

    async def node(self, folder: str) -> None:
        if not self.has(folder, "package.json"):
            if self.has(folder, "index.html"):
                self.found.languages.add("html")
            return
        found = self.found
        found.languages.add("javascript")
        if self.has(folder, "index.html"):
            found.languages.add("html")
        package: dict[str, Any] = {}
        if text := await self.read(f"{folder}package.json"):
            try:
                loaded = json.loads(text)
            except ValueError:
                loaded = {}
            package = loaded if isinstance(loaded, dict) else {}
        deps: set[str] = set()
        for key in ("dependencies", "devDependencies"):
            section = package.get(key)
            if isinstance(section, dict):
                deps |= {str(name) for name in section}
        if self.has(folder, "tsconfig.json") or "typescript" in deps:
            found.languages.add("typescript")

        managers = {manager for lock, manager in _NODE_LOCKS if self.has(folder, lock)}
        declared = package.get("packageManager")
        if isinstance(declared, str) and declared.split("@", 1)[0] in ("npm", "pnpm", "yarn", "bun"):
            managers.add(declared.split("@", 1)[0])
        found.package_managers |= managers or {"npm"}
        found.frameworks |= {name for name in _NODE_FRAMEWORKS if name in deps}

        scripts = package.get("scripts")
        test = scripts.get("test") if isinstance(scripts, dict) else None
        if isinstance(test, str) and test.strip() and test.strip() != _NPM_DEFAULT_TEST:
            found.test_profiles.add("node.npm_test")

        main = package.get("main")
        if isinstance(main, str) and self.has(folder, main.removeprefix("./")):
            found.entrypoints.add(f"{folder}{main.removeprefix('./')}")
        found.entrypoints |= {f"{folder}{name}" for name in _NODE_ENTRYPOINTS if self.has(folder, name)}

    def others(self, folder: str) -> None:
        self.found.languages |= {lang for marker, lang in _OTHER_LANGUAGES if self.has(folder, marker)}


async def detect_stack(workspace: Workspace) -> Stack:
    """The stack of the project at ``workspace``: root folder plus one level of sub-folders."""
    tree = await workspace.tree(".", Access.AGENT_READ, depth=3, max_entries=5000)
    files: set[str] = set()
    _paths(tree.entries, files)
    folders = ["", *sorted(f"{e.path}/" for e in tree.entries if e.type == "dir")]
    detector = _Detector(workspace, files)
    for folder in folders:
        await detector.python(folder)
        await detector.node(folder)
        detector.others(folder)
    return detector.found.stack()
