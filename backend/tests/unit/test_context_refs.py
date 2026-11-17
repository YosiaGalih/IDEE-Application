"""The ``context_refs`` schema (WP-3.2a Task 2): a closed set of kinds, no extra keys, positive lines."""

from __future__ import annotations

import uuid

import pytest
from pydantic import TypeAdapter, ValidationError

from app.services.context_refs import MAX_CONTEXT_REFS, ContextRef, FileRef

ADAPTER: TypeAdapter[ContextRef] = TypeAdapter(ContextRef)


def test_discriminator_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "url", "path": "x"})


def test_lines_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "file", "path": "a.py", "line_start": 0})
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "file", "path": "a.py", "line_end": -1})


def test_extra_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "folder", "path": "src", "line_start": 1})
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "task", "id": str(uuid.uuid4()), "extra": 1})


def test_every_kind_parses() -> None:
    uid = str(uuid.uuid4())
    for raw in (
        {"type": "file", "path": "a.py", "line_start": 1, "line_end": 2},
        {"type": "folder", "path": "src"},
        {"type": "task", "id": uid},
        {"type": "diff", "task_id": uid},
        {"type": "test_run", "id": uid},
        {"type": "finding", "id": uid},
    ):
        assert ADAPTER.validate_python(raw).type == raw["type"]
    assert FileRef(type="file", path="a", line_start=None, line_end=None).line_start is None
    assert MAX_CONTEXT_REFS == 20
