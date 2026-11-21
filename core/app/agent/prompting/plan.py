"""Stable prompt layers with optional provider cache breakpoints.
/ 稳定的提示分层，并支持可选的提供商缓存断点。
"""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage

from app.config import PromptSettings

# Reference layers may be dropped first when the input no longer fits the model window.
# 输入超出模型窗口时，参考层最先被缩减。
REFERENCE_NAMES = {
    "runtime_context",
    "explicit_reference",
    "skill_reference",
    "attachment_reference",
    "personalization_reference",
    "profile_snapshot",
    "conversation_summary",
}
# Drop order: cheapest to lose first. / 丢弃顺序：最可先舍弃的在前。
DROPPABLE_REFERENCES = (
    "skill_reference",
    "explicit_reference",
    "attachment_reference",
    "personalization_reference",
    "profile_snapshot",
    "runtime_context",
)


def estimate_tokens(text: str | None) -> int:
    """Estimate tokens with a stable local heuristic. / 使用稳定的本地启发式估算 Token。

    Args:
        text: Prompt text to estimate. / 待估算的提示文本。

    Returns:
        A positive token estimate. / 正数 Token 估算值。
    """
    return max(1, len(text or "") // 4)


def tool_schema_tokens(names: list[str]) -> int:
    """Estimate the tokens of the bound tool schemas. / 估算已绑定工具 schema 的 Token。

    Args:
        names: Model-visible tool names. / 模型可见的工具名称。

    Returns:
        Estimated schema tokens. / 估算的 schema Token。
    """
    return sum(estimate_tokens(name) for name in names) * 8


def message_tokens(message: AnyMessage) -> int:
    """Estimate one message's tokens. / 估算单条消息的 Token。

    Args:
        message: Chat message to estimate. / 待估算的聊天消息。

    Returns:
        Estimated tokens of its wire content. / 其线路内容的估算 Token。
    """
    content = message.content
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    return estimate_tokens(text) + estimate_tokens(str(message.name or ""))


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


@dataclass(frozen=True)
class PromptBudget:
    """Input budget derived from the selected model's context window.
    / 由所选模型窗口推导的输入预算。

    Attributes:
        context_window: Total model context window in tokens. / 模型总上下文窗口 Token 数。
        output_reserve: Tokens reserved for the reply. / 为回复预留的 Token。
        source: Where the window came from, catalog or default. / 窗口来源：目录或默认值。
        estimator: Local estimation strategy name. / 本地估算策略名称。
    """

    context_window: int
    output_reserve: int
    source: str = "catalog"
    estimator: str = "heuristic"

    @property
    def input_limit(self) -> int:
        """Return the tokens available for the whole request. / 返回整个请求可用的 Token 数。"""
        return max(1, self.context_window - self.output_reserve)


@dataclass(frozen=True)
class PromptFit:
    """Result of fitting one prompt plan into a budget. / 把提示计划装入预算的结果。

    Attributes:
        plan: Fitted plan, trimmed when needed. / 已装入预算的计划（必要时已裁剪）。
        estimated_tokens: Estimated input tokens after fitting. / 裁剪后的估算输入 Token。
        limit: Input token limit excluding tool schemas. / 不含工具 schema 的输入上限。
        dropped_references: Reference layer names removed to fit. / 为装入预算而移除的参考层名。
        removed_turns: Complete history turns compressed away. / 被压缩掉的完整历史轮次。
        over_limit: Whether even the minimal prompt exceeds the window. / 最小提示是否仍超窗。
    """

    plan: PromptPlan
    estimated_tokens: int
    limit: int
    dropped_references: tuple[str, ...] = ()
    removed_turns: int = 0
    over_limit: bool = False

    def metadata(self, budget: PromptBudget) -> dict:
        """Return the budget fields recorded with prompt telemetry. / 返回随提示遥测记录的预算字段。

        Args:
            budget: Budget the plan was fitted into. / 计划被装入的预算。

        Returns:
            Bounded, JSON-safe budget metadata. / 有界且可序列化的预算元数据。
        """
        return {
            "budget_context_window": budget.context_window,
            "budget_output_reserve": budget.output_reserve,
            "budget_input_limit": self.limit,
            "budget_input_estimated": self.estimated_tokens,
            "budget_window_source": budget.source,
            "budget_estimator": budget.estimator,
            "budget_dropped_references": list(self.dropped_references),
            "budget_removed_turns": self.removed_turns,
            "budget_trimmed": bool(self.dropped_references or self.removed_turns),
        }


class ContextLimitExceeded(ValueError):
    """The smallest possible prompt still exceeds the model's context window. /
    最小可能的提示仍然超出模型上下文窗口。"""

    def __init__(self, budget: PromptBudget, estimated: int, limit: int) -> None:
        """Record the budget details with the error. / 把预算细节附加到错误上。

        Args:
            budget: Budget that could not be met. / 无法满足的预算。
            estimated: Estimated minimal prompt tokens. / 最小提示的估算 Token。
            limit: Input token limit excluding tool schemas. / 不含工具 schema 的输入上限。
        """
        self.budget = budget
        self.estimated = estimated
        self.limit = limit
        super().__init__(
            "This request is larger than the selected model's context window. Edit the "
            "message or remove attachments and references, then send it again. / "
            "本次请求超出所选模型的上下文窗口，请编辑消息或移除附件与引用后重新发送。"
        )


def _is_reference(message: AnyMessage) -> bool:
    """Report whether a message is an optional reference layer. / 判断消息是否为可选参考层。

    Args:
        message: Chat message to classify. / 待分类的聊天消息。

    Returns:
        True for named reference messages. / 具名参考消息返回 True。
    """
    return message.name in REFERENCE_NAMES


def _current_turn_start(messages: list[AnyMessage], l1_end: int) -> int:
    """Locate the first message of the current turn. / 定位当前轮次的首条消息。

    Args:
        messages: Ordered prompt messages. / 有序提示消息。
        l1_end: Index of the last system-layer message. / 系统层最后一条消息索引。

    Returns:
        Index of the current turn's human message, or the last index. / 当前轮用户消息索引。
    """
    candidates = [
        index
        for index, message in enumerate(messages)
        if index > l1_end
        and isinstance(message, HumanMessage)
        and not _is_reference(message)
    ]
    return candidates[-1] if candidates else len(messages) - 1


def fit_prompt_budget(
    plan: PromptPlan, budget: PromptBudget, *, tool_schema: int = 0
) -> PromptFit:
    """Fit a composed prompt into the model's input budget. / 把已组装提示装入模型输入预算。

    Optional reference layers are dropped first, then the oldest complete history turns are
    compressed away. The current user message and every surviving tool call/result pair are
    always kept. / 先丢弃可选参考层，再压缩最旧的完整历史轮次；当前用户消息和所有存活工具调用/
    结果配对始终保留。

    Args:
        plan: Composed prompt plan. / 已组装的提示计划。
        budget: Context-window budget for this call. / 本次调用的上下文窗口预算。
        tool_schema: Estimated tokens of the bound tool schemas. / 已绑定工具 schema 的估算 Token。

    Returns:
        The fitted plan plus what had to be dropped. / 装入预算的计划及被舍弃内容。
    """
    limit = max(1, budget.input_limit - max(0, tool_schema))
    messages = list(plan.messages)

    def estimated() -> int:
        return sum(message_tokens(message) for message in messages)

    dropped: list[str] = []
    removed_turns = 0
    total = estimated()
    for name in DROPPABLE_REFERENCES:
        if total <= limit:
            break
        kept = [message for message in messages if message.name != name]
        if len(kept) != len(messages):
            dropped.append(name)
            messages = kept
            total = estimated()
    # Layer 1 may contain the profile snapshot; dropping it shifts the stable boundary.
    # 第 1 层可能包含画像快照，丢弃它会使稳定边界前移。
    original_l1 = plan.messages[: plan.l1_end + 1]
    l1_end = max(0, sum(1 for message in original_l1 if message.name not in dropped) - 1)
    while total > limit:
        current_start = _current_turn_start(messages, l1_end)
        history = [
            index
            for index in range(l1_end + 1, current_start)
            if not _is_reference(messages[index]) and not isinstance(messages[index], SystemMessage)
        ]
        if not history:
            break
        # Remove one complete turn: from the first history message to the next human turn.
        # 移除一个完整轮次：从首条历史消息到下一个用户轮次。
        start = next(
            (index for index in history if isinstance(messages[index], HumanMessage)), None
        )
        if start is None:
            break
        end = next(
            (
                index
                for index in history
                if index > start and isinstance(messages[index], HumanMessage)
            ),
            None,
        )
        if end is None:
            break
        messages = messages[:start] + messages[end:]
        removed_turns += 1
        total = estimated()
    current_start = _current_turn_start(messages, l1_end)
    history_indices = [
        index
        for index in range(l1_end + 1, current_start)
        if not _is_reference(messages[index]) and not isinstance(messages[index], SystemMessage)
    ]
    l2_end = history_indices[-1] if history_indices else l1_end
    metadata = dict(plan.metadata)
    if dropped or removed_turns:
        history = [stable_message(message) for message in messages[l1_end + 1 : l2_end + 1]]
        layer3 = [stable_message(message) for message in messages[l2_end + 1 :]]
        layer2_turns = sum(
            isinstance(message, HumanMessage) for message in messages[l1_end + 1 : l2_end + 1]
        )
        metadata.pop("prompt_plan_id", None)
        metadata.update(
            l2_hash=digest(history),
            l3_hash=digest(layer3),
            l2_bytes=len(canonical(history).encode()),
            l3_bytes=len(canonical(layer3).encode()),
            l2_turns=layer2_turns,
        )
        metadata["prompt_plan_id"] = digest(metadata)[:24]
    fitted = PromptPlan(deepcopy(messages), l1_end, l2_end, metadata)
    return PromptFit(
        fitted,
        total,
        limit,
        tuple(dropped),
        removed_turns,
        total > limit,
    )
