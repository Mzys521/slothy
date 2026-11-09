"""Model-visible argument schemas. / 模型可见参数类型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class FloatCalculationArgs(BaseModel):
    """General floating-point calculation arguments. / 普通浮点计算参数。"""

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
    """Financial operands are strict strings. / 金额严格使用字符串。"""

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
    """Structured commands, never arbitrary shell text. / 结构化命令参数，不接受任意 Shell。"""

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
    """Python document-generation arguments. / Python 文档生成参数。"""

    code: str = Field(
        min_length=1,
        max_length=100000,
        description="Python 文档代码；相对路径输出，input_dir 只读；金额用 Decimal 字符串 / "
        "Python code; relative outputs; read-only input_dir; Decimal strings for money",
    )


class SearchArgs(BaseModel):
    """DeepSeek web-search request arguments. / DeepSeek 网页搜索参数。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=128)
    count: int = Field(default=5, ge=1, le=10)
    ori_question: str | None = Field(default=None, min_length=1, max_length=128)
    need_content: bool = True


class WorkspaceDeleteArgs(BaseModel):
    """Delete one workspace file or directory after approval. / 审批后删除工作区文件或目录。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    recursive: bool = Field(
        default=False,
        description="Allow deleting a non-empty directory; high risk. / 允许删除非空目录；高风险。",
    )


class WorkspaceReadArgs(BaseModel):
    """
    List, paged-read or literal-search arguments for workspace files. /
    工作区文件列出、分页读取与字面搜索参数。
    """

    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "read", "search"]
    path: str = Field(default=".", min_length=1, max_length=4096)
    depth: int = Field(default=1, ge=1, le=4)
    offset: int = Field(default=0, ge=0, le=10_000_000)
    limit: int = Field(
        default=8000,
        ge=1,
        le=8000,
        description="Text characters; for search use at most 50 matches.",
    )
    pattern: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="Literal text, never a regular expression.",
    )

    @model_validator(mode="before")
    @classmethod
    def search_default(cls, value):
        """
        Default the search limit so literal searches stay bounded. /
        为搜索补默认上限，使字面搜索保持有界。

        Args:
            value: Raw argument mapping being validated. / 正在校验的原始参数映射。

        Returns:
            Mapping with a default search limit when omitted. / 省略时补上默认搜索上限的映射。
        """
        if isinstance(value, dict) and value.get("action") == "search" and "limit" not in value:
            return {**value, "limit": 50}
        return value

    @model_validator(mode="after")
    def search_parameters(self):
        """
        Require a fixed-string pattern and small limit for search. /
        搜索必须提供固定字符串模式与较小上限。

        Returns:
            The validated instance. / 校验通过的实例。

        Raises:
            ValueError: If search lacks a pattern or exceeds 50 matches. / 搜索缺少模式或超过
            50 个匹配时抛出。
        """
        if self.action == "search" and (not self.pattern or self.limit > 50):
            raise ValueError("Search requires a fixed-string pattern and limit <= 50")
        return self


class WorkspaceWriteArgs(BaseModel):
    """
    Create directories or write UTF-8 files inside the workspace. / 在工作区内创建目录或写入
    UTF-8 文件。
    """

    model_config = ConfigDict(extra="forbid")
    action: Literal["mkdir", "write_file"]
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(default="", max_length=1_000_000)
    overwrite: bool = Field(default=False, strict=True)


class DocumentTable(BaseModel):
    """Rectangular table with bounded columns and rows. / 行列有界的矩形表格。"""

    model_config = ConfigDict(extra="forbid")
    columns: list[StrictStr] = Field(min_length=1, max_length=20)
    rows: list[list[StrictStr]] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def rectangular(self):
        """Require every row to match the column count. / 要求每行与列数一致。

        Returns:
            The validated instance. / 校验通过的实例。

        Raises:
            ValueError: If any row length differs from the columns. / 任一行长度与列数不一致时抛出。
        """
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Every table row must match the columns")
        return self


class DocumentSection(BaseModel):
    """One document section with heading, paragraphs, items and an optional table. /
    含标题、段落、条目与可选表格的文档章节。"""

    model_config = ConfigDict(extra="forbid")
    heading: str = Field(default="", max_length=200)
    paragraphs: list[StrictStr] = Field(default_factory=list, max_length=100)
    items: list[StrictStr] = Field(default_factory=list, max_length=100)
    table: DocumentTable | None = None


class ExportDocumentArgs(BaseModel):
    """Structured arguments for exporting md, txt, csv, json or docx files. /
    导出 md、txt、csv、json 或 docx 的结构化参数。"""

    model_config = ConfigDict(extra="forbid")
    format: Literal["md", "txt", "csv", "json", "docx"]
    filename: str = Field(
        min_length=1,
        max_length=180,
        description="A plain filename with the chosen format extension.",
    )
    title: str = Field(default="", max_length=200)
    sections: list[DocumentSection] = Field(default_factory=list, max_length=50)


class DatetimeArgs(BaseModel):
    """Arguments for current-time lookup or timezone conversion. / 当前时间查询或时区换算参数。"""

    model_config = ConfigDict(extra="forbid")
    timezone: str = Field(
        default="local",
        min_length=1,
        max_length=100,
        description="IANA timezone such as Asia/Shanghai, or local.",
    )
    format: str = Field(default="iso", min_length=1, max_length=120)
    datetime_iso: str | None = Field(
        default=None,
        max_length=100,
        description="Optional ISO time including a UTC offset to convert.",
    )


class WebFetchArgs(BaseModel):
    """Arguments for allowlisted HTTPS text retrieval. / 允许列表 HTTPS 文本抓取参数。"""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096)
    max_bytes: int = Field(default=100_000, ge=1, le=10_000_000)
