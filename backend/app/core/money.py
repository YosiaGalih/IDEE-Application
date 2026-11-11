"""Money as ``Decimal``, from the price table to JSON (spec WP-3.1f Keputusan Lead #4, #13).

- Stored: ``numeric(20,10)``, rounded **up** to ten places, so a non-zero cost never becomes 0 and rounding
  never shrinks what a budget counts.
- In the API: a plain decimal string without an exponent (``"0.0000005"``, never ``"5.000E-7"`` as Pydantic's
  default ``Decimal`` serializer writes it), through ``Money``.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from typing import Annotated, Final

from pydantic import PlainSerializer

COST_QUANTUM: Final = Decimal("1E-10")  # numeric(20,10)
MONEY_PATTERN: Final = r"^(0|[1-9][0-9]*)(\.[0-9]*[1-9])?$"


def _non_negative(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("an amount of money must be finite")
    if value < 0:
        raise ValueError("an amount of money cannot be negative")
    return value.copy_abs()  # -0 becomes 0


def stored_cost(value: Decimal) -> Decimal:
    """``value`` at the ledger's ten decimal places, rounded up (ValueError for negative, NaN or infinite)."""
    return _non_negative(value).quantize(COST_QUANTUM, rounding=ROUND_CEILING)


def decimal_str(value: Decimal) -> str:
    """Plain decimal text matching ``MONEY_PATTERN``: no exponent, no trailing zeros (ValueError for negative
    or non-finite). Exact: unlike ``normalize()``, it never rounds to the context precision."""
    text = format(_non_negative(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# API responses only: requests take money as a string and validate it themselves.
Money = Annotated[Decimal, PlainSerializer(decimal_str, return_type=str)]
