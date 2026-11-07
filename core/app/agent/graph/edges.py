"""Conditional routing for the optional tool loop. / 可选工具循环的条件路由。"""

from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from app.agent.graph.state import AgentState


def route_after_agent(state: AgentState) -> Literal["tools", "finalize_response"]:
    """Route after the model node: execute tools or finalize the answer.
    模型节点之后的路由：执行工具或整理回答。

    Tools need at least one more model call plus finalization, so the remaining
    super-step budget is checked before any tool side effect runs. / 工具之后至少还需要
    一次模型调用和整理步骤，因此在产生工具副作用前先检查剩余 super-step 预算。

    Args:
        state: Working state after the model node updated it. / 模型节点更新后的工作状态。

    Returns:
        English: ``"tools"`` when the last message carries tool calls, otherwise
            ``"finalize_response"``. / 中文：最后一条消息带工具调用时返回 ``"tools"``，
            否则返回 ``"finalize_response"``。

    Raises:
        ValueError: If the last message is not an ``AIMessage``. / 最后一条消息不是 ``AIMessage``。
        GraphRecursionError: If fewer than three super-steps remain. / 剩余 super-step 少于三个。
    """
    message = state["messages"][-1]
    if not isinstance(message, AIMessage):
        raise ValueError("Agent routing requires an AIMessage")
    if message.tool_calls:
        if state.get("remaining_steps", 0) < 3:
            raise GraphRecursionError("Insufficient graph steps for tools, model and finalization")
        return "tools"
    return "finalize_response"
