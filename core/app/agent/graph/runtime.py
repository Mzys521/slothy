"""Map runtime limits to LangGraph invocation configuration.
把运行限制映射到 LangGraph 调用配置。
"""

import json
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import AgentSettings


def build_graph_config(
    state: Mapping[str, Any], settings: AgentSettings | None = None
) -> RunnableConfig:
    """Build the RunnableConfig passed to ``graph.invoke``/``ainvoke``. / 构建传给图调用的配置。

    Checkpoints are isolated by the user/thread pair while the original identifiers stay in
    application state and trace metadata. / 中文：使用用户/线程组合键隔离快照；原始 ID 保留在
    业务 State 和 metadata 中。

    Args:
        state: Mapping with trusted ``user_id``, ``thread_id`` and optional ``run_id``.
            中文：包含可信用户、线程与可选运行 ID 的映射。
        settings: Deployment settings; environment defaults are loaded when omitted.
            中文：部署参数，省略时从环境加载。

    Returns:
        English: A config carrying the top-level recursion limit, the JSON checkpoint key and
        non-secret trace metadata. / 中文：包含顶层递归上限、JSON 快照键与非敏感追踪元数据的配置。

    Raises:
        ValueError: If ``user_id`` or ``thread_id`` is missing, blank or not a string.
            中文：用户或线程 ID 缺失、为空或不是字符串时抛出。
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
        # Top-level recursion_limit caps the whole graph, not configurable or model calls. /
        # 中文：顶层 recursion_limit 限制整个 Graph，不放入 configurable 或模型请求。
        recursion_limit=resolved_settings.max_steps,
        # JSON composition avoids delimiter collisions; authentication remains the caller's job. /
        # 中文：JSON 组合避免简单字符串分隔符碰撞；身份仍须由应用认证。
        configurable={
            "thread_id": json.dumps([user_id, thread_id], ensure_ascii=False, separators=(",", ":"))
        },
        # Trace identifiers only, without secrets, profile content or complete messages. /
        # 中文：仅传递可追踪标识，不附带密钥、画像正文或全部消息。
        metadata=metadata,
    )
