"""``readiness``: the answers the readiness gate runner (``app.llm.readiness``) expects, one script per step.

It proves the runner and its wiring in dev and test, never the vendor: the scripted provider ignores time
limits, so ``readiness.timeout`` simply fails with ``timeout``. A report from scripted mode never counts as
ready for ``LLM_PROVIDER=anthropic`` (Lead decision 3.1e #8).
"""

from __future__ import annotations

import re

from app.llm.base import LLMRequest, TextBlock, ToolUseBlock
from app.llm.scenarios.model import Fail, Reply, Scenario

_VALUE = re.compile(r'value "([^"]+)"')


def _echo(req: LLMRequest) -> Reply | Fail:
    asked = req.messages[0].content
    match = _VALUE.search(asked) if isinstance(asked, str) else None
    if match is None:
        return Fail("bad_request")
    call = ToolUseBlock("toolu_readiness", "readiness.echo", {"value": match.group(1)})
    return Reply((call,), stop_reason="tool_use")


SCENARIO = Scenario(
    name="readiness",
    default=(Reply((TextBlock("ok"),)),),
    scripts={
        "readiness.probe": (Reply((TextBlock("ready"),)),),
        "readiness.tool": (_echo, Reply((TextBlock("echo received"),))),
        "readiness.timeout": (Fail("timeout"),),
        "readiness.cancel": (Reply((TextBlock("counting " * 20),)),),
    },
)
