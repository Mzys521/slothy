from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.graph import AgentState, create_initial_state
from app.agent.tools.gateway import ToolGateway
from app.agent.tools.results import tool_error_content
from app.tools.contracts import ToolResult


async def execute_calls(registry, calls, *, state=None, mode="work"):
    gateway = ToolGateway(registry, mode=mode)
    node = ToolNode(
        registry.execution_tools(),
        awrap_tool_call=gateway.wrap_tool_call,
        handle_tool_errors=tool_error_content,
    )
    builder = StateGraph(AgentState)
    builder.add_node("tools", gateway.execute_batch(node))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    state = state or create_initial_state(
        user_id="u", thread_id="t", user_input="test", run_id="run"
    )
    state = {**state, "messages": [*state["messages"], AIMessage(content="", tool_calls=calls)]}
    result = await builder.compile().ainvoke(state)
    messages = [item for item in result["messages"] if isinstance(item, ToolMessage)]
    return [ToolResult.model_validate_json(item.content) for item in messages], result


def call(name, args, identifier="call"):
    return {"name": name, "args": args, "id": identifier}
