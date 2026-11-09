"""Display-only reasoning extraction. / 推理提取与提示词、历史正文分离。"""

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.reasoning import reasoning_text


def test_reasoning_text_reads_vendor_metadata_only():
    """Vendor reasoning is read from metadata, never message content. / 仅从元数据读取推理。"""
    message = AIMessage(
        content="final answer",
        additional_kwargs={"deepseek_reasoning_content": "step one\nstep two"},
    )
    assert reasoning_text([HumanMessage("hi"), message]) == "step one\nstep two"


def test_reasoning_text_returns_empty_without_vendor_reasoning():
    """Plain content never becomes reasoning text. / 普通消息不产生推理文本。"""
    assert reasoning_text([AIMessage(content="thinking out loud")]) == ""
    assert reasoning_text([]) == ""
