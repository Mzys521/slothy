"""Public execution activity derived from the current run's graph messages.
由当前运行消息推导的公开执行活动。
"""

import json
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

# Compatibility export; execution observation now belongs to the tool gateway. /
# 中文：兼容导出；执行观测逻辑已归工具网关所有。
from app.agent.tools.gateway import observe_tool as observe_tool
from app.tools.contracts import Artifact, ToolResult


class RunActivity(BaseModel):
    """One public activity entry shown in the chat timeline. / 聊天时间线中展示的单条公开活动。

    ``kind`` selects the row type, ``status`` its lifecycle state, and ``id`` a stable
    position key. Tool rows may also carry a risk label, returned artifacts, duration and
    error code. / 中文：``kind`` 选择行类型，``status`` 表示生命周期状态，``id`` 是稳定的位置
    键；工具行还可能携带风险标签、产物、耗时与错误码。
    """

    id: str
    kind: Literal["prepare", "context", "model", "tool"]
    status: Literal["running", "done", "requested", "approval", "error", "rejected"]
    name: str | None = None
    arguments: str | None = None
    result: str | None = None
    memory_count: int | None = None
    approved: bool = False
    risk: str | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    duration_ms: int | None = None
    error_code: str | None = None
    summary: str | None = None
    error_hint: str | None = None


def preview(value: object, limit: int) -> str:
    """Render a bounded, redacted preview of a tool argument or result.
    生成有界、脱敏的参数或结果预览。

    Dict keys naming secrets are replaced with ``[redacted]``; long text is truncated with
    an ellipsis. / 中文：键名疑似密钥的值替换为 ``[redacted]``；过长文本以省略号截断。

    Args:
        value: Any JSON-like value, JSON string or plain text.
            中文：任意类 JSON 值、JSON 字符串或纯文本。
        limit: Maximum preview characters. / 预览字符上限。

    Returns:
        English: A readable preview no longer than ``limit`` characters.
        中文：不超过 ``limit`` 字符的可读预览。
    """

    def redact(item):
        """Recursively redact secret-looking keys. / 递归脱敏疑似密钥的键。

        Args:
            item: Nested value to scan. / 待扫描的嵌套值。

        Returns:
            English: A copy with sensitive values replaced by ``[redacted]``. / 中文：敏感值被替换为
            ``[redacted]`` 的副本。
        """
        if isinstance(item, dict):
            return {
                key: "[redacted]"
                if any(
                    word in key.lower().replace("-", "_")
                    for word in (
                        "password",
                        "token",
                        "api_key",
                        "apikey",
                        "secret",
                        "credential",
                        "authorization",
                        "cookie",
                    )
                )
                else redact(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    if isinstance(value, str):
        try:
            structured = json.loads(value)
        except ValueError:
            structured = None
        text = (
            value
            if not isinstance(structured, (dict, list))
            else json.dumps(redact(structured), ensure_ascii=False, indent=2)
        )
    else:
        text = json.dumps(redact(value), ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + "…"


def run_activity(
    state: dict,
    *,
    stage: str | None = None,
    waiting_for_approval: bool = False,
    tool_progress: dict[str, ToolMessage | None] | None = None,
) -> list[RunActivity]:
    """Derive the public activity timeline of the current run. / 推导当前运行的公开活动时间线。

    The scan starts after the current user message and replays assistant messages, tool calls
    and tool results. ``stage`` reports an in-flight context or model step, and live tool
    progress overrides persisted statuses. / 中文：从本轮用户消息之后开始扫描，重放助手消息、
    工具调用与工具结果；``stage`` 表示进行中的上下文或模型步骤，实时工具进度会覆盖已记录状态。

    Args:
        state: Graph values of the current run. / 当前运行的图状态值。
        stage: Current coarse stage, ``"context"`` or ``"model"``.
            中文：当前粗粒度阶段，``"context"`` 或 ``"model"``。
        waiting_for_approval: Whether pending calls are paused for review.
            中文：待处理调用是否暂停等待审批。
        tool_progress: Live per-call tool results keyed by tool call id.
            中文：按工具调用 ID 索引的实时工具结果。

    Returns:
        English: Ordered activity entries; empty when the current user message is not found.
        / 中文：按序排列的活动条目；找不到本轮用户消息时返回空列表。
    """
    run_id = state.get("run_id")
    messages = state.get("messages", [])
    start = (
        next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].id == f"{run_id}:user"
            ),
            None,
        )
        if run_id
        else None
    )
    if start is None:
        return []
    entries = [RunActivity(id="prepare", kind="prepare", status="done")]
    calls: dict[str, RunActivity] = {}
    replies = 0
    model_finished = False
    current_messages = messages[start + 1 :]
    context_ready = stage == "model" or bool(current_messages) or bool(state.get("response"))
    if context_ready or stage == "context":
        entries.append(
            RunActivity(
                id="context",
                kind="context",
                status="done" if context_ready else "running",
                memory_count=len(state.get("used_memory_ids", [])) if context_ready else None,
            )
        )
    for message in current_messages:
        if isinstance(message, AIMessage):
            if state.get("response_source") == "identity_fact" and (message.id or "").endswith(
                ":identity"
            ):
                model_finished = True
                continue
            replies += 1
            model_finished = not message.tool_calls
            entries.append(RunActivity(id=f"model:{replies}", kind="model", status="done"))
            for index, call in enumerate(message.tool_calls):
                entry = RunActivity(
                    id=f"tool:{replies}:{index}",
                    kind="tool",
                    status="requested",
                    name=call["name"],
                    arguments=preview(call["args"], 4000),
                    risk=state.get("tool_risks", {}).get(call["name"]),
                )
                entries.append(entry)
                if call["id"]:
                    calls[call["id"]] = entry
        elif isinstance(message, ToolMessage) and (entry := calls.get(message.tool_call_id)):
            apply_tool_result(entry, message)
    pending = [entry for entry in entries if entry.status == "requested"]
    if waiting_for_approval:
        for entry in pending:
            entry.status = "approval"
    if (
        stage == "model"
        and not pending
        and not model_finished
        and state.get("response_source") != "identity_fact"
    ):
        entries.append(RunActivity(id=f"model:{replies + 1}", kind="model", status="running"))
    for call_id, result in (tool_progress or {}).items():
        entry = calls.get(call_id)
        if entry and entry.status == "requested":
            if result is None:
                entry.status = "running"
            else:
                apply_tool_result(entry, result)
    return entries


