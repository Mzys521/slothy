"""The gateway preserves ToolNode results, activity events and failure boundaries."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool, ToolException
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.tools import ToolRegistry
from app.agent.tools.gateway import ToolGateway


def registry_and_request(call, **state):
    tool = StructuredTool.from_function(
        lambda text: text, name="example", description="Return text."
    )
    return ToolRegistry.from_tools([tool]), SimpleNamespace(tool_call=call, state=state, tool=tool)


@pytest.mark.parametrize("approved", [False, True])
async def test_gateway_keeps_result_and_activity_contract(monkeypatch, approved):
    events = []
    monkeypatch.setattr("app.agent.tools.gateway.get_stream_writer", lambda: events.append)
    call = {"id": "call", "name": "example", "args": {"text": "hello"}}
    registry, request = registry_and_request(call, approved_calls=[call] if approved else [])
    original = ToolMessage(
        content='{"ok": false, "error": "original"}',
        tool_call_id="call",
        name="example",
        status="error",
        additional_kwargs={"existing": 1},
    )

    async def execute(value):
        assert value is request
        return original

    result = await ToolGateway(registry).wrap_tool_call(request, execute)
    assert json.loads(result.content)["error"]["code"] == "EXEC_FAILED"
    assert result.status == "error"
    assert result.additional_kwargs.get("review_approved", False) is approved
    assert result.additional_kwargs["tool_metric"]["errors"] == 1
    assert original.additional_kwargs == {"existing": 1}
    assert events == [("tool", "call", None), ("tool", "call", result)]


async def test_gateway_rechecks_live_disable_before_calling_executor(monkeypatch):
    monkeypatch.setattr("app.agent.tools.gateway.get_stream_writer", lambda: lambda event: None)
    enabled = {"example"}
    registry, request = registry_and_request(
        {"id": "call", "name": "example", "args": {"text": "x"}}
    )
    gateway = ToolGateway(registry, tool_enabled=lambda name: name in enabled)
    executed = []

    async def execute(value):
        executed.append(value)
        return ToolMessage(content="unchanged", tool_call_id="call")

    assert (
        json.loads((await gateway.wrap_tool_call(request, execute)).content)["data"]["output"]
        == "unchanged"
    )
    enabled.clear()
    result = await gateway.wrap_tool_call(request, execute)
    assert result.status == "error"
    assert json.loads(result.content)["error"]["code"] == "CONNECTOR_DISABLED"
    assert executed == [request]


@pytest.mark.parametrize("error", [ValueError, ToolException, asyncio.CancelledError])
async def test_gateway_propagates_exceptions_and_cancellation(monkeypatch, error):
    events = []
    monkeypatch.setattr("app.agent.tools.gateway.get_stream_writer", lambda: events.append)
    registry, request = registry_and_request(
        {"id": "call", "name": "example", "args": {"text": "x"}}
    )

    async def execute(value):
        raise error("original failure")

    if error is asyncio.CancelledError:
        with pytest.raises(error, match="original failure"):
            await ToolGateway(registry).wrap_tool_call(request, execute)
        assert events == [("tool", "call", None)]
    else:
        result = await ToolGateway(registry).wrap_tool_call(request, execute)
        assert not json.loads(result.content)["ok"]
        assert "original failure" not in result.content
        assert len(events) == 2


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("example", {"number": 2}),
        ("example", {"number": "invalid"}),
        ("example", {"number": -1}),
        ("missing", {}),
    ],
)
async def test_tool_node_output_is_identical_with_gateway(monkeypatch, name, arguments):
    monkeypatch.setattr("app.agent.tools.gateway.get_stream_writer", lambda: lambda event: None)

    def example(number: int):
        if number < 0:
            raise ToolException("Original error")
        return {"value": number}

    tools = [StructuredTool.from_function(example, description="Return a number.")]
    gateway = ToolGateway(ToolRegistry.from_tools(tools))
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name,
                        "args": arguments,
                        "id": "call",
                    }
                ],
            )
        ]
    }
    plain = ToolNode(tools, handle_tool_errors="工具执行失败。 / Tool execution failed.")
    wrapped = ToolNode(
        tools,
        handle_tool_errors="工具执行失败。 / Tool execution failed.",
        awrap_tool_call=gateway.wrap_tool_call,
    )

    async def invoke(node):
        builder = StateGraph(MessagesState)
        builder.add_node("tools", node)
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        result = await builder.compile().ainvoke(state)
        return result["messages"][-1]

    expected = await invoke(plain)
    actual = await invoke(wrapped)
    # Message reducers assign a fresh storage ID in each independent graph.
    envelope = json.loads(actual.content)
    assert actual.status == expected.status
    assert actual.tool_call_id == expected.tool_call_id
    if actual.status == "success":
        assert envelope["data"] == json.loads(expected.content)
    else:
        assert envelope["error"]["code"] == (
            "UNKNOWN_TOOL"
            if name == "missing"
            else "INVALID_ARGS"
            if arguments["number"] == "invalid"
            else "EXEC_FAILED"
        )
