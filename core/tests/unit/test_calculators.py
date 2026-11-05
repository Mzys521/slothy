"""计算类型与精度约束。 / Calculation type and precision contracts."""

import math

import pytest
from pydantic import ValidationError

from app.agent.tools.schemas import FinancialCalculationArgs, FloatCalculationArgs
from app.tools.calculator import calculate, calculate_financial


@pytest.mark.parametrize(
    ("operation", "a", "b", "expected"),
    [
        ("add", 1.5, 2.25, 3.75),
        ("subtract", 3.5, 1.0, 2.5),
        ("multiply", 2.5, 2.0, 5.0),
        ("divide", 5.0, 2.0, 2.5),
        ("power", 2.0, 3.0, 8.0),
        ("sqrt", 9.0, None, 3.0),
    ],
)
def test_general_arithmetic_returns_float(operation, a, b, expected):
    result = calculate(operation, a, b)
    assert type(result) is float and result == expected


def test_general_arithmetic_keeps_binary_float_semantics():
    assert calculate("add", 0.1, 0.2) == 0.1 + 0.2
    assert calculate("add", 0.1, 0.2) != 0.3


@pytest.mark.parametrize(
    ("operation", "a", "b"),
    [
        ("divide", 1.0, 0.0),
        ("sqrt", -1.0, None),
        ("sqrt", 4.0, 2.0),
        ("add", math.inf, 1.0),
        ("add", True, 1.0),
        ("power", 1e308, 10.0),
        ("multiply", 1e308, 10.0),
    ],
)
def test_invalid_float_operations_are_rejected(operation, a, b):
    with pytest.raises((ValueError, OverflowError)):
        calculate(operation, a, b)


def test_float_schema_rejects_string_money_and_boolean_operands():
    for value in ("0.1", True):
        with pytest.raises(ValidationError):
            FloatCalculationArgs(operation="add", a=value, b=0.2)


@pytest.mark.parametrize(
    ("operation", "a", "b", "expected"),
    [
        ("add", "0.10", "0.20", "0.30"),
        ("subtract", "100.00", "0.01", "99.99"),
        ("multiply", "19.99", "3", "59.97"),
        ("divide", "10.00", "3", "3.33"),
        ("subtract", "0.001", "0.002", "0.00"),
        ("add", "9007199254740993.01", "0.01", "9007199254740993.02"),
    ],
)
def test_financial_arithmetic_preserves_string_precision(operation, a, b, expected):
    result = calculate_financial(operation, a, b)
    assert type(result) is str and result == expected


def test_financial_rounding_is_explicit_and_applied_at_the_end():
    assert calculate_financial("add", "2.345", "0", rounding="half_up") == "2.35"
    assert calculate_financial("add", "2.345", "0", rounding="half_even") == "2.34"
    assert calculate_financial("multiply", "0.105", "3") == "0.32"
    assert calculate_financial("divide", "1", "8", decimal_places=4) == "0.1250"


def test_financial_precision_covers_the_largest_accepted_multiplication():
    operand = "9" * 80 + "e80"
    expected = str(int("9" * 80) ** 2) + "0" * 160 + ".00"
    assert calculate_financial("multiply", operand, operand) == expected


@pytest.mark.parametrize("value", [0.1, 1, True, "NaN", "Infinity", "1e999999999", "", "x"])
def test_invalid_financial_inputs_are_not_coerced(value):
    with pytest.raises(ValueError):
        calculate_financial("add", value, "0.2")


def test_financial_schema_rejects_numeric_operands_and_boolean_places():
    with pytest.raises(ValidationError):
        FinancialCalculationArgs(operation="add", a=0.1, b="0.2")
    with pytest.raises(ValidationError):
        FinancialCalculationArgs(operation="add", a="0.1", b="0.2", decimal_places=True)
