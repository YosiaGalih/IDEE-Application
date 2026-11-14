"""Runtime prompts and ``prompt_version`` (spec WP-3.1f Keputusan Lead #26; plan WP-3.1f2a Task 2).

Every dispatched agent has a prompt inside the app package; every prompt carries the required rules word for
word, and the loader refuses to start without them; ``prompt_version`` is the first 12 hex of SHA-256.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

import app
from app.agents.prompt_book import (
    PROMPT_VERSION_LENGTH,
    PROMPTS_DIR,
    REQUIRED_RULES,
    PromptBook,
    PromptError,
    prompt_version,
)
from app.agents.registry import load_registry
from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
ABSORBED = {"A01", "C16"}  # AGENT_CATALOG §4.2: absorbed into the A02/A03 runs, never dispatched alone
DISPATCHED = ["A02", "A03", "B14", "C20", "D22", "A07", "B09", "B10", "B13", "G35", "C21"]


def a_prompt(*, without: int | None = None) -> str:
    rules = [f"{n}. {rule}" for n, rule in enumerate(REQUIRED_RULES, start=1) if n != without]
    return "# Role\n\nSECRET-PROMPT-BODY does the work.\n\n## Rules\n\n" + "\n".join(rules) + "\n"


def test_every_dispatched_agent_has_a_prompt(settings: Settings) -> None:
    registry = load_registry(Path(settings.agent_registry_path))
    expected = {
        a.agent_id for a in registry.agents if a.mvp_mode == "llm" or (a.mvp_mode == "profile" and a.enabled)
    } - ABSORBED
    assert PromptBook.load().agent_ids() == expected == set(DISPATCHED)


def test_absorbed_profiles_have_no_prompt() -> None:
    book = PromptBook.load()
    for agent_id in ABSORBED:
        assert book.get(agent_id) is None
        assert not (PROMPTS_DIR / f"{agent_id}.md").exists()


@pytest.mark.parametrize("agent_id", DISPATCHED)
def test_every_prompt_carries_the_required_rules(agent_id: str) -> None:
    text = (PROMPTS_DIR / f"{agent_id}.md").read_text(encoding="utf-8")
    for rule in REQUIRED_RULES:
        assert rule in text
    prompt = PromptBook.load().get(agent_id)
    assert prompt is not None
    assert (prompt.agent_id, prompt.text) == (agent_id, text)


def test_prompt_version_is_the_first_twelve_hex_of_sha256() -> None:
    text = a_prompt()
    version = prompt_version(text)
    assert version == hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    assert re.fullmatch(r"[0-9a-f]{12}", version)
    assert PROMPT_VERSION_LENGTH == 12  # agent_runs.prompt_version char(12)
    for agent_id in DISPATCHED:
        prompt = PromptBook.load().get(agent_id)
        assert prompt is not None
        assert prompt.version == prompt_version(prompt.text)


def test_prompt_version_changes_with_the_content() -> None:
    text = a_prompt()
    assert prompt_version(text) != prompt_version(text.replace("does", "doez"))


def test_a_prompt_repr_does_not_carry_its_text() -> None:
    prompt = PromptBook.load().get("A02")
    assert prompt is not None
    assert prompt.text[:40] not in repr(prompt)


@pytest.mark.parametrize("missing", range(1, len(REQUIRED_RULES) + 1))
def test_loader_refuses_a_prompt_without_a_rule(tmp_path: Path, missing: int) -> None:
    (tmp_path / "A02.md").write_text(a_prompt(), encoding="utf-8")
    (tmp_path / "B14.md").write_text(a_prompt(without=missing), encoding="utf-8")
    with pytest.raises(PromptError) as exc:
        PromptBook.load(tmp_path)
    message = str(exc.value)
    assert "B14.md" in message
    assert f"rule {missing}" in message
    assert "A02.md" not in message
    assert "SECRET-PROMPT-BODY" not in message


def test_loader_lists_every_missing_rule(tmp_path: Path) -> None:
    (tmp_path / "A02.md").write_text("# Role\n", encoding="utf-8")
    with pytest.raises(PromptError) as exc:
        PromptBook.load(tmp_path)
    assert all(f"rule {n}" in str(exc.value) for n in range(1, len(REQUIRED_RULES) + 1))


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("x.md", a_prompt().encode("utf-8")),
        ("a02.md", a_prompt().encode("utf-8")),
        ("A02.md", b""),
        ("A02.md", b"   \n"),
        ("A02.md", a_prompt().replace("does", "d\xe9s").encode("latin-1")),
    ],
    ids=["foreign-name", "lowercase-id", "empty", "blank", "latin-1"],
)
def test_loader_refuses_empty_non_utf8_and_foreign_names(tmp_path: Path, name: str, content: bytes) -> None:
    (tmp_path / name).write_bytes(content)
    with pytest.raises(PromptError) as exc:
        PromptBook.load(tmp_path)
    assert "SECRET-PROMPT-BODY" not in str(exc.value)


def test_a_valid_folder_loads(tmp_path: Path) -> None:
    (tmp_path / "A02.md").write_text(a_prompt(), encoding="utf-8")
    book = PromptBook.load(tmp_path)
    assert book.agent_ids() == frozenset({"A02"})
    assert book.get("B14") is None


def test_prompts_ship_inside_the_app_package() -> None:
    package = Path(app.__file__).resolve().parent
    assert PROMPTS_DIR.resolve().is_relative_to(package)
    # The image copies backend/app whole; the build context lets it through (no Dockerfile change).
    allowed = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "!backend/app/" in allowed
