"""工具循环的条件路由。 / Conditional routing for the optional tool loop."""

from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from app.agent.graph.state import AgentState


def route_after_agent(state: AgentState) -> Literal["tools", "finalize_response"]:
    """state：模型节点更新后的状态。 / State after the model node has returned.

    中文：工具之后至少需要再次推理并整理响应；预算不足时不执行工具副作用。
    English: Tools need another model call and finalization; do not run them without that budget.
    """
    message = state["messages"][-1]
    if not isinstance(message, AIMessage):
        raise ValueError("Agent routing requires an AIMessage")
    if message.tool_calls:
        if state.get("remaining_steps", 0) < 3:
            raise GraphRecursionError("Insufficient graph steps for tools, model and finalization")
        return "tools"
    return "finalize_response"
