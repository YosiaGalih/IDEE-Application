"""``generic``: a short text answer that ends the run with ``submit_result`` when the request offers it.

The runtime names the output contract of the run in ``metadata["output_contract"]`` (profiles share their
runtime agent's contract, while scripts are chosen per agent id). ``generic`` answers with the literal output
of that contract: it claims nothing (no changed file, no finding, nothing verified). ``app.llm`` cannot import
``app.agents``, so tests/unit/test_llm_scripted.py validates these literals against the real contracts. An
unknown contract gets ``{}``, which the runtime reports as invalid (ADR-015, WP-3.1f2a Task 9).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from app.llm.base import LLMRequest, TextBlock, ToolUseBlock
from app.llm.scenarios.model import Fail, Reply, Scenario

_TEXT = TextBlock("Scripted response.")
_REQUIREMENT: Mapping[str, Any] = {
    "key": "REQ-1",
    "title": "Do what the prompt asks",
    "description": "Scripted requirement: the change the prompt describes.",
    "priority": "must",
    "acceptance_criteria": [
        {
            "key": "AC-1",
            "criterion": "Scripted criterion: the change works as the prompt describes.",
            "verification_method": "test",
            "mandatory": True,
        }
    ],
}
OUTPUTS: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "IntakeOutput": {
            "title": "Scripted task",
            "summary": "Scripted intake: one requirement with one criterion.",
            "workflow_type": "create",
            "complexity": "low",
            "risk": "low",
            "requirements": [_REQUIREMENT],
            "constraints": [],
            "detected_stack_hints": [],
            "clarification_questions": [],
        },
        "PlanOutput": {
            "steps": [
                {
                    "id": "P1",
                    "title": "Implement the requirement",
                    "specialist": "B14",
                    "description": "Scripted step.",
                    "depends_on": [],
                    "requirement_keys": ["REQ-1"],
                    "files_hint": [],
                }
            ],
            "test_plan": {"summary": "Scripted test plan.", "criterion_keys": ["AC-1"]},
            "risks": [],
        },
        "ImplementOutput": {
            "summary": "Scripted run: nothing was changed.",
            "changed_files": [],
            "evidence_refs": [],
            "follow_ups": [],
        },
        "DebugOutput": {
            "root_cause": "Scripted run: no root cause was looked for.",
            "category": "assertion",
            "fix_summary": "Scripted run: nothing was fixed.",
            "changed_files": [],
            "next_step": "escalate",
            "evidence_refs": [],
        },
        "ReviewOutput": {"summary": "Scripted run: nothing was reviewed.", "findings": []},
        "UserProxyOutput": {
            "status": "unverified",
            "summary": "Scripted run: nothing was verified.",
            "criteria_results": [],
            "next_actions": [],
            "confidence": "low",
        },
    }
)


def _answer(req: LLMRequest) -> Reply | Fail:
    """Text only when submit_result is not offered; otherwise submit_result with OUTPUTS[metadata
    "output_contract"], or {} for a contract this scenario does not know (the runtime reports it invalid)."""
    if not any(tool.name == "submit_result" for tool in req.tools):
        return Reply((_TEXT,))
    turn = sum(1 for message in req.messages if message.role == "assistant")
    output = copy.deepcopy(dict(OUTPUTS.get(req.metadata.get("output_contract", ""), {})))
    return Reply(
        (_TEXT, ToolUseBlock(f"toolu_scripted_{turn}", "submit_result", output)), stop_reason="tool_use"
    )


SCENARIO = Scenario(name="generic", default=(_answer,))
