"""Stable prompt layers with optional provider cache breakpoints.
/ 稳定的提示分层，并支持可选的提供商缓存断点。
"""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage

from app.config import PromptSettings


def canonical(value) -> str:
    """Serialize a value to deterministic compact JSON. / 将值序列化为确定性的紧凑 JSON。

    Args:
        value: JSON-serializable value to serialize. / 要序列化的 JSON 兼容值。

    Returns:
        Canonical JSON text with sorted keys. / 排序键后的规范 JSON 文本。
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    """Return the SHA-256 hex digest of a value's canonical JSON.
    / 返回值规范 JSON 的 SHA-256 十六进制摘要。

    Args:
        value: JSON-serializable value to hash. / 要计算哈希的 JSON 兼容值。

    Returns:
        Hex-encoded SHA-256 digest. / 十六进制 SHA-256 摘要。
    """
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def reference(name: str, value: dict | list) -> HumanMessage:
    """Build a named human reference message carrying canonical JSON.
    / 构建携带规范 JSON 的具名人类参考消息。

    Args:
        name: Message name and reference type. / 消息名称与参考类型。
        value: Payload mapping or list to encode. / 要编码的载荷字典或列表。

    Returns:
        HumanMessage whose content is canonical JSON. / 内容为规范 JSON 的 HumanMessage。
    """
    payload = {"reference_type": name, **value} if isinstance(value, dict) else value
    return HumanMessage(name=name, content=canonical(payload))


def stable_message(message: AnyMessage) -> dict:
    """Strip a message to the fields affecting provider prompt text.
    / 将消息裁剪为影响提供商提示文本的字段。

    Args:
        message: Chat message to normalize. / 要规范化的聊天消息。

    Returns:
        Wire-stable mapping with role, content and protocol fields.
        / 含角色、内容与协议字段的稳定字典。
    """
    # Only fields sent to the provider; checkpoint IDs and usage are not prompt text.
    result = {"role": message.type, "content": message.content}
    if message.name:
        result["name"] = message.name
    if isinstance(message, AIMessage):
        result["tool_calls"] = message.tool_calls
        result["additional_kwargs"] = message.additional_kwargs
    if isinstance(message, ToolMessage):
        result["tool_call_id"] = message.tool_call_id
    return result


@dataclass
class PromptPlan:
    """Layered prompt plan with L1/L2 boundaries and cache metadata.
    / 带 L1/L2 边界与缓存元数据的分层提示计划。

    Attributes:
        messages: Ordered messages sent to the provider. / 发送给提供商的有序消息。
        l1_end: Index of the last stable system-layer message. / 稳定系统层最后一条消息的索引。
        l2_end: Index of the last stable history-layer message. / 稳定历史层最后一条消息的索引。
        metadata: Prompt hashes, sizes and version fields. / 提示哈希、大小与版本字段。
    """

    messages: list[AnyMessage]
    l1_end: int
    l2_end: int
    metadata: dict

    def for_provider(self, protocol: str, breakpoints: str) -> list[AnyMessage]:
        """Return a provider-ready copy with optional cache breakpoints.
        / 返回带可选缓存断点的提供商副本。

        Args:
            protocol: Provider protocol, for example "anthropic". / 提供商协议，例如 "anthropic"。
            breakpoints: Breakpoint mode; "off" disables markers. / 断点模式；"off" 表示禁用标记。

        Returns:
            Deep-copied messages with cache_control markers applied.
            / 应用 cache_control 标记后的消息副本。
        """
        messages = deepcopy(self.messages)
        if protocol != "anthropic" or breakpoints == "off":
            return messages
        # Mark actual provider content blocks. Never mark thinking/signature blocks.
        for boundary in sorted({self.l1_end, self.l2_end}):
            for index in range(boundary, -1, -1):
                message = messages[index]
                if isinstance(message.content, str):
                    if not message.content:
                        continue
                    message.content = [
                        {
                            "type": "text",
                            "text": message.content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                    break
                blocks = message.content
                eligible = [
                    block
                    for block in blocks
                    if isinstance(block, dict)
                    and block.get("type")
                    in {"text", "tool_use", "tool_result", "image", "document"}
                ]
                if eligible:
                    eligible[-1]["cache_control"] = {"type": "ephemeral"}
                    break
        return messages


def compose(
    *,
    system: str,
    settings: PromptSettings,
    messages: list[AnyMessage],
    run_id: str,
    references: list[AnyMessage],
    snapshot: dict | None = None,
    summary: dict | None = None,
    toolset_hash: str = "",
    context_by_anchor: dict[str, list[AnyMessage]] | None = None,
) -> PromptPlan:
    """Compose the layered prompt plan for one run. / 为单次运行组装分层提示计划。

    Args:
        system: System prompt text. / 系统提示文本。
        settings: Prompt settings controlling layout and version. / 控制布局与版本的提示设置。
        messages: Conversation messages including the current user turn.
            / 含当前用户轮次的会话消息。
        run_id: Current run identifier used to locate the user turn. / 用于定位用户轮次的运行标识。
        references: Reference messages such as profile or attachments. / 画像或附件等参考消息。
        snapshot: Optional profile snapshot payload. / 可选的画像快照载荷。
        summary: Optional conversation summary record. / 可选的会话摘要记录。
        toolset_hash: Hash of the bound tool set. / 绑定工具集的哈希。

    Returns:
        Composed prompt plan with layer boundaries and metadata. / 含分层边界与元数据的提示计划。

    Raises:
        ValueError: If the current user message is missing from messages.
            / 会话消息中缺少当前用户消息时抛出。
    """
    start = next(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage) and message.id == f"{run_id}:user"
        ),
        None,
    )
    if start is None:
        raise ValueError("Current user message is missing from the conversation")
    l1: list[AnyMessage] = [SystemMessage(content=system + "\nprompt_version=" + settings.version)]
    if snapshot:
        l1.append(reference("profile_snapshot", snapshot))
    def materialize(items):
        result = []
        for item in items:
            result.append(item)
            result.extend((context_by_anchor or {}).get(item.id, []))
        return result

    history = materialize(messages[:start])
    if summary:
        history.insert(0, reference("conversation_summary", summary))
    current = materialize(messages[start:])
    if settings.layout == "legacy" or not settings.layered_l3:
        ordered = [*l1, *references, *history, *current]
        l2_end = len(l1) - 1
    else:
        ordered = [*l1, *history, *references, *current]
        l2_end = len(l1) + len(history) - 1
    stable_l1 = [stable_message(item) for item in l1]
    stable_l2 = [stable_message(item) for item in history]
    stable_l3 = [stable_message(item) for item in [*references, *current]]
    l1_hash = digest({"messages": stable_l1, "tools": toolset_hash})
    metadata = {
        "prompt_version": settings.version,
        "l1_hash": l1_hash,
        "l2_hash": digest(stable_l2),
        "l3_hash": digest(stable_l3),
        "l1_bytes": len(canonical(stable_l1).encode()),
        "l2_bytes": len(canonical(stable_l2).encode()),
        "toolset_hash": toolset_hash,
        "l2_turns": sum(isinstance(item, HumanMessage) for item in messages[:start]),
        "l3_bytes": len(canonical(stable_l3).encode()),
        "history_segment_version": (summary or {}).get("version", 0),
        "profile_snapshot_version": (snapshot or {}).get("version"),
        "profile_snapshot_revision": (snapshot or {}).get("revision", 0),
    }
    metadata["prompt_plan_id"] = digest(metadata)[:24]
    return PromptPlan(ordered, len(l1) - 1, l2_end, metadata)
