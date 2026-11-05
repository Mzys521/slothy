"""Batch whole turns and produce a bounded, stable fallback digest."""

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from app.agent.prompting.plan import canonical


def clip(text: str, size: int) -> str:
    raw = text.encode()
    return text if len(raw) <= size else raw[:size].decode("utf-8", errors="ignore") + " […]"


def extractive_summary(messages: list[AnyMessage], previous: dict | None) -> str:
    parts = [previous["text"]] if previous else []
    for message in messages:
        if isinstance(message, HumanMessage):
            parts.append("User: " + clip(message.text, 500))
        elif isinstance(message, AIMessage):
            if message.tool_calls:
                parts.append("Tools requested: " + clip(canonical(message.tool_calls), 350))
            if message.text:
                parts.append("Assistant: " + clip(message.text, 750))
        elif isinstance(message, ToolMessage):
            parts.append("Tool result: " + clip(message.text, 350))
    text = "\n".join(parts)
    if len(text.encode()) > 10000:
        raw = text.encode()
        text = (
            raw[:3000].decode("utf-8", errors="ignore")
            + "\n[Earlier details omitted; full conversation remains in history.]\n"
            + raw[-6500:].decode("utf-8", errors="ignore")
        )
    return text
