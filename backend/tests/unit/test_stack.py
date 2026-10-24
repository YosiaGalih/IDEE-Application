"""Deterministic stack detection from marker files (FR-022, ``repo.detect_stack``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.workspace.fs import Workspace
from app.workspace.stack import Stack, detect_stack


def tree(root: Path, files: dict[str, str]) -> Workspace:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return Workspace(root)


PYPROJECT_UV = """
[project]
name = "svc"
dependencies = ["fastapi>=0.110", "SQLAlchemy[asyncio]~=2.0"]
[dependency-groups]
dev = ["pytest>=8"]
"""


async def test_python_uv_fastapi(tmp_path: Path) -> None:
    ws = tree(tmp_path, {"pyproject.toml": PYPROJECT_UV, "uv.lock": "", "app/main.py": "", "src/x.py": ""})
    assert await detect_stack(ws) == Stack(
        languages=["python"],
        frameworks=["fastapi"],
        package_managers=["uv"],
        test_profiles=["python.pytest"],
        entrypoints=["app/main.py"],
    )


async def test_python_requirements_flask_without_lock_is_pip(tmp_path: Path) -> None:
    ws = tree(tmp_path, {"requirements.txt": "Flask==3.0  # web\n-r other.txt\n", "app.py": ""})
    stack = await detect_stack(ws)
    assert (stack.frameworks, stack.package_managers, stack.entrypoints) == (["flask"], ["pip"], ["app.py"])
    assert stack.test_profiles == []


async def test_django_by_manage_py_and_tests_folder(tmp_path: Path) -> None:
    ws = tree(tmp_path, {"manage.py": "", "Pipfile": "", "Pipfile.lock": "{}", "tests/test_views.py": ""})
    stack = await detect_stack(ws)
    assert stack.frameworks == ["django"]
    assert stack.package_managers == ["pipenv"]
    assert stack.test_profiles == ["python.pytest"]
    assert stack.entrypoints == ["manage.py"]


async def test_poetry_dependencies(tmp_path: Path) -> None:
    pyproject = '[tool.poetry.dependencies]\npython = "^3.12"\ndjango = "^5"\n[tool.pytest.ini_options]\n'
    ws = tree(tmp_path, {"pyproject.toml": pyproject, "poetry.lock": ""})
    stack = await detect_stack(ws)
    assert (stack.frameworks, stack.package_managers, stack.test_profiles) == (
        ["django"],
        ["poetry"],
        ["python.pytest"],
    )


async def test_node_typescript_vite_pnpm(tmp_path: Path) -> None:
    package = {
        "name": "web",
        "packageManager": "pnpm@9.1.0",
        "scripts": {"test": "vitest run"},
        "dependencies": {"react": "^18"},
        "devDependencies": {"vite": "^5", "typescript": "^5"},
    }
    ws = tree(tmp_path, {"package.json": json.dumps(package), "src/main.ts": "", "index.html": ""})
    assert await detect_stack(ws) == Stack(
        languages=["html", "javascript", "typescript"],
        frameworks=["react", "vite"],
        package_managers=["pnpm"],
        test_profiles=["node.npm_test"],
        entrypoints=["src/main.ts"],
    )


async def test_npm_default_test_script_is_not_a_test_profile(tmp_path: Path) -> None:
    package = {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}, "main": "./server.js"}
    ws = tree(tmp_path, {"package.json": json.dumps(package), "server.js": "", "package-lock.json": "{}"})
    stack = await detect_stack(ws)
    assert stack.test_profiles == []
    assert stack.package_managers == ["npm"]
    assert stack.entrypoints == ["server.js"]


async def test_monorepo_one_level_down(tmp_path: Path) -> None:
    ws = tree(
        tmp_path,
        {
            "backend/pyproject.toml": PYPROJECT_UV,
            "backend/uv.lock": "",
            "backend/app/main.py": "",
            "frontend/package.json": json.dumps({"dependencies": {"express": "4"}}),
            "frontend/yarn.lock": "",
            "frontend/index.js": "",
        },
    )
    stack = await detect_stack(ws)
    assert stack.languages == ["javascript", "python"]
    assert stack.frameworks == ["express", "fastapi"]
    assert stack.package_managers == ["uv", "yarn"]
    assert stack.entrypoints == ["backend/app/main.py", "frontend/index.js"]


async def test_other_languages_have_no_profiles(tmp_path: Path) -> None:
    ws = tree(tmp_path, {"go.mod": "module x\n", "svc/Cargo.toml": "", "pom.xml": ""})
    stack = await detect_stack(ws)
    assert stack.languages == ["go", "java", "rust"]
    assert (stack.test_profiles, stack.package_managers) == ([], [])


@pytest.mark.parametrize(
    "files",
    [
        {"pyproject.toml": "not = [valid toml", "main.py": ""},
        {"package.json": "{not json", "index.js": ""},
        {"package.json": "[1, 2]"},
    ],
)
async def test_broken_markers_are_ignored_not_guessed(tmp_path: Path, files: dict[str, str]) -> None:
    stack = await detect_stack(tree(tmp_path, files))
    assert stack.frameworks == []
    assert stack.test_profiles == []


async def test_denied_and_dependency_folders_are_not_read(tmp_path: Path) -> None:
    ws = tree(
        tmp_path,
        {
            "node_modules/pkg/package.json": json.dumps({"dependencies": {"express": "4"}}),
            ".venv/pyproject.toml": PYPROJECT_UV,
            ".agentignore": "private/\n",
            "private/package.json": json.dumps({"dependencies": {"react": "18"}}),
        },
    )
    assert await detect_stack(ws) == Stack()


async def test_deterministic(tmp_path: Path) -> None:
    ws = tree(tmp_path, {"pyproject.toml": PYPROJECT_UV, "package.json": "{}", "go.mod": ""})
    assert await detect_stack(ws) == await detect_stack(ws)


@pytest.mark.parametrize(
    "files",
    [
        {"pyproject.toml": "project = 5\n"},
        {"pyproject.toml": "tool = 1\n"},
        {"pyproject.toml": "[tool]\npoetry = 1\n"},
        {"pyproject.toml": "[project]\noptional-dependencies = [1]\n"},
        {"pyproject.toml": "dependency-groups = [1]\n"},
        {"pyproject.toml": "[tool.poetry]\ngroup = [1]\n"},
        {"pyproject.toml": "[project]\ndependencies = 5\n"},
        {"pyproject.toml": "x = " + "[" * 20000 + "]" * 20000 + "\n"},
        {"package.json": "[" * 20000 + "]" * 20000},
        {"package.json": json.dumps({"scripts": 5, "dependencies": [1], "main": 7, "packageManager": 3})},
    ],
)
async def test_hostile_markers_never_crash(tmp_path: Path, files: dict[str, str]) -> None:
    """Markers come from imported or sandbox-written projects: wrong types and deep nesting are ignored."""
    stack = await detect_stack(tree(tmp_path, files))
    assert stack.frameworks == []
