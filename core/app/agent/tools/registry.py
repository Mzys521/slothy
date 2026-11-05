"""只负责 SDK 适配与注册。 / SDK adaptation and registration only."""

from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool, ToolException

from app.agent.tools.schemas import (
    CommandArgs,
    FinancialCalculationArgs,
    FloatCalculationArgs,
    PythonDocumentArgs,
)
from app.config import ToolSettings
from app.tools.calculator import calculate as float_calculate
from app.tools.calculator import calculate_financial as financial_calculate
from app.tools.command import CommandExecutor
from app.tools.python_document import PythonDocumentExecutor


def calculate(
    operation: Literal["add", "subtract", "multiply", "divide", "power", "sqrt"],
    a: float,
    b: float | None = None,
) -> float:
    """operation：运算；a/b：有限浮点数。 / Operation and finite float operands, never for money."""
    try:
        return float_calculate(operation, a, b)
    except (ValueError, ArithmeticError) as exc:
        raise ToolException(str(exc)) from exc


def calculate_financial(
    operation: Literal["add", "subtract", "multiply", "divide"],
    a: str,
    b: str,
    decimal_places: int = 2,
    rounding: Literal["half_up", "half_even"] = "half_up",
) -> str:
    """operation：运算；a/b：金额字符串；decimal_places/rounding：结果规则。

    English: Operation, string operands, output decimal places and rounding policy.
    """
    try:
        return financial_calculate(operation, a, b, decimal_places, rounding)
    except (ValueError, ArithmeticError) as exc:
        raise ToolException(str(exc)) from exc


def create_builtin_tools(settings: ToolSettings | None = None) -> list[BaseTool]:
    """创建可复用工具列表，由应用注入 Graph。 / Create reusable tools for application injection.

    Args:
        settings: 工具目录与预算，省略时读环境。 / Tool settings; defaults to environment.
    """
    resolved = settings if settings is not None else ToolSettings()
    commands, documents = CommandExecutor(resolved), PythonDocumentExecutor(resolved)
    return [
        StructuredTool.from_function(
            calculate,
            name="calculate",
            args_schema=FloatCalculationArgs,
            description="普通加减乘除、幂和平方根，参数与返回值为 float。"
            "金额、记账使用 calculate_financial。 / General float arithmetic; money uses "
            "calculate_financial.",
            handle_tool_error=True,
            handle_validation_error="请提供有限浮点参数 / Provide finite numeric float arguments",
        ),
        StructuredTool.from_function(
            calculate_financial,
            name="calculate_financial",
            args_schema=FinancialCalculationArgs,
            description="金融/记账计算。a、b 必须是十进制字符串，返回定点字符串；"
            "禁止将金额传为 float。 / Financial arithmetic with strict string inputs and output.",
            handle_tool_error=True,
            handle_validation_error="金额用字符串，小数位 0～28 / String amounts; 0-28 places",
        ),
        StructuredTool.from_function(
            coroutine=commands.run,
            name="run_command",
            args_schema=CommandArgs,
            description="工作区内的 pwd/ls/cat/mkdir/echo 与只读 rg；参数是字面列表，"
            "不能删除或运行 Shell/解释器。 / Workspace commands; no deletion or shells.",
            handle_validation_error="仅支持白名单命令 / Use pwd/ls/cat/mkdir/echo/rg",
        ),
        StructuredTool.from_function(
            coroutine=documents.run,
            name="write_document_python",
            args_schema=PythonDocumentArgs,
            description="直接编写 Python 生成 Word/文本/CSV/JSON 等文档，默认支持 docx；"
            "openpyxl/reportlab/pptx 需可选 documents 依赖。相对路径写本次产物目录，"
            "workspace 是输出目录，input_dir 是只读输入目录。禁止删除、网络和进程调用。 / "
            "Execute local document Python code in a new output directory; returns artifact paths. "
            f"代码上限 / Source cap: {resolved.max_code_chars} characters; "
            f"执行时限 / Timeout: {resolved.python_timeout_seconds:g}s.",
            handle_validation_error="请提供限长非空源码 / Provide bounded nonempty Python code",
        ),
    ]
