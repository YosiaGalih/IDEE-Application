"""Line coverage from a Cobertura ``coverage.xml`` (TEST_STRATEGY AC-08: coverage only from the file)."""

from __future__ import annotations

import math

from app.sandbox.safexml import UnsafeXML, parse


def parse_coverage(data: bytes) -> float | None:
    """``line-rate`` in [0, 1], or ``None`` when the file is not a usable Cobertura report."""
    try:
        root = parse(data)
    except UnsafeXML:
        return None
    if root.tag != "coverage":
        return None
    try:
        rate = float(root.get("line-rate", ""))
    except ValueError:
        return None
    if math.isnan(rate) or not 0.0 <= rate <= 1.0:
        return None
    if root.get("lines-valid") == "0":  # 0 of 0 lines: coverage.py writes line-rate="1" for nothing measured
        return None
    return rate