def apply_tool_result(entry: RunActivity, message: ToolMessage) -> None:
    """Apply one tool message to its activity entry. / 把一条工具消息应用到对应活动条目。

    Rejection, error, artifacts, risk, duration and approval flags are taken from the message
    status and ``additional_kwargs``. / 中文：拒绝、错误、产物、风险、耗时与批准标记来自消息
    状态与 ``additional_kwargs``。

    Args:
        entry: Activity entry to update in place. / 就地更新的活动条目。
        message: Tool result message produced by the tool node. / 工具节点产生的工具结果消息。
    """
    result = message.text
    failed = message.status == "error"
    try:
        payload = json.loads(result)
    except ValueError:
        payload = None
    failed = failed or (isinstance(payload, dict) and payload.get("ok") is False)
    try:
        envelope = ToolResult.model_validate(payload)
        entry.artifacts = envelope.artifacts
        entry.error_code = envelope.error.code if envelope.error else None
        entry.summary = envelope.summary
        entry.error_hint = envelope.error.hint if envelope.error else None
    except ValueError:
        pass
    entry.risk = message.additional_kwargs.get("risk", entry.risk)
    entry.duration_ms = (message.additional_kwargs.get("tool_metric") or {}).get("duration_ms")
    entry.status = (
        "rejected"
        if message.additional_kwargs.get("review_rejected")
        else "error"
        if failed
        else "done"
    )
    entry.result = preview(result, 8000)
    entry.approved = message.additional_kwargs.get("review_approved") is True
