"""Tool metadata, without executors or changes to the model-visible contract. /
工具元数据，不包含执行器，也不改变模型可见契约。"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import ToolSettings
from app.tools.contracts import ResultKind

ToolMode = Literal["chat", "work"]


class Risk(StrEnum):
    """Risk class that drives approval and mode policy. / 驱动审批与模式策略的风险等级。"""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


class Platform(StrEnum):
    """Platform where a tool may be exposed. / 工具可被暴露的平台。"""

    DESKTOP = "desktop"
    SERVER = "server"


class ToolDescriptor(BaseModel):
    """The tool's schema, availability, review policy and execution limits. /
    工具的 schema、可用性、审批策略与执行限制。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str
    args_schema: type[BaseModel] | dict[str, Any] | None = None
    risk: Risk
    requires_approval: bool
    platforms: frozenset[Platform] = Field(default=frozenset({Platform.DESKTOP}), min_length=1)
    mode: frozenset[ToolMode] = Field(default=frozenset({"work"}), min_length=1)
    instruction: str = ""
    timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_output_chars: int | None = Field(default=None, gt=0)
    idempotent: bool = True
    source: Literal["builtin", "connector", "skill"] = "builtin"
    version: str = Field(default="1", min_length=1)
    enabled: bool = True
    deprecated: bool = False
    alias_for: str | None = None
    result_kind: ResultKind = "structured"
    journaled: bool = False
    approval_argument: str | None = None
    argument_risks: dict[str, Risk] = Field(default_factory=dict)
    replacements: frozenset[str] = frozenset()
    argument_targets: dict[str, str] = Field(default_factory=dict)

    def target_for(self, arguments: dict) -> str | None:
        """Resolve the action target implied by one call's arguments.
        / 解析单次调用参数所隐含的操作目标。

        Args:
            arguments: Model-supplied tool arguments.
                / 模型提供的工具参数。

        Returns:
            Configured argument target, alias, or None when no target applies.
            / 配置的参数目标、别名；不适用时为 None。
        """
        value = arguments.get(self.approval_argument) if self.approval_argument else None
        return (
            self.argument_targets.get(value, self.alias_for)
            if isinstance(value, str)
            else self.alias_for
        )

    def risk_for(self, arguments: dict) -> Risk:
        """Resolve the effective risk for one call's arguments. / 解析单次调用参数对应的实际风险。

        Args:
            arguments: Model-supplied tool arguments. / 模型提供的工具参数。

        Returns:
            Argument-specific risk when configured, otherwise the base risk. /
            配置了参数风险时返回该风险，否则返回基础风险。
        """
        value = arguments.get(self.approval_argument) if self.approval_argument else None
        return self.argument_risks.get(value, self.risk) if isinstance(value, str) else self.risk

    def approval_for(self, arguments: dict) -> bool:
        """Decide whether one call needs explicit approval. / 判断单次调用是否需要显式审批。

        Args:
            arguments: Model-supplied tool arguments. / 模型提供的工具参数。

        Returns:
            True when the resolved risk is not read-only. / 实际风险不是只读时返回 True。
        """
        value = arguments.get(self.approval_argument) if self.approval_argument else None
        if isinstance(value, str) and value in self.argument_risks:
            return self.risk_for(arguments) != Risk.READ
        return self.requires_approval

    @model_validator(mode="before")
    @classmethod
    def default_approval(cls, value):
        """Default approval from risk when metadata omits it. / 元数据未提供审批时按风险推导默认值。

        Args:
            value: Raw descriptor input, usually a mapping. / 原始描述符输入，通常为映射。

        Returns:
            Mapping with requires_approval filled in for non-read risks. /
            为非只读风险补全 requires_approval 的映射。
        """
        if isinstance(value, dict) and "requires_approval" not in value:
            return {**value, "requires_approval": value.get("risk") != Risk.READ}
        return value

    @classmethod
    def from_tool(cls, tool: BaseTool, **metadata) -> "ToolDescriptor":
        """Describe an SDK tool without rebuilding its schema. / 描述 SDK 工具且不重建其 schema。

        Args:
            tool: LangChain tool supplying name, description and args_schema. /
                提供名称、描述与 args_schema 的 LangChain 工具。
            metadata: Preset fields such as risk, mode and instruction. /
                risk、mode、instruction 等预设描述符字段。

        Returns:
            Descriptor mirroring the tool's existing model-visible contract. /
            与工具现有模型可见契约一致的描述符。
        """
        # English: Preserve inferred/injected schemas rather than deriving new SDK signatures.
        # 中文：保留推断或注入的 schema，而不是重新推导 SDK 签名。
        return cls(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            **metadata,
        )


class DescribedTool(StructuredTool):
    """Metadata travels with SDK tools without entering their schema or callback payload. /
    元数据随 SDK 工具传递，但不进入 schema 或回调负载。"""

    descriptor: ToolDescriptor = Field(exclude=True)
    execution_settings: ToolSettings | None = Field(default=None, exclude=True)


def describe_builtin_tool(tool: BaseTool, settings: ToolSettings | None = None) -> ToolDescriptor:
    """Use carried metadata; unknown caller-injected tools become reviewed work tools. /
    使用随工具携带的元数据；未知的调用方工具默认视为需审批的 work 工具。

    Args:
        tool: Built-in or caller-injected LangChain tool. / 内置或调用方注入的 LangChain 工具。
        settings: Optional tool limits retained for legacy callers. / 为旧调用方保留的可选工具限制。

    Returns:
        Descriptor with risk, mode, result kind and optional execution limits. /
        含风险、模式、结果类型与可选执行限制的描述符。
    """
    if isinstance(tool, DescribedTool):
        return tool.descriptor
    return ToolDescriptor.from_tool(tool, risk=Risk.EXECUTE, idempotent=False)


def render_tool_instructions(instructions: Mapping[str, str]) -> str:
    """
    Render sorted per-tool guidance for the system prompt. /
    为系统提示词渲染按名称排序的工具指引。

    Args:
        instructions: Tool name to model guidance mapping. / 工具名称到模型指引的映射。

    Returns:
        Prompt suffix starting with a newline, or an empty string. /
        以换行开头提示词后缀，或空字符串。
    """
    names = sorted(instructions)
    if not names:
        return ""
    return "\n" + "\n".join(
        [
            "Tools available in this conversation: " + ", ".join(names) + ".",
            "Use the relevant tool when the user requests an operation it supports. "
            "Describe only the actual available capabilities and results.",
            *(instructions[name] for name in names if instructions[name]),
        ]
    )
