"""
SDK adapters and a shared catalog for built-in and connector tools. / 内置与连接器工具的 SDK
适配器及共享目录。
"""

from collections.abc import Iterable
from typing import Literal, Protocol

from langchain_core.tools import BaseTool, ToolException

from app.agent.tools.descriptor import (
    DescribedTool,
    Platform,
    ToolDescriptor,
    ToolMode,
    describe_builtin_tool,
    render_tool_instructions,
)
from app.config import ToolSettings
from app.tools.calculator import calculate as float_calculate
from app.tools.calculator import calculate_financial as financial_calculate


class ConnectorAdapter(Protocol):
    """Source of connector tools for the registry. / 目录的连接器工具来源。"""

    def tool_entries(self, directory: str | None) -> list[tuple[ToolDescriptor, BaseTool]]:
        """
        Return described tools for the current connector directory. /
        返回当前连接器目录的带描述工具。

        Args:
            directory: Optional per-run connector directory. / 可选的本次运行连接器目录。

        Returns:
            Descriptor and SDK tool pairs supplied by the adapter. / 适配器提供的描述符与 SDK
            工具对。
        """
        ...


class ToolRegistry:
    """Per-assembly metadata and tools; no credentials, execution state or I/O. /
    每次装配的元数据与工具；不含凭据、执行状态或 I/O。"""

    def __init__(self) -> None:
        """Create an empty registry with desktop defaults. / 创建带桌面默认值的空目录。"""
        self._entries: dict[str, tuple[ToolDescriptor, BaseTool]] = {}
        self._disabled_builtin: frozenset[str] = frozenset()
        self._platform = Platform.DESKTOP
        self.settings: ToolSettings | None = None

    @classmethod
    def from_tools(
        cls, tools: Iterable[BaseTool], *, settings: ToolSettings | None = None
    ) -> "ToolRegistry":
        """
        Build a registry from SDK tools, preserving their existing schemas. /
        从 SDK 工具构建目录并保留其现有 schema。

        Args:
            tools: Built-in or caller-injected SDK tools. / 内置或调用方注入的 SDK 工具。
            settings: Optional tool limits used for descriptor metadata. /
            用于描述符元数据的可选工具限制。

        Returns:
            Registry containing one descriptor per tool. / 每个工具一个描述符的目录。
        """
        registry = cls()
        registry.settings = settings
        for tool in tools:
            if registry.settings is None and isinstance(tool, DescribedTool):
                registry.settings = tool.execution_settings
            registry.register(describe_builtin_tool(tool, settings), tool)
        return registry

    def register(self, descriptor: ToolDescriptor, tool: BaseTool) -> None:
        """
        Register one descriptor and implementation pair. / 注册一个描述符与实现对。

        Args:
            descriptor: Metadata that must match the tool's model-visible contract. /
                必须与工具模型可见契约一致的元数据。
            tool: SDK tool implementation. / SDK 工具实现。

        Raises:
            ValueError: If names differ or the name is already registered. /
            名称不一致或已注册时抛出。
        """
        if descriptor.name != tool.name:
            raise ValueError("Tool descriptor and implementation names must match")
        if descriptor.name in self._entries:
            raise ValueError("Tool names must be unique")
        self._entries[descriptor.name] = (descriptor, tool)

    def register_connector(self, adapter: ConnectorAdapter, directory: str | None = None) -> None:
        """
        Register all tools supplied by one connector adapter. /
        注册一个连接器适配器提供的全部工具。

        Args:
            adapter: Connector source of descriptor and tool pairs. /
            提供描述符与工具对的连接器来源。
            directory: Optional per-run connector directory. / 可选的本次运行连接器目录。

        Raises:
            ValueError: If names are duplicated or descriptor names differ. /
            名称重复或描述符名称不一致时抛出。
        """
        entries = adapter.tool_entries(directory)
        names = [descriptor.name for descriptor, _ in entries]
        if len(set(names)) != len(names) or self._entries.keys() & set(names):
            raise ValueError("Tool names must be unique")
        if any(descriptor.name != tool.name for descriptor, tool in entries):
            raise ValueError("Tool descriptor and implementation names must match")
        for descriptor, tool in entries:
            self.register(descriptor, tool)

    def rebuild(
        self,
        *,
        disabled_builtin: Iterable[str] = (),
        platform: Platform = Platform.DESKTOP,
    ) -> None:
        """Record disabled built-ins and the target platform for availability. /
        记录禁用内置工具与目标平台以计算可用性。

        Args:
            disabled_builtin: Built-in tool names disabled by the user. / 用户禁用的内置工具名称。
            platform: Platform whose availability rules apply. / 应用可用性规则的平台。
        """
        disabled = set(disabled_builtin)
        for name in tuple(disabled):
            descriptor = self.descriptor(name)
            if descriptor and descriptor.deprecated:
                disabled.update(descriptor.replacements or {descriptor.alias_for})
        self._disabled_builtin = frozenset(disabled)
        self._platform = Platform(platform)

    def _available(self, descriptor: ToolDescriptor, mode: ToolMode) -> bool:
        """Report whether a descriptor is enabled, current and allowed in this mode. /
        判断描述符是否启用、有效且允许用于该模式。

        Args:
            descriptor: Tool metadata to check. / 待检查的工具元数据。
            mode: Chat or work mode request. / 请求的 chat 或 work 模式。

        Returns:
            True when the tool may be exposed in this mode. / 该模式可暴露该工具时返回 True。
        """
        return (
            descriptor.enabled
            and not descriptor.deprecated
            and mode in descriptor.mode
            and self._platform in descriptor.platforms
            and not (descriptor.source == "builtin" and descriptor.name in self._disabled_builtin)
        )

    def for_mode(self, mode: ToolMode) -> list[BaseTool]:
        """Return available SDK tools in registration order. / 按注册顺序返回可用的 SDK 工具。

        Args:
            mode: Chat or work mode request. / 请求的 chat 或 work 模式。

        Returns:
            Available tools, without sensitive or deprecated entries. / 不含敏感或弃用项的可用工具。
        """
        # English: Public runtime arrays historically use registration order.
        # 中文：公开运行时数组沿用历史注册顺序。
        return [
            tool for descriptor, tool in self._entries.values() if self._available(descriptor, mode)
        ]

    def descriptors(self, mode: ToolMode) -> list[ToolDescriptor]:
        """Return available descriptors sorted by tool name. / 按工具名称排序返回可用描述符。

        Args:
            mode: Chat or work mode request. / 请求的 chat 或 work 模式。

        Returns:
            Descriptors for tools available in this mode. / 该模式下可用工具的描述符。
        """
        return sorted(
            (item for item, _ in self._entries.values() if self._available(item, mode)),
            key=lambda item: item.name,
        )

    def descriptor(self, name: str) -> ToolDescriptor | None:
        """Look up one registered descriptor by name. / 按名称查找已注册描述符。

        Args:
            name: Model-visible tool name. / 模型可见工具名称。

        Returns:
            Matching descriptor, or None when unregistered. / 匹配描述符，未注册时为 None。
        """
        entry = self._entries.get(name)
        return entry[0] if entry else None

    def tool(self, name: str) -> BaseTool | None:
        """
        Return the registered SDK adapter without executing it. / 读取已注册的 SDK
        适配器，不执行。
        """
        entry = self._entries.get(name)
        return entry[1] if entry else None

    def names(self, mode: ToolMode) -> list[str]:
        """Return available tool names in registration order. / 按注册顺序返回可用工具名称。

        Args:
            mode: Chat or work mode request. / 请求的 chat 或 work 模式。

        Returns:
            Model-visible names of tools available in this mode. / 该模式下可用工具的模型可见名称。
        """
        return [tool.name for tool in self.for_mode(mode)]

    def instructions(self, mode: ToolMode) -> str:
        """
        Render per-tool guidance for the system prompt. / 为系统提示词渲染逐工具指引。

        Args:
            mode: Chat or work mode request. / 请求的 chat 或 work 模式。

        Returns:
            Prompt suffix listing available tools and their guidance. /
            列出可用工具及其指引的提示词后缀。
        """
        return render_tool_instructions(
            {item.name: item.instruction for item in self.descriptors(mode)}
        )

    def execution_tools(self) -> list[BaseTool]:
        """
        Return every registered tool regardless of availability. /
        返回所有已注册工具，忽略可用性。

        Returns:
            SDK tools in registration order. / 按注册顺序的 SDK 工具。
        """
        return [tool for _, tool in self._entries.values()]

    def unavailable(
        self, name: str, *, mode: ToolMode | None = None, legacy=False, arguments=None
    ) -> str | None:
        """
        Explain why one tool cannot run in this mode. / 解释某个工具为何不能在该模式运行。

        Args:
            name: Model-visible tool name. / 模型可见工具名称。
            mode: Optional chat or work mode request. / 可选的 chat 或 work 模式请求。
            legacy: Whether a legacy checkpoint may use deprecated aliases. /
            旧检查点是否可使用弃用别名。

        Returns:
            Stable reason code such as UNKNOWN_TOOL or UNAVAILABLE, or None. /
            UNKNOWN_TOOL、UNAVAILABLE 等稳定原因码，或 None。
        """
        descriptor = self.descriptor(name)
        if descriptor is None:
            return "UNKNOWN_TOOL"
        if (
            not descriptor.enabled
            or self._platform not in descriptor.platforms
            or (descriptor.source == "builtin" and name in self._disabled_builtin)
            or (descriptor.target_for(arguments or {}) in self._disabled_builtin)
            or (mode is not None and mode not in descriptor.mode and not legacy)
            or (descriptor.deprecated and not legacy)
        ):
            return "UNAVAILABLE"
        return None

    def groups(self, mode: ToolMode) -> dict[str, list[str]]:
        """
        Group available tool names by risk class. / 按风险等级分组可用工具名称。

        Args:
            mode: Chat or work mode request. / 请求的 chat 或 work 模式。

        Returns:
            Mapping from read, write, delete, execute and network to tool names. /
            只读、写入、删除、执行与网络到工具名称的映射。
        """
        return {
            risk: [item.name for item in self.descriptors(mode) if item.risk == risk]
            for risk in ("read", "write", "delete", "execute", "network")
        }

    def deprecated_names(self) -> list[str]:
        """Return names of deprecated compatibility aliases. / 返回弃用兼容别名的名称。

        Returns:
            Sorted deprecated tool names. / 排序后的弃用工具名称。
        """
        return sorted(name for name, (item, _) in self._entries.items() if item.deprecated)


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
        b: Optional second finite operand; omit it for sqrt. / 可选的第二个有限浮点数；sqrt
        不传。

    Returns:
        Finite floating-point result; never used for money. / 有限浮点结果，不用于金额。

    Raises:
        ToolException: If operands, operation or result are invalid or not finite. /
            操作数、运算或结果无效或非有限时抛出。
    """
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
    """
    Perform exact decimal money arithmetic on string operands. /
    对字符串操作数执行精确十进制金额运算。

    Args:
        operation: Add, subtract, multiply or divide. / 加、减、乘、除。
        a: Decimal string operand such as 0.10; never a float. / 如 0.10 的十进制字符串，禁止
        float。
        b: Second decimal string operand such as 0.20. / 第二个十进制字符串，例如 0.20。
        decimal_places: Output decimal places from 0 to 28, default 2. / 结果小数位数
        0～28，默认 2。
        rounding: half_up or half_even final-result rounding. / 结果使用 half_up 或 half_even
        舍入。

    Returns:
        Rounded decimal string with the requested scale. / 按请求精度舍入后的十进制字符串。

    Raises:
        ToolException: If an operand, bound, operation or rounding mode is invalid. /
            操作数、边界、运算或舍入方式无效时抛出。
    """
    try:
        return financial_calculate(operation, a, b, decimal_places, rounding)
    except (ValueError, ArithmeticError) as exc:
        raise ToolException(str(exc)) from exc


def create_builtin_tools(settings: ToolSettings | None = None, *, search=None) -> list[BaseTool]:
    """
    Create reusable tool lists for application graph injection. /
    创建供应用注入图的复用工具列表。

    Args:
        settings: Tool workspace and budget settings; environment when omitted. /
            工具工作区与预算配置；省略时读取环境。

    Returns:
        Built-in tools with descriptors for the online graph. /
        供在线图使用的带描述符内置工具。
    """
    from app.agent.tools.builtins import builtin_tools

    return builtin_tools(settings if settings is not None else ToolSettings(), search=search)
