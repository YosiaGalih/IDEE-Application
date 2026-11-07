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


def test_redaction_cost_is_bounded_by_the_window() -> None:
    from app.core.audit import AuditWriter
    from app.core.redaction import Redactor
    from app.tools.gateway import REDACT_WINDOW_CHARS

    seen: list[int] = []

    class Measuring(Redactor):
        def redact(self, text: str) -> str:
            seen.append(len(text))
            return super().redact(text)

    built = ToolGateway(sessions=async_sessionmaker(), audit=AuditWriter(Measuring()), ports=None)  # type: ignore[arg-type]
    built._model_text("z" * (3 * REDACT_WINDOW_CHARS))  # pyright: ignore[reportPrivateUsage]
    assert seen and max(seen) <= REDACT_WINDOW_CHARS


@pytest.mark.parametrize("name", ["test.write", "test.exec", "test.r3"])
def test_risky_tools_must_be_side_effects(name: str) -> None:
    # L3: an R1+ tool without side_effect would skip the idempotency key and could run twice.
    with pytest.raises(ValueError, match="side_effect"):
        bare().register(spec(name, Recorder(), side_effect=False))


def test_an_r3_input_model_cannot_exclude_fields() -> None:
    from pydantic import ConfigDict, Field

    class Hiding(BaseModel):
        model_config = ConfigDict(extra="forbid")
        target: str
        hidden: str = Field(default="", exclude=True)

    with pytest.raises(ValueError, match="exclude"):
        bare().register(spec("test.r3", Recorder(), input_model=Hiding))


def test_tool_statuses_match_the_table() -> None:
    # The model statuses are the table's, minus the claim ("running") that never reaches a caller.
    from typing import get_args

    from app.models.enums import TOOL_CALL_STATUSES
    from app.tools.base import ToolStatus

    assert {*get_args(ToolStatus), "running"} == set(TOOL_CALL_STATUSES)
