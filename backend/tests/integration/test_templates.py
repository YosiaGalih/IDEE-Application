"""Every project template materialises and its own tests pass offline (FR-014, AC-02 template)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.workspace.errors import AlreadyExists, WorkspaceNotFound
from app.workspace.fs import Workspace
from app.workspace.stack import detect_stack
from app.workspace.templates_registry import list_templates, materialize

TEMPLATE_IDS = ["node-basic", "node-http-api", "python-basic", "python-cli", "python-fastapi", "static-site"]
OFFLINE_ENV = {
    "PATH": os.environ["PATH"],
    "HOME": os.environ.get("HOME", str(Path.home())),
    "PYTHONDONTWRITEBYTECODE": "1",
    "npm_config_offline": "true",
    "npm_config_update_notifier": "false",
}


def test_list_templates() -> None:
    templates = list_templates()
    assert [t.id for t in templates] == TEMPLATE_IDS
    for template in templates:
        assert template.name and template.description and template.stack and template.icon


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
async def test_template_materialises_and_tests_pass(tmp_path: Path, template_id: str) -> None:
    dest = tmp_path / "repo"
    written = materialize(template_id, dest, project_name="Proyek Uji", project_slug="proyek-uji")
    assert "README.md" in written
    assert "template.toml" not in written
    assert ".gitignore" in written
    for rel in written:
        text = (dest / rel).read_text()
        assert "{{" not in text, rel
    assert "Proyek Uji" in (dest / "README.md").read_text()

    stack = await detect_stack(Workspace(dest))
    if template_id.startswith("python"):
        assert stack.test_profiles == ["python.pytest"]
        run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], dest)
        ruff = [sys.executable, "-m", "ruff"]
        run(
            [
                *ruff,
                "check",
                "--isolated",
                "--no-cache",
                "--select",
                "E,F,W,I,B,UP,S",
                "--ignore",
                "S101",
                ".",
            ],
            dest,
        )
        run([*ruff, "format", "--isolated", "--no-cache", "--check", "."], dest)
    else:
        assert stack.test_profiles == ["node.npm_test"]
        npm = shutil.which("npm")
        if npm is None:
            pytest.skip("npm is not installed")
        run([npm, "test", "--silent"], dest)


def run(argv: list[str], cwd: Path) -> None:
    result = subprocess.run(  # noqa: S603 - our own template tests, fixed argv
        argv, cwd=cwd, env=OFFLINE_ENV, capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_materialize_validation(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceNotFound):
        materialize("../python-basic", tmp_path / "a", project_name="x", project_slug="x")
    with pytest.raises(WorkspaceNotFound):
        materialize("nope", tmp_path / "a", project_name="x", project_slug="x")
    for slug in ("Bad", "-x", "a b", 'x"; import os', ""):
        with pytest.raises(AppError):
            materialize("python-basic", tmp_path / "a", project_name="x", project_slug=slug)
    for name in ("", "a\nb", "x" * 121):
        with pytest.raises(AppError):
            materialize("python-basic", tmp_path / "a", project_name=name, project_slug="ok")
    (tmp_path / "exists").mkdir()
    with pytest.raises(AlreadyExists):
        materialize("python-basic", tmp_path / "exists", project_name="x", project_slug="ok")


def test_name_is_not_interpreted(tmp_path: Path) -> None:
    """A project name is inserted literally: no template syntax, no slug substitution inside it."""
    name = "{{project_slug}} <b>&</b>"
    materialize("static-site", tmp_path / "repo", project_name=name, project_slug="safe")
    readme = (tmp_path / "repo" / "README.md").read_text()
    assert readme.startswith("# {{project_slug}} <b>&</b>")


def test_project_name_is_escaped_in_html(tmp_path: Path) -> None:
    name = "</title><script>alert(document.domain)</script>"
    materialize("static-site", tmp_path / "repo", project_name=name, project_slug="safe")
    page = (tmp_path / "repo" / "index.html").read_text()
    assert "<script>alert" not in page
    assert "&lt;/title&gt;&lt;script&gt;" in page
    assert (tmp_path / "repo" / "README.md").read_text().startswith(f"# {name}")  # Markdown keeps it literal


def test_failed_materialize_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_write = Path.write_text
    calls = {"n": 0}

    def flaky(self: Path, *args: object, **kwargs: object) -> int:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_write(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", flaky)
    with pytest.raises(OSError, match="disk full"):
        materialize("python-basic", tmp_path / "repo", project_name="x", project_slug="ok")
    assert list(tmp_path.iterdir()) == []


def test_failed_copy_leaves_no_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as shutil_module

    real_copytree = shutil_module.copytree

    def partial_copy(src: Path, dst: Path, *args: object, **kwargs: object) -> object:
        copied = real_copytree(src, dst, *args, **kwargs)  # type: ignore[arg-type]
        if Path(dst).name.startswith(".repo.creating-"):  # the top-level call, not copytree's recursion
            raise OSError("disk full")  # the copy ran out of space after writing the tree
        return copied

    monkeypatch.setattr(shutil_module, "copytree", partial_copy)
    with pytest.raises(OSError, match="disk full"):
        materialize("python-basic", tmp_path / "repo", project_name="x", project_slug="ok")
    assert list(tmp_path.iterdir()) == []
