"""Floating-point arithmetic and string-based money arithmetic. / 浮点计算与字符串金额计算。"""

import math
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, DecimalException, localcontext
from typing import Literal


def calculate(
    operation: Literal["add", "subtract", "multiply", "divide", "power", "sqrt"],
    a: float,
    b: float | None = None,
) -> float:
    """
    Perform bounded floating-point arithmetic and return a float. / 执行有界浮点运算并返回
    float。

    Args:
        operation: Add, subtract, multiply, divide, power or square root. /
        加、减、乘、除、幂或平方根。
        a: Finite floating-point first operand. / 有限浮点第一操作数。
        b: Second finite operand for binary operations; omit it for sqrt. /
        二元运算的第二个有限浮点数；sqrt 不传。

    Returns:
        Finite floating-point result; never used for money. / 有限浮点结果，不用于金额。

    Raises:
        ValueError: If operands, operation or result are invalid or not finite. /
            操作数、运算或结果无效或非有限时抛出。
    """
    if type(a) not in (int, float) or not math.isfinite(a):
        raise ValueError("a 必须是有限浮点数 / must be a finite float")
    a = float(a)
    if operation == "sqrt":
        if b is not None or a < 0:
            raise ValueError("sqrt 只接受一个非负数 / requires one nonnegative operand")
        return math.sqrt(a)
    if type(b) not in (int, float) or not math.isfinite(b):
        raise ValueError("b 必须是有限浮点数 / must be a finite float")
    b = float(b)
    try:
        match operation:
            case "add":
                result = a + b
            case "subtract":
                result = a - b
            case "multiply":
                result = a * b
            case "divide":
                if b == 0:
                    raise ValueError("除数不能为零 / division by zero")
                result = a / b
            case "power":
                result = math.pow(a, b)
            case _:
                raise ValueError("不支持的运算 / unsupported operation")
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("计算超出浮点范围 / floating-point range exceeded") from exc
    if not math.isfinite(result):
        raise ValueError("计算结果必须有限 / result must be finite")
    return float(result)


def calculate_financial(
    operation: Literal["add", "subtract", "multiply", "divide"],
    a: str,
    b: str,
    decimal_places: int = 2,
    rounding: Literal["half_up", "half_even"] = "half_up",
) -> str:
    """
    Perform exact decimal money arithmetic on string operands. /
    对字符串操作数执行精确十进制金额运算。

    Args:
        operation: Add, subtract, multiply or divide. / 加、减、乘、除。
        a: Decimal string such as 0.10; never a float. / 如 0.10 的十进制字符串，禁止 float。
        b: Second decimal string such as 0.20. / 第二个十进制字符串，例如 0.20。
        decimal_places: Output decimal places from 0 to 28, default 2. / 结果小数位数
        0～28，默认 2。
        rounding: half_up or half_even final-result rounding. / 结果使用 half_up 或 half_even
        舍入。

    Returns:
        Rounded decimal string with the requested scale. / 按请求精度舍入后的十进制字符串。

    Raises:
        ValueError: If an operand, bound, operation or rounding mode is invalid. /
            操作数、边界、运算或舍入方式无效时抛出。
    """
    if not isinstance(a, str) or not isinstance(b, str):
        raise ValueError("金额必须使用字符串 / financial operands must be strings")
    if type(decimal_places) is not int or not 0 <= decimal_places <= 28:
        raise ValueError("小数位数必须为 0～28 / decimal_places must be from 0 to 28")
    if rounding not in ("half_up", "half_even"):
        raise ValueError("不支持的舍入方式 / unsupported rounding mode")
    try:
        operands = []
        for value in (a, b):
            if not value.strip() or len(value) > 128:
                raise ValueError("金额字符串为空或过长 / financial string is empty or too long")
            number = Decimal(value.strip())
            if (
                not number.is_finite()
                or len(number.as_tuple().digits) > 80
                or abs(number.as_tuple().exponent) > 80
            ):
                raise ValueError("金额超出支持范围 / financial value is outside supported bounds")
            operands.append(number)
        left, right = operands
        # English: Keep bounded intermediate values precise and round only the final result.
        # 中文：足够容纳受限输入的中间结果，只在最后按明确规则舍入。
        with localcontext() as context:
            context.prec = 512
            context.rounding = ROUND_HALF_UP if rounding == "half_up" else ROUND_HALF_EVEN
            match operation:
                case "add":
                    result = left + right
                case "subtract":
                    result = left - right
                case "multiply":
                    result = left * right
                case "divide":
                    if right == 0:
                        raise ValueError("除数不能为零 / division by zero")
                    result = left / right
                case _:
                    raise ValueError("不支持的金额运算 / unsupported financial operation")
            result = result.quantize(Decimal(1).scaleb(-decimal_places))
            if result.is_zero():
                result = abs(result)
            return format(result, f".{decimal_places}f")
    except DecimalException as exc:
        raise ValueError("无效十进制金额 / invalid decimal financial value") from exc
