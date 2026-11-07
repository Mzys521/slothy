"""Batch whole turns and produce a bounded, stable fallback digest.
/ 将整轮对话分批并生成有界、稳定的回退摘要。
"""

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from app.agent.prompting.plan import canonical


def clip(text: str, size: int) -> str:
    """Truncate UTF-8 text to a byte budget with an ellipsis.
    / 按字节预算截断 UTF-8 文本并追加省略号。

    Args:
        text: Text to truncate. / 要截断的文本。
        size: Maximum UTF-8 byte length before truncation.
            / 截断前的最大 UTF-8 字节数。

    Returns:
        Original text or a byte-bounded prefix with " […]".
        / 原文，或带 " […]" 的字节受限前缀。
    """
    raw = text.encode()
    return text if len(raw) <= size else raw[:size].decode("utf-8", errors="ignore") + " […]"


def extractive_summary(messages: list[AnyMessage], previous: dict | None) -> str:
    """Build a bounded extractive digest of the current turn batch.
    / 为当前轮次批次构建有界抽取式摘要。

    Args:
        messages: Messages added since the previous summary.
            / 自上次摘要以来新增的消息。
        previous: Prior summary record with a "text" field, if any.
            / 此前摘要记录（含 "text" 字段），可为空。

    Returns:
        Summary text bounded to roughly 10 KB of UTF-8.
        / 约限制在 10 KB UTF-8 内的摘要文本。
    """
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
