"""``faults``: the agent id names the failure to produce (TEST_STRATEGY §4, PRD §7.3.3).

Each ``LLMError`` kind fails every time (``rate_limited`` with ``retry-after: 1``); ``slow_stream`` answers
with a long text for cancel-mid-stream tests; any other agent gets a plain reply.
"""

from __future__ import annotations

from typing import get_args

from app.llm.base import ErrorKind, TextBlock, Usage
from app.llm.scenarios.model import Fail, Reply, Scenario, Turn

_FAULTS: dict[str, tuple[Turn, ...]] = {
    kind: (Fail(kind, retry_after_s=1.0 if kind == "rate_limited" else None),) for kind in get_args(ErrorKind)
}

SCENARIO = Scenario(
    name="faults",
    default=(Reply((TextBlock("No fault requested."),)),),
    scripts={
        **_FAULTS,
        "slow_stream": (
            Reply((TextBlock("streaming " * 200),), usage=Usage(input_tokens=50, output_tokens=400)),
        ),
    },
)
