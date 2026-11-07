"""ToolGateway without a database: registration rules, what is offered to the model, and the output helpers
(spec WP-3.1d Goal 3; TEST_STRATEGY §3 app/tools). Execution is in tests/integration/test_tool_gateway.py."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.tools.base import RiskLevel, ToolOutput
from app.tools.gateway import MAX_RESULT_BYTES, ToolGateway, _truncate  # pyright: ignore[reportPrivateUsage]
from tests.tool_fakes import ReadInput, Recorder, gateway, grant, spec


class LaxInput(BaseModel):
    path: str = ""


def bare() -> ToolGateway:
    return gateway(async_sessionmaker())


@pytest.mark.parametrize(
    ("name", "overrides", "problem"),
    [
        ("repo__read_file", {"input_model": ReadInput, "risk": RiskLevel.R0}, "canonical"),
        ("submit_result", {"input_model": ReadInput, "risk": RiskLevel.R0}, "agent runtime"),
        ("shell.exec", {"input_model": ReadInput, "risk": RiskLevel.R0}, "R4"),
        ("test.read", {"risk": RiskLevel.R4}, "R4"),
        ("nope.tool", {"input_model": ReadInput, "risk": RiskLevel.R0}, "not in the catalog"),
        ("test.read", {"risk": RiskLevel.R1}, "risk differs"),
        ("test.planned", {"input_model": ReadInput, "risk": RiskLevel.R0}, "planned"),
        ("git.push", {"input_model": ReadInput, "risk": RiskLevel.R3}, "disabled"),
        ("test.write", {"write_paths": None}, "write_paths"),
        ("test.read", {"input_model": LaxInput}, "extra"),
        ("test.read", {"timeout_s": 0}, "timeout"),
    ],
)
def test_register_refuses(name: str, overrides: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        bare().register(spec(name, Recorder(), **overrides))


def test_register_twice_is_refused() -> None:
    built = bare()
    built.register(spec("test.read", Recorder()))
    with pytest.raises(ValueError, match="already registered"):
        built.register(spec("test.read", Recorder()))


def test_tool_defs_are_allowlist_and_registered_and_permitted_sorted() -> None:
    built = bare()
    for name in ("test.write", "test.read", "test.exec"):
        built.register(spec(name, Recorder()))
    full = grant(["test.write", "test.read", "test.exec", "repo.read_file"])
    assert [d.name for d in built.tool_defs_for(full)] == ["test.exec", "test.read", "test.write"]
    reviewer = grant(["test.write", "test.read", "test.exec"], filesystem="read_only", sandbox="deny")
    assert [d.name for d in built.tool_defs_for(reviewer)] == ["test.read"]
    schema = built.tool_defs_for(grant(["test.read"]))[0].input_schema
    assert schema["additionalProperties"] is False


def test_truncation_is_bounded_and_marked() -> None:
    text = "é" * MAX_RESULT_BYTES  # two bytes each
    cut = _truncate(text, MAX_RESULT_BYTES)
    assert len(cut.encode()) <= MAX_RESULT_BYTES
    assert cut.endswith("]") and "truncated" in cut
    assert _truncate("short", MAX_RESULT_BYTES) == "short"


def test_tool_output_defaults() -> None:
    assert ToolOutput("x").evidence_refs == ()


def test_a_huge_output_is_redacted_within_a_window_then_cut() -> None:
    from tests.tool_fakes import SECRET

    built = bare()
    content = "x" * 100 + SECRET + "y" * (5 * 1024 * 1024)
    text = built._model_text(content)  # pyright: ignore[reportPrivateUsage]
    assert len(text.encode()) <= MAX_RESULT_BYTES
    assert SECRET not in text and "[REDACTED]" in text
    assert f"truncated: {len(content.encode())} bytes" in text
