"""Serializable tool results shared by executors, the agent and HTTP projections. /
供执行器、Agent 与 HTTP 投影共用的可序列化工具结果。"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, model_validator


class ToolError(BaseModel):
    """Structured, model-safe tool failure details. / 结构化且对模型安全的工具失败信息。

    Contains a stable code, a bounded message, a retry flag and an optional hint. /
    包含稳定错误码、有界消息、重试标记与可选提示。
    """

    code: str
    message: str
    retryable: bool = False
    hint: str | None = None


class Artifact(BaseModel):
    """Reference to one file produced by a tool invocation. / 工具调用产生的单个文件引用。"""

    id: str
    name: str
    bytes: int = Field(ge=0)
    rel_path: str
    sha256: str | None = None
    url: str | None = None


class ToolResult(BaseModel):
    """
    Success or failure envelope returned by every tool execution. /
    每次工具执行返回的成功或失败信封。

    The ok flag and the error object are mutually exclusive and validated together. /
    ok 标志与 error 对象互斥并一起校验。
    """

    ok: bool
    summary: str
    data: dict[str, JsonValue] | list[JsonValue] | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    next_offset: int | None = Field(default=None, ge=0)
    error: ToolError | None = None

    @model_validator(mode="after")
    def consistent_error(self):
        """
        Reject envelopes whose ok flag and error disagree. / 拒绝 ok 标志与 error
        不一致的信封。

        Returns:
            The validated result instance. / 校验通过的结果实例。

        Raises:
            ValueError: If ok is true with an error, or false without one. / ok
            为真却有错误，或为假却无错误时抛出。
        """
        if self.ok == (self.error is not None):
            raise ValueError("Successful results have no error; failed results require an error")
        return self

    def wire(self) -> str:
        """
        Serialize this result for a model-visible tool message. /
        将结果序列化为模型可见的工具消息。

        Returns:
            JSON text with None fields omitted. / 省略 None 字段的 JSON 文本。
        """
        return self.model_dump_json(exclude_none=True)


def failure(code: str, message: str, *, retryable=False, hint=None) -> ToolResult:
    """Create a failed envelope with structured error details. / 创建带结构化错误详情的失败信封。

    Args:
        code: Stable machine-readable error code. / 稳定的机器可读错误码。
        message: Bounded model-safe failure message. / 对模型安全的有界失败消息。
        retryable: Whether the same call may succeed later. / 同一调用之后是否可能成功。
        hint: Optional remedy shown to the model. / 可选、展示给模型的修复提示。

    Returns:
        Failed ToolResult carrying the error. / 携带该错误的失败 ToolResult。
    """
    return ToolResult(
        ok=False,
        summary=message,
        error=ToolError(code=code, message=message, retryable=retryable, hint=hint),
    )


class ToolOperationError(ValueError):
    """
    ValueError that carries a ready-made failed tool result. / 携带现成失败工具结果的
    ValueError。
    """

    def __init__(self, code: str, message: str, *, retryable=False, hint=None):
        """Build the exception and its failed result envelope. / 构造异常及其失败结果信封。

        Args:
            code: Stable machine-readable error code. / 稳定的机器可读错误码。
            message: Bounded model-safe failure message. / 对模型安全的有界失败消息。
            retryable: Whether the same call may succeed later. / 同一调用之后是否可能成功。
            hint: Optional remedy shown to the model. / 可选、展示给模型的修复提示。
        """
        super().__init__(message)
        self.result = failure(code, message, retryable=retryable, hint=hint)


class ToolMetric(BaseModel):
    """Per-tool counters aggregated for one run. / 单次运行按工具聚合的计数器。"""

    name: str
    calls: int = 0
    ok: int = 0
    errors: int = 0
    rejected: int = 0
    approval_wait_ms: int = 0
    duration_ms: int = 0
    output_bytes: int = 0
    output_chars: int = 0
    artifacts: int = 0
    timed_out: int = 0
    reused: int = 0


def artifact_for(path: Path, root: Path, key: str) -> Artifact:
    """
    Describe an existing file as a tool artifact. / 将现有文件描述为工具产物。

    Args:
        path: Existing artifact file inside the workspace. / 工作区内已存在的产物文件。
        root: Workspace root that bounds artifact paths. / 限定产物路径的工作区根目录。
        key: Run or invocation identity mixed into the artifact id. / 混入产物 id
        的运行或调用标识。

    Returns:
        Artifact metadata with relative path and SHA-256 digest. / 含相对路径与 SHA-256
        摘要的产物元数据。

    Raises:
        ValueError: If the path is outside the workspace root. /
        路径在工作区根目录之外时抛出。
    """
    import hashlib

    relative = path.relative_to(root).as_posix()
    return Artifact(
        id=hashlib.sha256(f"{key}:{relative}".encode()).hexdigest(),
        name=path.name,
        bytes=path.stat().st_size,
        rel_path=relative,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


ResultKind = Literal[
    "structured",
    "value",
    "decimal",
    "command",
    "artifacts",
    "items",
    "page",
    "envelope",
    "connector",
]
