"""把运行限制映射到 LangGraph 配置。 / Map runtime limits to LangGraph configuration."""

import json
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import AgentSettings


def build_graph_config(
    state: Mapping[str, Any], settings: AgentSettings | None = None
) -> RunnableConfig:
    """构建传给 graph.invoke/ainvoke 的配置。 / Build config for graph.invoke/ainvoke.

    Args:
        state: 可信用户、线程与可选运行 ID 的映射。 / Trusted user/thread and optional run ID.
        settings: 部署参数，省略时加载环境。 / Deployment settings, loaded from env if omitted.

    中文：使用用户/线程组合键隔离快照；原始 ID 保留在业务 State 和 metadata 中。
    English: Isolate checkpoints by user/thread pair; retain original IDs in state and metadata.
    """
    resolved_settings = settings if settings is not None else AgentSettings()
    for name in ("user_id", "thread_id"):
        if not isinstance(state.get(name), str) or not state[name].strip():
            raise ValueError(f"{name} must be a non-empty string")
    user_id, thread_id = state["user_id"].strip(), state["thread_id"].strip()
    metadata = {"user_id": user_id, "thread_id": thread_id}
    if state.get("run_id") is not None:
        metadata["run_id"] = state["run_id"]
    return RunnableConfig(
        # 中文：顶层 recursion_limit 限制整个 Graph，不放入 configurable 或模型请求。
        # English: Top-level recursion_limit caps the graph, not configurable values or model calls.
        recursion_limit=resolved_settings.max_steps,
        # 中文：JSON 组合避免简单字符串分隔符碰撞；身份仍须由应用认证。
        # English: JSON avoids delimiter collisions; application authentication remains separate.
        configurable={
            "thread_id": json.dumps([user_id, thread_id], ensure_ascii=False, separators=(",", ":"))
        },
        # 中文：仅传递可追踪标识，不附带密钥、画像正文或全部消息。
        # English: Trace identifiers only, without secrets, profile content, or complete messages.
        metadata=metadata,
    )
