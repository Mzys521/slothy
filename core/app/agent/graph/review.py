"""Review model-proposed tool batches before invoking their executors."""

from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, StateSnapshot, interrupt

from app.agent.graph.state import AgentState

ApprovalPolicy = Literal["ask", "every"]
READ_ONLY_COMMANDS = {"pwd", "ls", "cat", "rg", "echo"}
SAFE_TOOLS = {"calculate", "calculate_financial", "list_uploaded_files", "read_uploaded_file"}


def batch(state: AgentState) -> list[dict]:
    message = state["messages"][-1]
    if not isinstance(message, AIMessage) or not message.tool_calls:
        raise ValueError("Expected pending tool calls")
    return message.tool_calls


def needs_review(state: AgentState, calls: list[dict]) -> bool:
    if state.get("approval_policy") == "every":
        return True
    for call in calls:
        if call["name"] in SAFE_TOOLS:
            continue
        if (
            call["name"] == "run_command"
            and isinstance(call["args"].get("command"), str)
            and call["args"]["command"] in READ_ONLY_COMMANDS
        ):
            continue
        return True
    return False


def pending_review(snapshot: StateSnapshot) -> dict | None:
    for item in snapshot.interrupts:
        if isinstance(item.value, dict) and item.value.get("kind") == "tool_review":
            return {
                "approval_id": item.id,
                **item.value,
                "used_memory_ids": snapshot.values.get("used_memory_ids", []),
            }
    return None


def review_tools(state: AgentState) -> Command[Literal["tools", "agent"]]:
    calls = batch(state)
    approved = interrupt(
        {
            "kind": "tool_review",
            "thread_id": state["thread_id"],
            "run_id": state["run_id"],
            "calls": [{"name": call["name"], "arguments": call["args"]} for call in calls],
        }
    )
    if approved is True:
        return Command(goto="tools", update={"approved_calls": calls})
    rejected = [
        ToolMessage(
            content="The user declined this tool batch. Do not treat it as executed.",
            name=call["name"],
            tool_call_id=call["id"],
            status="error",
            additional_kwargs={"review_rejected": True},
        )
        for call in calls
    ]
    return Command(goto="agent", update={"messages": rejected, "approved_calls": []})


def guarded_tools(node: ToolNode):
    async def execute(
        state: AgentState, config: RunnableConfig
    ) -> Command[Literal["agent", "review_tools"]]:
        calls = batch(state)
        if needs_review(state, calls) and calls != state.get("approved_calls"):
            return Command(goto="review_tools")
        result = await node.ainvoke(state, config)
        return Command(goto="agent", update={**result, "approved_calls": []})

    return execute
