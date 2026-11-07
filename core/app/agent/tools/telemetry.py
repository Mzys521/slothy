"""
Per-run tool accounting derived from checkpointed result messages. /
从已检查点工具消息推导的单次运行工具计量。
"""

from langchain_core.messages import ToolMessage

from app.tools.contracts import ToolMetric


def current_tool_messages(state: dict) -> list[ToolMessage]:
    """
    Return tool messages produced after the current run's user message. /
    返回当前运行用户消息之后产生的工具消息。

    Args:
        state: Graph state with run id and message history. / 含运行 id 与消息历史的图状态。

    Returns:
        Tool messages belonging to the current run only. / 仅属于当前运行的工具消息。
    """
    messages = state.get("messages", [])
    start = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].id == f"{state.get('run_id')}:user"
        ),
        -1,
    )
    return [item for item in messages[start + 1 :] if isinstance(item, ToolMessage)]


def tool_metrics(state: dict) -> list[ToolMetric]:
    """Aggregate per-tool metrics for the current run. / 聚合当前运行的工具逐项指标。

    Args:
        state: Graph state with run id and tool messages. / 含运行 id 与工具消息的图状态。

    Returns:
        Metrics sorted by tool name with summed counters. / 按工具名称排序并汇总计数器的指标列表。
    """
    metrics = {}
    for message in current_tool_messages(state):
        raw = message.additional_kwargs.get("tool_metric")
        if not raw:
            continue
        item = ToolMetric.model_validate(raw)
        total = metrics.setdefault(item.name, ToolMetric(name=item.name))
        for field in ToolMetric.model_fields:
            if field != "name":
                setattr(total, field, getattr(total, field) + getattr(item, field))
    return [metrics[name] for name in sorted(metrics)]
