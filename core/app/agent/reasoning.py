"""Extract vendor reasoning text without adding it to prompt/history content.
 / 提取厂商推理文本，同时不把它写入提示词或消息正文。"""

from langchain_core.messages import AIMessage


def reasoning_text(messages) -> str:
    """Return the last assistant reasoning text, if the vendor returned one.

    / 返回最后一条助手消息的推理文本（若厂商返回）。

    Args:
        messages: Graph message list. / 图消息列表。

    Returns:
        Reasoning text, or an empty string. / 推理文本或空串。
    """
    for message in reversed(list(messages or [])):
        if not isinstance(message, AIMessage):
            continue
        for key, value in (message.additional_kwargs or {}).items():
            if (key == "reasoning_content" or key.endswith("_reasoning_content")) and isinstance(
                value, str
            ):
                return value
    return ""
