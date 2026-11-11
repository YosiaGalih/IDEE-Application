"""Money as ``Decimal`` end to end (spec WP-3.1f Keputusan Lead #4, #13; plan 3.1f1 Keputusan plan #6, #7)."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.core.money import COST_QUANTUM, MONEY_PATTERN, Money, decimal_str, stored_cost


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("5.000E-7"), "0.0000005"),
        (Decimal("0E-10"), "0"),
        (Decimal("2.0000000000"), "2"),
        (Decimal("1E+2"), "100"),
        (Decimal("0"), "0"),
        (Decimal("-0"), "0"),
        (Decimal("2.5"), "2.5"),
        (Decimal("0.0000000001"), "0.0000000001"),
        (Decimal("1234567890.1234567890"), "1234567890.123456789"),
    ],
)
def test_decimal_str(value: Decimal, expected: str) -> None:
    text = decimal_str(value)
    assert text == expected
    assert re.fullmatch(MONEY_PATTERN, text)
    assert Decimal(text) == value


@pytest.mark.parametrize("value", [Decimal("-0.1"), Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity")])
def test_decimal_str_refuses_negative_and_non_finite(value: Decimal) -> None:
    with pytest.raises(ValueError):
        decimal_str(value)


def test_stored_cost_rounds_up_never_to_zero() -> None:
    assert COST_QUANTUM == Decimal("1E-10")
    assert stored_cost(Decimal("1E-11")) == Decimal("1E-10")
    assert stored_cost(Decimal("0.00000000011")) == Decimal("0.0000000002")
    assert stored_cost(Decimal("0.0000005")) == Decimal("0.0000005")
    assert stored_cost(Decimal("0")) == Decimal("0")
    assert stored_cost(Decimal("0.0000005")).as_tuple().exponent == -10


@pytest.mark.parametrize(
    "value", [Decimal("-1E-11"), Decimal("NaN"), Decimal("-Infinity"), Decimal("Infinity")]
)
def test_stored_cost_refuses_negative_and_non_finite(value: Decimal) -> None:
    with pytest.raises(ValueError):
        stored_cost(value)


class _Priced(BaseModel):
    cost: Money
    budget: Money | None = None


def test_money_serializes_as_plain_decimal() -> None:
    dumped = _Priced(cost=Decimal("5.000E-7"), budget=Decimal("0E-10")).model_dump(mode="json")
    assert dumped == {"cost": "0.0000005", "budget": "0"}
    assert _Priced(cost=Decimal("2.0000000000")).model_dump_json() == '{"cost":"2","budget":null}'
