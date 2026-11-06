"""Canonical payload hash (SECURITY_MODEL §5, FR-063): the same action always hashes the same, anything else
never does (spec WP-3.1c Goal 2)."""

from __future__ import annotations

import re
import uuid

import pytest

from app.services.approval_store import canonical_json, payload_hash

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
TASK = uuid.UUID("22222222-2222-4222-8222-222222222222")


def test_known_vector() -> None:
    assert canonical_json({"b": [1, "é"], "a": None, "c": True}) == '{"a":null,"b":[1,"é"],"c":true}'.encode()


def test_key_order_does_not_change_the_hash() -> None:
    one = payload_hash(
        "project_delete", project_id=PROJECT, task_id=None, payload={"a": 1, "b": {"x": 1, "y": 2}}
    )
    two = payload_hash(
        "project_delete", project_id=PROJECT, task_id=None, payload={"b": {"y": 2, "x": 1}, "a": 1}
    )
    assert one == two


@pytest.mark.parametrize("bad", [1.5, float("nan"), {1: "x"}, {"s": {1, 2}}, {"t": (1, 2)}, b"raw"])
def test_floats_and_odd_values_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="canonical"):
        canonical_json({"value": bad} if not isinstance(bad, dict) else bad)


def test_target_and_type_are_part_of_the_hash() -> None:
    payload = {"name": "Demo"}
    base = payload_hash("project_delete", project_id=PROJECT, task_id=None, payload=payload)
    assert base != payload_hash("project_delete", project_id=uuid.uuid4(), task_id=None, payload=payload)
    assert base != payload_hash("project_delete", project_id=PROJECT, task_id=TASK, payload=payload)
    assert base != payload_hash("tool_r3", project_id=PROJECT, task_id=None, payload=payload)
    assert base != payload_hash("project_delete", project_id=PROJECT, task_id=None, payload={"name": "Demo "})


def test_hash_is_lowercase_hex() -> None:
    digest = payload_hash("project_delete", project_id=PROJECT, task_id=None, payload={})
    assert re.fullmatch("[0-9a-f]{64}", digest)


def test_bool_is_not_an_int() -> None:
    # json keeps true and 1 apart; a payload that says "true" must not match one that says 1.
    assert canonical_json({"v": True}) != canonical_json({"v": 1})
