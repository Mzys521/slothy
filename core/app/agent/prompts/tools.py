"""Compatibility wrapper for callers that only hold tool names. / 仅持有工具名的调用方兼容层。"""

from app.agent.tools.descriptor import render_tool_instructions
from app.agent.tools.registry import ToolRegistry


def tool_instructions(names: list[str], registry: ToolRegistry | None = None) -> str:
    """Render selected registry guidance from tool names. / 根据工具名渲染注册表中的工具说明。

    中文：按名称排序；提供注册表时读取真实说明，否则只列出名称；没有名称时返回空串。

    Args:
        names: Selected tool names. / 已选工具名称列表。
        registry: Optional source of tool guidance. / 可选的工具说明来源。

    Returns:
        English: Newline-prefixed instruction block, or ``""`` when ``names`` is empty.
        / 中文：以换行开头的说明块；``names`` 为空时返回 ``""``。
    """
    return render_tool_instructions({name: descriptor.instruction if registry
                                    and (descriptor := registry.descriptor(name)) else ""
                                    for name in names})
