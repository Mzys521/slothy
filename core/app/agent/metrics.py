"""Measurements from completed model calls in one conversation turn.
单轮对话中已完成模型调用的度量。
"""

from pydantic import BaseModel, Field

from app.agent.tools.telemetry import tool_metrics
from app.tools.contracts import ToolMetric


class RunMetrics(BaseModel):
    """Aggregated token, timing and prompt-cache measurements for one turn.
    单轮对话的 token、耗时与提示词缓存度量。

    Token fields sum across model calls; ``context_tokens`` reports the last call. Optional
    prompt-plan fields describe the cache layers, while ``tools`` aggregates per-tool metrics.
    / 中文：token 字段跨模型调用求和，``context_tokens`` 取最后一次调用；可选提示词计划字段
    描述缓存分层，``tools`` 汇总各工具指标。
    """

    model: str | None = None
    reasoning_effort: str | None = None
    total_tokens: int
    output_tokens: int
    input_tokens: int
    cached_input_tokens: int
    cache_reported_input_tokens: int
    model_seconds: float
    context_tokens: int
    context_window: int | None = None
    cache_write_tokens: int | None = None
    prefix_hit_ratio: float | None = None
    prompt_plan_id: str | None = None
    prompt_version: str | None = None
    l1_hash: str | None = None
    l2_hash: str | None = None
    l3_hash: str | None = None
    l1_bytes: int | None = None
    l2_bytes: int | None = None
    toolset_hash: str | None = None
    l2_turns: int | None = None
    l3_bytes: int | None = None
    profile_snapshot_version: int | None = None
    profile_snapshot_revision: int | None = None
    history_segment_version: int | None = None
    summary_tokens: int = 0
    tools: list[ToolMetric] = Field(default_factory=list)
    usage_reported: bool = True


def run_metrics(state: dict) -> RunMetrics | None:
    """Aggregate turn metrics from model calls and tool telemetry.
    汇总模型调用与工具遥测的轮次指标。

    Calls may be absent while tool metrics exist; a zero row is then substituted so totals and
    selection metadata stay well-defined. / 中文：可能只有工具指标而没有模型调用；此时补一行
    零值，使总量与选择元数据保持有定义。

    Args:
        state: Graph values carrying ``model_stats`` and ``model_selection``. / 中文：携带
            ``model_stats`` 与 ``model_selection`` 的图状态值。

    Returns:
        English: Aggregated metrics, or ``None`` when there is neither model nor tool telemetry.
        / 中文：聚合指标；既无模型也无工具遥测时返回 ``None``。
    """
    calls = state.get("model_stats") or []
    tools = tool_metrics(state)
    if not calls and not tools:
        if state.get("response_source") == "identity_fact":
            return RunMetrics(
                total_tokens=0,
                output_tokens=0,
                input_tokens=0,
                cached_input_tokens=0,
                cache_reported_input_tokens=0,
                model_seconds=0,
                context_tokens=0,
                usage_reported=True,
            )
        return None
    reported_usage = bool(calls)
    calls = calls or [
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": None,
            "seconds": 0,
        }
    ]
    selection = state.get("model_selection") or {}
    reported = sum(call["input_tokens"] for call in calls if call["cached_tokens"] is not None)
    read = sum(call["cached_tokens"] or 0 for call in calls)
    writes = [
        call["cache_write_tokens"] for call in calls if call.get("cache_write_tokens") is not None
    ]
    metadata = {
        key: calls[-1].get(key)
        for key in (
            "prompt_plan_id",
            "prompt_version",
            "l1_hash",
            "l2_hash",
            "l3_hash",
            "l1_bytes",
            "l2_bytes",
            "toolset_hash",
            "l2_turns",
            "l3_bytes",
            "profile_snapshot_version",
            "profile_snapshot_revision",
            "history_segment_version",
        )
    }
    return RunMetrics(
        model=selection.get("id"),
        reasoning_effort=selection.get("effort"),
        total_tokens=sum(call["total_tokens"] for call in calls),
        output_tokens=sum(call["output_tokens"] for call in calls),
        input_tokens=sum(call["input_tokens"] for call in calls),
        cached_input_tokens=sum(call["cached_tokens"] or 0 for call in calls),
        cache_reported_input_tokens=sum(
            call["input_tokens"] for call in calls if call["cached_tokens"] is not None
        ),
        model_seconds=sum(call["seconds"] for call in calls),
        context_tokens=calls[-1]["total_tokens"],
        context_window=selection.get("context_window"),
        cache_write_tokens=sum(writes) if writes else None,
        summary_tokens=sum(
            call["total_tokens"] for call in calls if call.get("purpose") == "history_summary"
        ),
        prefix_hit_ratio=read / reported if reported else None,
        **metadata,
        tools=tools,
        usage_reported=reported_usage,
    )
