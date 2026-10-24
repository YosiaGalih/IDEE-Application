"""Project templates (FR-014, API_CONTRACTS ``GET /projects/templates``).

**Format.** Each template is a folder under ``app/workspace/templates/<id>/`` with a ``template.toml``
(id, name, description, stack, icon) and the files of a runnable skeleton. The skeleton includes a README
that says how to run it and how to test it.

**Materialisation.**
- Files are copied as they are.
- Two placeholders are replaced with plain ``str.replace`` (no template engine, so nothing in a name can be
  evaluated):
  - ``{{project_name}}`` in Markdown/HTML;
  - ``{{project_slug}}`` everywhere. The slug is validated, so it is safe inside TOML, JSON and code.

Every template's tests pass without network access (``tests/integration/test_templates.py``).
"""

from __future__ import annotations

import html
import os
import re
import secrets
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import AppError
from app.workspace.errors import AlreadyExists, WorkspaceNotFound

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
METADATA = "template.toml"
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_NAME_MAX = 120
_NAME_FILES = (".md", ".html")


@dataclass(frozen=True)
class TemplateInfo:
    id: str
    name: str
    description: str
    stack: str
    icon: str


def _load(folder: Path) -> TemplateInfo:
    data = tomllib.loads((folder / METADATA).read_text())
    return TemplateInfo(data["id"], data["name"], data["description"], data["stack"], data["icon"])


def list_templates() -> list[TemplateInfo]:
    return sorted(
        (_load(folder) for folder in TEMPLATES_DIR.iterdir() if (folder / METADATA).is_file()),
        key=lambda t: t.id,
    )


def materialize(template_id: str, dest: Path, *, project_name: str, project_slug: str) -> list[str]:
    """Copy template ``template_id`` into the new folder ``dest``; returns the files written."""
    if not _ID.fullmatch(template_id) or not (TEMPLATES_DIR / template_id / METADATA).is_file():
        raise WorkspaceNotFound()
    if not _SLUG.fullmatch(project_slug):
        raise AppError("BAD_REQUEST", "Invalid project slug")
    if not 1 <= len(project_name) <= _NAME_MAX or any(ch in project_name for ch in "\x00\r\n"):
        raise AppError("BAD_REQUEST", "Invalid project name")
    if os.path.lexists(dest):
        raise AlreadyExists()

    source = TEMPLATES_DIR / template_id
    staging = dest.parent / f".{dest.name}.creating-{secrets.token_hex(6)}"
    try:
        shutil.copytree(
            source, staging, ignore=shutil.ignore_patterns(METADATA, "__pycache__"), symlinks=False
        )
        written: list[str] = []
        for path in sorted(p for p in staging.rglob("*") if p.is_file()):
            text = path.read_text()
            replaced = text.replace("{{project_slug}}", project_slug)
            if path.suffix == ".html":  # the name is user text: escaped where a browser renders it
                replaced = replaced.replace("{{project_name}}", html.escape(project_name))
            elif path.suffix in _NAME_FILES:
                replaced = replaced.replace("{{project_name}}", project_name)
            if replaced != text:
                path.write_text(replaced)
            written.append(path.relative_to(staging).as_posix())
        if os.path.lexists(dest):
            raise AlreadyExists()
        os.rename(staging, dest)  # all or nothing: a failed copy never leaves a half project at dest
    finally:
        if os.path.lexists(staging):
            shutil.rmtree(staging)
    return written
