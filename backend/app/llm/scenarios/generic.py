"""``generic``: a short text answer that ends the run with ``submit_result`` when the request offers it."""

from __future__ import annotations

from app.llm.base import LLMRequest, TextBlock, ToolUseBlock
from app.llm.scenarios.model import Fail, Reply, Scenario

_TEXT = TextBlock("Scripted response.")


def _answer(req: LLMRequest) -> Reply | Fail:
    if not any(tool.name == "submit_result" for tool in req.tools):
        return Reply((_TEXT,))
    turn = sum(1 for message in req.messages if message.role == "assistant")
    return Reply((_TEXT, ToolUseBlock(f"toolu_scripted_{turn}", "submit_result", {})), stop_reason="tool_use")


SCENARIO = Scenario(name="generic", default=(_answer,))
