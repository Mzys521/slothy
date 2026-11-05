"""模型可见参数类型。 / Model-visible argument schemas."""

from typing import Literal

from pydantic import BaseModel, Field, StrictStr


class FloatCalculationArgs(BaseModel):
    """普通浮点计算参数。 / General floating-point calculation arguments."""

    operation: Literal["add", "subtract", "multiply", "divide", "power", "sqrt"] = Field(
        description="运算类型；金额不要使用此工具 / Operation; never use this tool for money"
    )
    a: float = Field(
        strict=True, allow_inf_nan=False, description="有限浮点操作数 / Finite float operand"
    )
    b: float | None = Field(
        default=None,
        strict=True,
        allow_inf_nan=False,
        description="二元运算第二个操作数，sqrt 不传 / Second float operand; omit for sqrt",
    )


class FinancialCalculationArgs(BaseModel):
    """金额严格使用字符串。 / Financial operands are strict strings."""

    operation: Literal["add", "subtract", "multiply", "divide"] = Field(
        description="金额运算类型 / Financial arithmetic operation"
    )
    a: StrictStr = Field(
        min_length=1, max_length=128, description='十进制字符串，如 "0.10" / Decimal string'
    )
    b: StrictStr = Field(
        min_length=1, max_length=128, description="第二个十进制字符串 / Second decimal string"
    )
    decimal_places: int = Field(
        default=2, strict=True, ge=0, le=28, description="结果小数位数 / Output decimal places"
    )
    rounding: Literal["half_up", "half_even"] = Field(
        default="half_up", description="四舍五入或半偶数舍入 / Half-up or half-even rounding"
    )


class CommandArgs(BaseModel):
    """结构化命令参数，不接受任意 Shell。 / Structured commands, never arbitrary shell text."""

    command: Literal["pwd", "ls", "cat", "mkdir", "echo", "rg"] = Field(
        description="命令名称；无删除命令 / Command name; no deletion command"
    )
    arguments: list[StrictStr] = Field(
        default_factory=list,
        max_length=20,
        description="字面参数；rg: pattern, 可选 path / Literal argv; rg: pattern, optional path",
    )
    cwd: str = Field(
        default=".",
        min_length=1,
        max_length=4096,
        description="工具工作区内的目录 / Directory inside the tool workspace",
    )


class PythonDocumentArgs(BaseModel):
    """Python 文档生成参数。 / Python document-generation arguments."""

    code: str = Field(
        min_length=1,
        max_length=100000,
        description="Python 文档代码；相对路径输出，input_dir 只读；金额用 Decimal 字符串 / "
        "Python code; relative outputs; read-only input_dir; Decimal strings for money",
    )
