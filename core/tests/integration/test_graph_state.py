"""用真实 LangGraph 验证 reducer 与步数预算。 / Verify reducers and budgets with real LangGraph."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from app.agent.graph import AgentState, build_graph_config, create_initial_state
from app.config import AgentSettings


def test_graph_merges_messages_by_id_and_preserves_tool_messages() -> None:
    settings = AgentSettings(_env_file=None)
    initial = create_initial_state(
        user_id="user-1", thread_id="thread-1", user_input="查询天气", settings=settings
    )

    def agent(state: AgentState) -> dict:
        assert state["user_input"] == "查询天气"
        return {
            "messages": [
                AIMessage(
                    id="answer",
                    content="",
                    tool_calls=[{"id": "call-1", "name": "weather", "args": {}}],
                )
            ]
        }

    def tools(state: AgentState) -> dict:
        return {"messages": [ToolMessage(id="tool-result", tool_call_id="call-1", content="晴天")]}

    def finalize(state: AgentState) -> dict:
        return {"messages": [AIMessage(id="final", content="今天晴天")], "response": "今天晴天"}

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent)
    builder.add_node("tools", tools)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", "tools")
    builder.add_edge("tools", "finalize")
    builder.add_edge("finalize", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = build_graph_config(initial, settings)
    result = graph.invoke(initial, config)
    assert [message.type for message in result["messages"]] == ["human", "ai", "tool", "ai"]
    assert result["messages"][2].tool_call_id == "call-1"
    assert result["response"] == "今天晴天"

    graph.update_state(config, {"messages": [AIMessage(id="final", content="更新后的回答")]})
    persisted = graph.get_state(config).values
    assert len(persisted["messages"]) == 4
    assert persisted["messages"][-1].content == "更新后的回答"
    assert "remaining_steps" not in persisted


def test_configured_step_cap_stops_a_loop_and_exposes_remaining_budget() -> None:
    settings = AgentSettings(max_steps=3, _env_file=None)
    initial = create_initial_state(
        user_id="user-1", thread_id="thread-1", user_input="test", settings=settings
    )
    remaining = []

    def loop(state: AgentState) -> dict:
        remaining.append(state["remaining_steps"])
        return {"response": "still running"}

    builder = StateGraph(AgentState)
    builder.add_node("loop", loop)
    builder.add_edge(START, "loop")
    builder.add_edge("loop", "loop")
    graph = builder.compile()
    with pytest.raises(GraphRecursionError):
        graph.invoke(initial, build_graph_config(initial, settings))
    assert len(remaining) == settings.max_steps
    assert remaining == sorted(remaining, reverse=True)
    assert len(set(remaining)) == len(remaining)


def test_default_step_cap_allows_the_documented_online_path() -> None:
    settings = AgentSettings(_env_file=None)
    initial = create_initial_state(
        user_id="user-1", thread_id="thread-1", user_input="test", settings=settings
    )
    builder = StateGraph(AgentState)
    nodes = [
        "prepare_run",
        "load_profile",
        "retrieve_memories",
        "build_personalized_context",
        "agent",
        "finalize",
    ]
    for name in nodes:
        builder.add_node(name, lambda state: {})
    for source, target in zip([START, *nodes], [*nodes, END], strict=True):
        builder.add_edge(source, target)
    result = builder.compile().invoke(initial, build_graph_config(initial, settings))
    assert result["run_id"] == initial["run_id"]
    assert result["user_input"] == "test"
